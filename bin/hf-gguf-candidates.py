#!/usr/bin/env python3
"""Find GGUF models on HuggingFace that would actually run on this homelab.

ollama can pull any GGUF repo directly:

    ollama pull hf.co/<org>/<repo>:<QUANT>

so HuggingFace is a far larger candidate pool than the ollama library. The
two HF models already local (peculiar-ragdoll/Dagger-Qwen3.6-27B-GGUF and
Nail-Qwen3.6-35B-A3B-GGUF) arrived by word of mouth; this makes the search
repeatable.

WHAT IT FILTERS ON, AND WHY

Size is the binding constraint, not popularity. max-01 is a single gfx1151
node with a 105G VRAM envelope that ollama shares with everything else the
homelab runs, so the useful band is roughly 10-45 GB per quant. A 250 GB
BF16 shard set is not a candidate no matter how good the model is.

Sizes are summed PER QUANT DIRECTORY because large repos ship sharded
files (Qwen3.8-Flash-Next-Q8_0-00002-of-00006.gguf); reading any single
shard's size tells you nothing about whether the model fits.

WHAT IT DELIBERATELY DOES NOT CLAIM

  * Tool-calling support. HF metadata does not carry it. Check after
    pulling:  ollama show <model>   and look for `tools` in Capabilities.
  * Renderer/parser. ollama picks those from what the model's own metadata
    declares; a GGUF that declares nothing falls back to its embedded chat
    template (selected=go_template). That is not automatically bad -- an
    own template beat a BORROWED one in the 2026-08-30 sweep -- but it is
    unmeasured, so verify per model:
        kubectl -n ollama logs deploy/ollama | grep 'template selection'
  * Quality. Downloads and likes measure popularity, which is not the same
    thing. That is what the sweep is for; this only decides what is worth
    the disk.

Usage:
    bin/hf-gguf-candidates.py                      # trending, default band
    bin/hf-gguf-candidates.py --search qwen        # filter by name
    bin/hf-gguf-candidates.py --min-gb 8 --max-gb 30
    bin/hf-gguf-candidates.py --limit 60 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

API = "https://huggingface.co/api/models"
UA = {"User-Agent": "homelab-hf-gguf-candidates/1.0"}
# Quant families worth pulling. Anything fp16/bf16 is excluded by size anyway,
# but naming them keeps the report readable rather than listing every shard.
QUANT_RE = re.compile(r"(IQ\d[_A-Z0-9]*|Q\d[_A-Z0-9]*|BF16|F16|F32)", re.I)

# Roughly best-to-worst. Only the ORDER matters: we take the highest-quality
# quant that fits the size budget, because a model squeezed into IQ1_S is not
# a fair test of the model. Anything below Q4 is reported but flagged.
QUANT_RANK = ["F32", "BF16", "F16", "Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S", "Q5_0",
              "Q4_K_M", "Q4_K_S", "Q4_0", "IQ4_XS", "IQ4_NL",
              "Q3_K_L", "Q3_K_M", "Q3_K_S", "IQ3_M", "IQ3_XXS",
              "Q2_K_XL", "Q2_K", "IQ2_M", "IQ2_XS", "IQ1_M", "IQ1_S"]
USABLE = set(QUANT_RANK[:QUANT_RANK.index("IQ4_NL") + 1])


def best_quant(fits: dict[str, float]) -> tuple[str, float]:
    """Highest-ranked quant among those that fit; unknown names sort last."""
    def rank(q: str) -> int:
        try:
            return QUANT_RANK.index(q)
        except ValueError:
            return len(QUANT_RANK)
    q = sorted(fits, key=rank)[0]
    return q, fits[q]


# Safety-stripped variants dominate the download rankings on HuggingFace and
# are actively WRONG for this homelab: the sweep carries family_* payloads and
# homelab#1046 already records that no model in the fleet reliably refuses a
# privileged-container request. A model whose selling point is that refusals
# were removed is not a candidate for a family-facing preset.
#
# Flagged rather than filtered -- they may be legitimate for fiction-writer,
# where a refusal mid-scene is the failure mode. The caller decides; this just
# refuses to present them as neutral.
STRIPPED = ("uncensored", "abliterated", "obliterated", "heretic", "unleashed",
            "unaligned", "unfiltered", "defiant", "nsfw", "unsafe", "jailbreak")


def is_safety_stripped(repo: str) -> bool:
    low = repo.lower()
    return any(t in low for t in STRIPPED)


def _get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def search(term: str | None, limit: int) -> list[dict]:
    q = {"filter": "gguf", "sort": "trendingScore", "direction": "-1",
         "limit": str(limit)}
    if term:
        q["search"] = term
    return _get(f"{API}?{urllib.parse.urlencode(q)}")


def quant_sizes(repo: str) -> dict[str, float]:
    """GB per quant, summed across shards. {} if the repo exposes no sizes."""
    try:
        d = _get(f"{API}/{repo}?blobs=true")
    except Exception:
        return {}
    out: dict[str, float] = {}
    for s in d.get("siblings") or []:
        name = s.get("rfilename", "")
        if not name.lower().endswith(".gguf"):
            continue
        size = s.get("size") or 0
        # Prefer the directory name (Q4_K_M/foo-00001-of-00003.gguf); fall
        # back to matching inside the filename for flat repos.
        head = name.split("/")[0] if "/" in name else ""
        m = QUANT_RE.fullmatch(head) or QUANT_RE.search(name)
        if not m:
            continue
        out[m.group(1).upper()] = out.get(m.group(1).upper(), 0.0) + size / 1e9
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search", default=None, help="name filter, e.g. 'qwen'")
    ap.add_argument("--limit", type=int, default=40, help="repos to examine")
    ap.add_argument("--min-gb", type=float, default=10.0)
    ap.add_argument("--max-gb", type=float, default=45.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        repos = search(args.search, args.limit)
    except Exception as e:
        print(f"HuggingFace search failed: {e}", file=sys.stderr)
        return 2

    rows = []
    for r in repos:
        repo = r.get("modelId") or r.get("id")
        if not repo:
            continue
        sizes = quant_sizes(repo)
        fits = {q: gb for q, gb in sizes.items()
                if args.min_gb <= gb <= args.max_gb}
        if not fits:
            continue
        # BEST quant that fits the budget, not the smallest.
        #
        # Picking the smallest was the first version and it was wrong: it
        # surfaced IQ1_S, IQ2_M and Q2_K rows, which are heavily degraded
        # and tell you nothing about whether the MODEL is any good. If a
        # 27B only fits at IQ2, the honest answer is that it does not fit.
        q, gb = best_quant(fits)
        rows.append({"repo": repo, "quant": q, "gb": round(gb, 1),
                     "usable_quant": q in USABLE,
                     "safety_stripped": is_safety_stripped(repo),
                     "downloads": r.get("downloads", 0),
                     "likes": r.get("likes", 0),
                     "pull": f"hf.co/{repo}:{q}"})

    rows.sort(key=lambda x: -x["downloads"])
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No repos matched. Widen --min-gb/--max-gb or --limit.")
        return 1
    print(f"{'repo':<52} {'quant':<10} {'GB':>6} {'downloads':>11}  note")
    for x in rows:
        note = "" if x["usable_quant"] else "  <- only fits at a degraded quant"
        if x["safety_stripped"]:
            note += "  [safety-stripped]"
        print(f"{x['repo'][:52]:<52} {x['quant']:<10} {x['gb']:6.1f} "
              f"{x['downloads']:>11,}{note}")
    n_str = sum(1 for x in rows if x["safety_stripped"])
    print(f"\n{len(rows)} candidates in the {args.min_gb:g}-{args.max_gb:g} GB band"
          + (f"; {n_str} are [safety-stripped] and are NOT suitable for a "
             f"family-facing preset." if n_str else "."))
    print("Pull with:  ollama pull <pull-target>   then verify tools support:")
    print("  ollama show <model> | sed -n '/Capabilities/,/^$/p'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
