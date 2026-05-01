#!/usr/bin/env python3
"""
AI-powered analysis of benchmark results using qwen3.6:35b.

Usage:
    python benchmarks/analyze.py results/sweep_20260430T120000Z.jsonl
    python benchmarks/analyze.py results/sweep_20260430T120000Z.jsonl --out report.md
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

DEFAULT_OLLAMA = "http://localhost:11434"
ANALYSIS_MODEL = "qwen3.6:35b"
HTTP_TIMEOUT = 300


def load_results(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def ollama_chat(ollama_url: str, messages: list[dict]) -> str:
    payload = {
        "model": ANALYSIS_MODEL,
        "stream": False,
        "options": {"num_gpu": -1},
        "messages": messages,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{ollama_url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        resp = json.loads(r.read())
    return strip_think(resp.get("message", {}).get("content", ""))


def build_summary_table(results: list[dict]) -> str:
    lines = ["| Model | Payload | Facts | Violations | Gen Tok | Gen Time | Gen TPS |",
             "|-------|---------|-------|------------|---------|----------|---------|"]
    for r in results:
        viol = len(r.get("forbidden_violations", []))
        viol_str = f"**{viol} ❌**" if viol else "0"
        lines.append(
            f"| {r['model']} | {r['payload']} | {r['facts_score']:.0%} "
            f"| {viol_str} | {r['eval_count']} | {r['eval_duration']/1e9:.2f}s "
            f"| {r['gen_tps']:.1f} |"
        )
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Analyze benchmark results with AI")
    p.add_argument("results_file",          help="Path to sweep JSONL file")
    p.add_argument("--ollama", default=DEFAULT_OLLAMA, help="Ollama base URL")
    p.add_argument("--out",    default=None,           help="Output markdown file (default: stdout)")
    args = p.parse_args()

    path = Path(args.results_file)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)

    results = load_results(path)
    if not results:
        print("ERROR: no results found in file", file=sys.stderr)
        sys.exit(1)

    table = build_summary_table(results)
    raw_json = json.dumps(results, indent=2)

    prompt = f"""You are analyzing LLM benchmark results. The models are being evaluated as candidates for an AI coding assistant in a Kubernetes/homelab context.

The benchmarks test three things:
- **schema_adherence**: Given a reference YAML config, create a new one without hallucinating fields
- **instruction_following**: Given an existing config (do not touch it), create a new one — scope discipline test
- **factual_recall**: Recall exact numbers from a long conversation after many intervening turns

Scoring:
- `facts_score`: fraction of required facts present in the response (higher is better)
- `forbidden_violations`: terms that must NOT appear — any violation is a hard failure
- `gen_tps`: generation tokens per second (higher is better for latency)

Here are the results:

{table}

Full result data:
```json
{raw_json}
```

Please provide:
1. A ranking of the models with a brief rationale for each
2. Which model you recommend for the coding assistant role and why
3. Any notable failure patterns (especially forbidden violations or systematic misses)
4. Any surprising findings worth flagging
"""

    models_seen = sorted({r["model"] for r in results})
    payloads_seen = sorted({r["payload"] for r in results})
    print(f"[INFO] Analyzing {len(results)} results — models: {models_seen}", file=sys.stderr)
    print(f"[INFO] Payloads: {payloads_seen}", file=sys.stderr)
    print(f"[INFO] Sending to {ANALYSIS_MODEL} for summary...", file=sys.stderr)
    analysis = ollama_chat(args.ollama, [
        {"role": "system", "content": "You are a concise technical analyst. Use markdown. Be specific about numbers."},
        {"role": "user", "content": prompt},
    ])

    output = f"# Benchmark Analysis\n\n## Results Table\n\n{table}\n\n## AI Analysis\n\n{analysis}\n"

    if args.out:
        Path(args.out).write_text(output)
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
