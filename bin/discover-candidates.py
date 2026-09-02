#!/usr/bin/env python3
"""Weekly: find new candidate LLMs, pre-flight them, propose the survivors.

    bin/discover-candidates.py --max-preflight 3 --out /tmp/pr-body.md

Chains three pieces that already exist or are trivially thin:

    hf-gguf-candidates.py   what would FIT max-01
    candidates.py           what is worth spending a cold load on
    preflight-candidate.py  whether it actually RUNS, and safely

and then edits models.yaml so a human can merge it. It deliberately stops
there. models.yaml states the hazard:

    Pre-flighting attended is deliberate -- a first-ever load failing at 3am
    is how this GPU gets wedged.

This automates the attended part rather than removing it: everything up to a
one-click merge is done for you, and the thing a person approves is exactly
the thing that has bitten before -- an unknown model entering a 13-hour
unattended sweep.

THE BREAKER IS THE POINT. The moment a pre-flight leaves ollama unhealthy,
the loop stops. Pulling the next unknown model onto a wedged gfx1151 is how
one bad candidate becomes a night of downtime.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


cand = _load("candidates", ROOT / "benchmarks" / "candidates.py")
hf = _load("hf_gguf", ROOT / "bin" / "hf-gguf-candidates.py")
pre = _load("preflight", ROOT / "bin" / "preflight-candidate.py")


def discover(limit: int, min_gb: float, max_gb: float) -> list[dict]:
    rows = []
    for r in hf.search(None, limit):
        repo = r.get("id") or ""
        if not repo:
            continue
        fits = {q: gb for q, gb in hf.quant_sizes(repo).items()
                if min_gb <= gb <= max_gb}
        if not fits:
            continue
        q, gb = hf.best_quant(fits)
        if q not in hf.USABLE:
            # A model that only fits at IQ2 is not a fair test of the model.
            continue
        rows.append({"model": f"hf.co/{repo}:{q}", "repo": repo,
                     "quant": q, "size_gb": round(gb, 1),
                     "downloads": r.get("downloads", 0)})
    rows.sort(key=lambda x: -x["downloads"])
    return rows


def render_pr_body(accepted, rejected, breaker, considered) -> str:
    out = ["## Weekly candidate discovery", "",
           f"Searched HuggingFace, considered **{considered}** repo(s) in the "
           f"size band, pre-flighted **{len(accepted) + len(rejected)}** on "
           f"max-01.", ""]
    if accepted:
        out += ["### Proposed for the sweep", "",
                "| model | params | quant | GB | capabilities |",
                "|---|---|---|---|---|"]
        for c in accepted:
            out.append(f"| `{c['model']}` | {c.get('params') or '?'} | "
                       f"{c.get('quant') or '?'} | {c.get('size_gb', 0):.1f} | "
                       f"{', '.join(c.get('capabilities') or []) or '?'} |")
        out += ["", "Each was pulled, generated at least one token, reported "
                    "its capabilities, and unloaded cleanly. Pre-flight proves "
                    "it RUNS; the sweep is what decides whether it is good.", ""]
    else:
        out += ["### Nothing proposed", "",
                "No candidate cleared pre-flight this week.", ""]
    if rejected:
        out += ["### Rejected (recorded, will not be retried)", ""]
        for r, why in rejected:
            out.append(f"- `{r['model']}` — {'; '.join(r.get('notes') or []) or 'no reason recorded'}")
        out.append("")
    if breaker:
        out += ["### :rotating_light: Pre-flight stopped early", "",
                f"`{breaker[0]['model']}` left ollama unhealthy, so the loop "
                f"stopped rather than pulling another unknown model onto a "
                f"possibly-wedged GPU.", "",
                "```", "\n".join(breaker[0].get("notes") or []), "```", "",
                "**This model is NOT blacklisted** — a wedge is a fact about "
                "the GPU, not proof the model is bad. Check `ollama ps` for a "
                "`Stopping...` entry and restart the deployment if needed.", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--min-gb", type=float, default=10.0)
    ap.add_argument("--max-gb", type=float, default=45.0)
    # Bounded on purpose. Each pre-flight is a cold multi-GB pull plus a real
    # load; an unbounded loop would hold the GPU for hours and collide with
    # whatever else the homelab wanted to do that night.
    ap.add_argument("--max-preflight", type=int, default=3)
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--models-yaml", default=str(ROOT / "models.yaml"))
    ap.add_argument("--ledger", default=str(ROOT / "candidates-ledger.json"))
    ap.add_argument("--out", default=None, help="write the PR body here")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    roster_path, ledger_path = Path(a.models_yaml), Path(a.ledger)
    roster = roster_path.read_text()
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}

    found = discover(a.limit, a.min_gb, a.max_gb)
    fresh = cand.filter_candidates(found, cand.known_models(roster), ledger)
    print(f"discovered={len(found)} new={len(fresh)} "
          f"will_preflight={min(len(fresh), a.max_preflight)}")

    accepted, rejected, breaker, results = [], [], [], []
    for c in fresh[:a.max_preflight]:
        print(f"pre-flighting {c['model']} ...", flush=True)
        if a.dry_run:
            continue
        r = pre.preflight(a.ollama.rstrip("/"), c["model"])
        r.update({k: c[k] for k in ("size_gb", "quant") if k in c})
        verdict = cand.classify_preflight(r)
        print(f"  {verdict}: {'; '.join(r.get('notes') or [])}")
        results.append((r, verdict))
        if cand.should_trip_breaker(verdict):
            breaker.append(r)
            break
        (accepted if verdict == "OK" else rejected).append(
            r if verdict == "OK" else (r, verdict))

    today = datetime.date.today().isoformat()
    if not a.dry_run:
        ledger_path.write_text(
            json.dumps(cand.updated_ledger(ledger, results), indent=2,
                       sort_keys=True) + "\n")
        roster_path.write_text(
            cand.render_roster_addition(roster, accepted, today))
    body = render_pr_body(accepted, rejected, breaker, len(found))
    if a.out:
        Path(a.out).write_text(body)

    # Completion marker: "ran and found nothing" must be distinguishable from
    # "never ran" by anything reading the log from outside.
    print(f"DISCOVERY-COMPLETE considered={len(found)} new={len(fresh)} "
          f"accepted={len(accepted)} rejected={len(rejected)} "
          f"breaker={'yes' if breaker else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
