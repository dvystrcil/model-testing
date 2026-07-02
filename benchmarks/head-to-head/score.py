#!/usr/bin/env python3
"""Head-to-head scorer + report generator (model-testing#20 AC6/AC7).

Reads the JSONL from run.py, computes the auto-checkable dimensions
deterministically, prompts the operator for the human-judgment dimensions, then
writes the aggregated report with a variance section.

Auto dimensions (deterministic, also used as a first-pass the operator can
override): latency, token_io, format, memory (tag references), voice
(celebratory/restraint screen).
Human dimensions (prompted, or injected for tests): correctness, faithfulness,
tool_coherence.

The scoring functions are import-safe and side-effect-free so tests/ can drive
them with synthetic outputs (the 5 TDD scenarios in #20).
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

MAX = 3  # 0-3 scale

# Which dimensions apply to each task (from the payload headers).
AUTO = {"latency", "token_io", "format", "memory", "voice"}
HUMAN = {"correctness", "faithfulness", "tool_coherence"}

TASK_DIMS = {
    "01-issue-scoping": {"correctness", "faithfulness", "format", "memory"},
    "02-bug-from-trace": {"correctness", "faithfulness", "format"},
    "03-skill-invocation": {"correctness", "faithfulness", "memory", "tool_coherence"},
    "04-manifest-authoring": {"correctness", "faithfulness", "format", "memory"},
    "05-multi-step-refactor": {"correctness", "tool_coherence", "format"},
    "06-tdd-design": {"correctness", "faithfulness", "format", "memory"},
    "07-pr-review": {"correctness", "faithfulness", "format"},
    "08-arch-decision": {"correctness", "faithfulness", "memory", "voice"},
    "09-cross-repo-linking": {"correctness", "faithfulness", "memory"},
    "10-issue-editing": {"correctness", "faithfulness", "format", "voice"},
    "11-commit-message": {"correctness", "format", "voice"},
    "12-board-velocity": {"correctness", "faithfulness", "format"},
    "13-memory-qa": {"correctness", "faithfulness", "memory"},
    "14-verification-design": {"correctness", "faithfulness", "memory", "voice"},
    "15-rule-distillation": {"correctness", "format", "voice"},
    "16-diary-entry": {"correctness", "faithfulness", "voice"},
    "17-ac-closing": {"correctness", "faithfulness", "format"},
    "18-doc-minimal-update": {"correctness", "faithfulness", "format", "voice"},
}

# Memory-only marker strings: present only if the injected memory was used.
MEMORY_MARKERS = {
    "01-issue-scoping": ["infisicalsecret", "pipeline", "filter"],
    "03-skill-invocation": ["pgo-pre-upgrade-backup", "pgbackrest", "postgrescluster"],
    "04-manifest-authoring": ["infisicalsecret", "imageupdater"],
    "06-tdd-design": ["before", "after", "diff"],
    "08-arch-decision": ["memory_loader_postgres", "inlet", "filter"],
    "09-cross-repo-linking": ["research", "292", "kubectl explain"],
    "13-memory-qa": ["amdgpu.noretry=0", "scaled to 0", "segfault"],
    "14-verification-design": ["memory_loader_postgres", "inlet"],
}

# Celebratory / performed-emotion markers that should drop voice.
CELEBRATORY = ["🎉", "🚀", "🔥", "💪", "great progress", "amazing", "awesome",
               "so proud", "i'm proud", "excited to", "nailed it", "crushed it"]


def _yaml_ok(text: str) -> bool:
    block = _code_block(text, "yaml") or text
    try:
        import yaml
        list(yaml.safe_load_all(block))
        return True
    except Exception:
        return False


def _code_block(text: str, lang: str = "") -> str:
    m = re.search(rf"```{lang}[^\n]*\n(.*?)```", text, re.S)
    return m.group(1) if m else ""


# --- format checks: return True (pass) / False (fail) per structured task ------

def _fmt_issue_scoping(t):
    tl = t.lower()
    return ("## background" in tl and ("## pieces" in tl or "piece" in tl)
            and "acceptance criteria" in tl and t.count("- [ ]") >= 3)


def _fmt_bug(t):
    return ("memory_store.py" in t and "PG_PASSWORD" in t and len(t) < 1200)


def _fmt_manifest(t):
    tl = t.lower()
    return ("kind: cronjob" in tl and "schedule:" in tl
            and "requests" in tl and "limits" in tl and _yaml_ok(t))


def _fmt_refactor(t):
    return bool(re.search(r"passed|\bok\b|pytest|assert", t, re.I))


def _fmt_tdd(t):
    return len(re.findall(r"==|expected", t, re.I)) >= 6


def _fmt_review(t):
    return bool(re.search(r"request[- ]changes|approve", t, re.I))


def _fmt_issue_edit(t):
    return "## background" in t.lower() and "acceptance criteria" in t.lower()


def _fmt_commit(t):
    first = t.strip().splitlines()[0] if t.strip() else ""
    # imperative title <=72 chars, and not a per-line bullet dump
    bulletty = t.count("\n- ") + t.count("\n* ") > 4
    return len(first) <= 72 and not bulletty


def _fmt_velocity(t):
    return t.count("|") >= 6 and bool(re.search(r"clos|open", t, re.I))


def _fmt_rule(t):
    tl = t.lower()
    return "why" in tl and "how to apply" in tl


def _fmt_ac_table(t):
    rows = re.findall(r"^\|.*\|$", t, re.M)
    has_status = bool(re.search(r"✅|❌|⏳", t))
    return len(rows) >= 5 and has_status


def _fmt_doc(t):
    tl = t.lower()
    return "analyze.py" in tl and "run-sweep" in tl  # full README returned


FORMAT_CHECKS = {
    "01-issue-scoping": _fmt_issue_scoping,
    "02-bug-from-trace": _fmt_bug,
    "04-manifest-authoring": _fmt_manifest,
    "05-multi-step-refactor": _fmt_refactor,
    "06-tdd-design": _fmt_tdd,
    "07-pr-review": _fmt_review,
    "10-issue-editing": _fmt_issue_edit,
    "11-commit-message": _fmt_commit,
    "12-board-velocity": _fmt_velocity,
    "15-rule-distillation": _fmt_rule,
    "17-ac-closing": _fmt_ac_table,
    "18-doc-minimal-update": _fmt_doc,
}


def auto_scores(row: dict) -> dict:
    """Deterministic scores derivable from the row alone."""
    task = row["task_id"]
    out = row["raw_output"]
    dims = TASK_DIMS.get(task, set())
    scores = {}
    # latency / token_io are recorded raw, not 0-3 -- carried for the report.
    scores["latency"] = row.get("latency_seconds")
    scores["token_io"] = row.get("input_tokens", 0) + row.get("output_tokens", 0)
    if "format" in dims:
        check = FORMAT_CHECKS.get(task)
        scores["format"] = MAX if (check and check(out)) else 0
    if "memory" in dims:
        markers = MEMORY_MARKERS.get(task, [])
        hit = sum(1 for m in markers if m.lower() in out.lower())
        scores["memory"] = MAX if hit >= 2 else (1 if hit == 1 else 0)
    if "voice" in dims:
        low = out.lower()
        celebratory = any(c.lower() in low for c in CELEBRATORY)
        scores["voice"] = 0 if celebratory else MAX
    return scores


def human_dims_for(task: str) -> list:
    return sorted(d for d in TASK_DIMS.get(task, set()) if d in HUMAN)


def collect_human(row: dict, provider) -> dict:
    """provider(task_id, dim) -> int. Interactive by default; injected in tests."""
    return {d: int(provider(row["task_id"], d)) for d in human_dims_for(row["task_id"])}


def _interactive_provider(task, dim):
    while True:
        raw = input(f"  {task} / {dim} (0-3): ").strip()
        if raw in {"0", "1", "2", "3"}:
            return int(raw)
        print("    enter 0, 1, 2, or 3")


def _row_key(r):
    return (r["task_id"], r["side"], r["run_n"])


def score_rows(rows, provider=_interactive_provider, prior=None, on_scored=None):
    """Score each row. `prior` (key -> scores) skips already-done rows so an
    interrupted interactive session resumes; `on_scored` persists each newly
    scored row immediately so nothing is lost if the process dies mid-session."""
    prior = prior or {}
    for row in rows:
        key = _row_key(row)
        if key in prior:
            row["scores"] = prior[key]
            continue
        row["scores"] = {**auto_scores(row), **collect_human(row, provider)}
        if on_scored:
            on_scored(row)
    return rows


# --- aggregation ---------------------------------------------------------------

QUAL_DIMS = ["correctness", "faithfulness", "format", "memory", "voice", "tool_coherence"]


def aggregate(rows):
    per_task = {}   # (task, side) -> {dim: mean}
    variance = []   # (task, side, dim) where runs disagree by > 1
    by_key = {}
    for r in rows:
        by_key.setdefault((r["task_id"], r["side"]), []).append(r)

    for (task, side), rs in by_key.items():
        agg = {}
        for dim in QUAL_DIMS + ["latency", "token_io"]:
            vals = [r["scores"].get(dim) for r in rs if r["scores"].get(dim) is not None]
            if not vals:
                continue
            agg[dim] = round(statistics.mean(vals), 2)
            if dim in QUAL_DIMS and len(vals) > 1 and (max(vals) - min(vals)) > 1:
                variance.append((task, side, dim, min(vals), max(vals)))
        per_task[(task, side)] = agg

    per_side = {}
    for side in {s for _, s in per_task}:
        rows_for_side = [a for (t, s), a in per_task.items() if s == side]
        per_side[side] = {
            dim: round(statistics.mean([a[dim] for a in rows_for_side if dim in a]), 2)
            for dim in QUAL_DIMS + ["latency", "token_io"]
            if any(dim in a for a in rows_for_side)
        }
    return {"per_task": per_task, "per_side": per_side, "variance": variance}


BIASES = """\
- Claude Code's auto-memory has been tuned for months; opencode->OWUI is younger. Maturity asymmetry.
- Some skills' guidance text was written assuming Claude Code's idiom; may favor Side A.
- Operator familiarity with Claude's style may color "looks good" judgments. Rubrics mitigate; don't eliminate.
- Tasks 09, 10, 11, 12, 16 are especially Claude-Code-style-sensitive; read with extra skepticism."""


def write_report(agg, out_path: Path, provenance: str = ""):
    lines = ["# Head-to-head report", "",
             "Claude Code (Side A) vs opencode->OWUI qwen3-coder-next (Side B).",
             "Feeds homelab#292 A3 / open-webui#100.", ""]
    if provenance:
        lines += [provenance, ""]
    lines += ["## Per-side aggregate (0-3, higher better; latency/token_io raw)", ""]
    dims = QUAL_DIMS + ["latency", "token_io"]
    lines.append("| Side | " + " | ".join(dims) + " |")
    lines.append("|" + "---|" * (len(dims) + 1))
    for side, a in sorted(agg["per_side"].items()):
        lines.append("| " + side + " | " + " | ".join(str(a.get(d, "-")) for d in dims) + " |")

    lines += ["", "## Per-task", ""]
    lines.append("| Task | Side | " + " | ".join(dims) + " |")
    lines.append("|" + "---|" * (len(dims) + 2))
    for (task, side) in sorted(agg["per_task"]):
        a = agg["per_task"][(task, side)]
        lines.append(f"| {task} | {side} | " + " | ".join(str(a.get(d, "-")) for d in dims) + " |")

    lines += ["", "## Variance (runs disagree by > 1 point)", ""]
    if agg["variance"]:
        for task, side, dim, lo, hi in agg["variance"]:
            lines.append(f"- {task} / {side} / {dim}: {lo}..{hi}")
    else:
        lines.append("_none_")

    lines += ["", "## Per-cluster gap analysis", "",
              "_Operator-written after reading the data — one paragraph each._", ""]
    for cluster in ["Code & cluster", "Planning & meta",
                    "Memory & verification", "Reflective & docs"]:
        lines += [f"### {cluster}", "", "_TBD_", ""]

    lines += ["## Known biases (AC8)", "", BIASES, "",
              "## Honest discussion", "",
              "_Operator-written after reading the data: where is the gap large? "
              "where is Side B good enough? what would close it (larger model, "
              "better prompt, more skills, RAG)?_", ""]
    out_path.write_text("\n".join(lines))
    return out_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Head-to-head scorer (model-testing#20)")
    ap.add_argument("--in", dest="inp", type=Path, required=True, help="JSONL from run.py")
    ap.add_argument("--out", type=Path, default=None, help="report .md path")
    ap.add_argument("--human-scores", type=Path, default=None,
                    help="JSON {task_id: {dim: score}} to inject human dims non-interactively")
    args = ap.parse_args(argv)

    rows = [json.loads(l) for l in args.inp.read_text().splitlines() if l.strip()]

    if args.human_scores:
        table = json.loads(args.human_scores.read_text())
        provider = lambda task, dim: table.get(task, {}).get(dim, 0)
    else:
        provider = _interactive_provider

    # Resume + crash-safe persistence: prior scores are reloaded, new ones are
    # appended as they're entered so a long interactive session survives a crash.
    scored_path = args.inp.with_suffix(".scored.jsonl")
    prior = {}
    if scored_path.exists():
        for line in scored_path.read_text().splitlines():
            if line.strip():
                pr = json.loads(line)
                prior[_row_key(pr)] = pr["scores"]
        if prior:
            print(f"resuming: {len(prior)} rows already scored in {scored_path.name}")

    with scored_path.open("a") as sink_f:
        def on_scored(row):
            sink_f.write(json.dumps(row) + "\n")
            sink_f.flush()
        score_rows(rows, provider, prior=prior, on_scored=on_scored)

    agg = aggregate(rows)
    out = args.out or args.inp.with_suffix(".report.md")
    write_report(agg, out)
    print(f"wrote report -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
