#!/usr/bin/env python3
"""
DPO dataset generator for K8s constraint alignment.

Reads benchmark payload JSONs and result JSONLs, outputs preference pairs
in the format expected by trl.DPOTrainer.

Two modes:
  --mode real      Use actual model responses (requires --save-responses in run_benchmark.py)
  --mode synthetic  Generate rejected pairs by removing target fields from chosen responses

Usage:
    # Synthetic pairs for resource dropout (Finding 2)
    python training/generate_dpo_dataset.py \
        --payloads benchmarks/payloads/stress_constraint_hard.json \
        --results benchmarks/results/sweep_*.jsonl \
        --out training/data/k8s_resources_dpo.jsonl \
        --mode synthetic

    # Real pairs from saved responses
    python training/generate_dpo_dataset.py \
        --payloads benchmarks/payloads/stress_constraint_hard.json \
        --results benchmarks/results/sweep_with_responses_*.jsonl \
        --out training/data/k8s_resources_dpo.jsonl \
        --mode real
"""

import argparse
import json
import re
import sys
from pathlib import Path


# Fields that are consistently dropped at hard/extreme stress levels (Finding 2).
# Used as synthetic rejection targets when --mode synthetic is set.
DEFAULT_TARGET_MISSES = [
    'memory: "512Mi"',
    'memory: "256Mi"',
]


def load_payload(path: Path) -> dict:
    return json.loads(path.read_text())


def load_results(paths: list[Path]) -> list[dict]:
    records = []
    for path in paths:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "metadata":
                continue
            if r.get("type") == "agentic":
                continue
            records.append(r)
    return records


def build_prompt(payload_spec: dict) -> list[dict]:
    """Extract the messages list from a payload JSON (system + user roles)."""
    return payload_spec["payload"]["messages"]


def make_rejected_synthetic(chosen: str, target_misses: list[str]) -> str | None:
    """
    Build a rejected variant by removing target_misses fields from a chosen response.
    Operates on raw YAML text — strips the line(s) containing each target field.
    Returns None if chosen does not contain all target fields (can't make a meaningful pair).
    """
    low = chosen.lower()
    for field in target_misses:
        if field.lower() not in low:
            return None

    rejected = chosen
    for field in target_misses:
        # Match the line(s) containing the target field (case-insensitive, preserve surrounding)
        pattern = re.compile(r'^.*' + re.escape(field) + r'.*$\n?', re.MULTILINE | re.IGNORECASE)
        rejected = pattern.sub('', rejected)

    return rejected.strip() if rejected.strip() != chosen.strip() else None


def generate_synthetic(payload_spec: dict, results: list[dict], target_misses: list[str]) -> list[dict]:
    """
    Mode: synthetic.
    Find chosen responses (zero misses) from results, then manufacture rejected
    variants by removing the target fields.
    """
    name = payload_spec["name"]
    prompt = build_prompt(payload_spec)
    pairs = []

    for r in results:
        if r.get("payload") != name:
            continue
        if r.get("quality_misses"):
            continue
        if r.get("forbidden_violations"):
            continue

        chosen = r.get("response_text") or r.get("response_snippet", "")
        if not chosen:
            continue

        rejected = make_rejected_synthetic(chosen, target_misses)
        if rejected is None:
            continue

        pairs.append({
            "prompt": prompt,
            "chosen": chosen,
            "rejected": rejected,
            "meta": {
                "payload": name,
                "model": r.get("model"),
                "mode": "synthetic",
                "target_misses": target_misses,
            },
        })

    return pairs


def generate_real(payload_spec: dict, results: list[dict], target_misses: list[str]) -> list[dict]:
    """
    Mode: real.
    Match chosen (zero misses) and rejected (target_misses dropped) responses
    from actual model runs. Requires response_text to be present in JSONL.
    """
    name = payload_spec["name"]
    prompt = build_prompt(payload_spec)

    chosen_pool = []
    rejected_pool = []

    for r in results:
        if r.get("payload") != name:
            continue

        text = r.get("response_text")
        if not text:
            print(f"  [skip] {r.get('model')} run has no response_text — re-run with --save-responses", file=sys.stderr)
            continue

        misses = set(r.get("quality_misses", []))
        violations = r.get("forbidden_violations", [])
        target_set = set(target_misses)

        if not misses and not violations:
            chosen_pool.append((r, text))
        elif misses and misses.issubset(target_set) and not violations:
            rejected_pool.append((r, text))

    pairs = []
    for (c_meta, chosen), (r_meta, rejected) in zip(chosen_pool, rejected_pool):
        pairs.append({
            "prompt": prompt,
            "chosen": chosen,
            "rejected": rejected,
            "meta": {
                "payload": name,
                "chosen_model": c_meta.get("model"),
                "rejected_model": r_meta.get("model"),
                "mode": "real",
                "target_misses": target_misses,
            },
        })

    return pairs


def main():
    p = argparse.ArgumentParser(description="Generate DPO preference pairs from benchmark results")
    p.add_argument("--payloads", nargs="+", required=True,
                   help="Payload JSON files (e.g. benchmarks/payloads/stress_constraint_hard.json)")
    p.add_argument("--results", nargs="+", required=True,
                   help="Result JSONL files (benchmarks/results/sweep_*.jsonl)")
    p.add_argument("--out", required=True, help="Output JSONL file path")
    p.add_argument("--mode", choices=["synthetic", "real"], default="synthetic",
                   help="synthetic: remove fields from chosen; real: use actual model fail responses")
    p.add_argument("--target-misses", nargs="+", default=DEFAULT_TARGET_MISSES,
                   help="Fields to use as rejection targets (default: memory resource fields)")
    args = p.parse_args()

    payload_paths = [Path(p) for p in args.payloads]
    result_paths = [Path(r) for r in args.results]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = load_results(result_paths)
    print(f"Loaded {len(results)} result records from {len(result_paths)} file(s)")
    print(f"Target misses: {args.target_misses}")
    print(f"Mode: {args.mode}")

    all_pairs = []
    for pp in payload_paths:
        spec = load_payload(pp)
        name = spec["name"]

        if args.mode == "synthetic":
            pairs = generate_synthetic(spec, results, args.target_misses)
        else:
            pairs = generate_real(spec, results, args.target_misses)

        print(f"  {name}: {len(pairs)} pair(s)")
        all_pairs.extend(pairs)

    if not all_pairs:
        print("WARNING: no pairs generated. Check that result JSONLs match payload names and contain response_text (real mode) or zero-miss runs (synthetic mode).", file=sys.stderr)
        sys.exit(1)

    with open(out_path, "w") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair) + "\n")

    print(f"\nWrote {len(all_pairs)} DPO pair(s) to {out_path}")

    avg_chosen = sum(len(p["chosen"]) for p in all_pairs) / len(all_pairs)
    avg_rejected = sum(len(p["rejected"]) for p in all_pairs) / len(all_pairs)
    print(f"Avg chosen length:   {avg_chosen:.0f} chars")
    print(f"Avg rejected length: {avg_rejected:.0f} chars")


if __name__ == "__main__":
    main()
