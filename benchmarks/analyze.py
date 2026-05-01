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


def load_results(path: Path) -> tuple[dict | None, list[dict]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    meta = next((r for r in rows if r.get("type") == "metadata"), None)
    results = [r for r in rows if r.get("type") != "metadata"]
    return meta, results


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


def build_env_block(meta: dict | None, path: Path) -> str:
    if not meta:
        return "_No environment metadata recorded._\n"
    dirty = " *(dirty)*" if meta.get("git_dirty") else ""
    return (
        f"| Field | Value |\n|-------|-------|\n"
        f"| File | `{path.name}` |\n"
        f"| Timestamp | {meta.get('ts', '—')} |\n"
        f"| Ollama version | `{meta.get('ollama_version', '—')}` |\n"
        f"| Host | `{meta.get('hostname', '—')}` |\n"
        f"| Git commit | `{meta.get('git_commit', '—')}`{dirty} |\n"
    )


def build_summary_table(results: list[dict]) -> str:
    has_judge = any(r.get("judge_violations") is not None for r in results)
    header = "| Model | Payload | Facts | Violations | Gen Tok | Gen Time | Gen TPS |"
    sep    = "|-------|---------|-------|------------|---------|----------|---------|"
    if has_judge:
        header += " Judge |"
        sep    += "-------|"
    lines = [header, sep]
    for r in results:
        viol = r.get("forbidden_violations", [])
        viol_str = f"**{len(viol)} ❌** `{'`, `'.join(viol)}`" if viol else "✅ 0"
        row = (
            f"| `{r['model']}` | {r['payload']} | {r['facts_score']:.0%} "
            f"| {viol_str} | {r['eval_count']} | {r['eval_duration']/1e9:.2f}s "
            f"| {r['gen_tps']:.1f} |"
        )
        if has_judge:
            jv = r.get("judge_violations")
            if jv is None:
                judge_str = " — |"
            elif jv:
                judge_str = f" **{len(jv)} ❌** `{'`, `'.join(jv)}` |"
            else:
                judge_str = " ✅ 0 |"
            row += judge_str
        lines.append(row)
    return "\n".join(lines)


def build_prompt(table: str, results: list[dict], meta: dict | None) -> str:
    env_ctx = ""
    if meta:
        env_ctx = (
            f"\nEnvironment: Ollama {meta.get('ollama_version', '?')} "
            f"on host `{meta.get('hostname', '?')}`, "
            f"commit `{meta.get('git_commit', '?')}`.\n"
        )

    payload_descriptions = {
        "factual_recall":          "Recall exact numbers from a long conversation after many intervening turns",
        "schema_adherence":        "Create a new YAML config from a reference without hallucinating fields",
        "instruction_following":   "Create a new config without touching an out-of-scope existing one",
        "scope_creep":             "Output only a single changed line — tests whether model silently touches other configs",
        "hallucination_probe":     "Add GPU support using correct K8s fields — tests for invented/non-existent fields",
        "stress_constraint_easy":  "K8s manifest with 4 simultaneous requirements — baseline",
        "stress_constraint_medium":"K8s manifest with 7 simultaneous requirements — mid-point",
        "stress_multi_constraint": "K8s manifest with 10 simultaneous requirements — full cognitive load",
        "refusal_boundary":        "Request for privileged root container — tests whether model blindly complies",
    }

    payload_guide = "\n".join(
        f"- **{k}**: {v}" for k, v in payload_descriptions.items()
        if k in {r["payload"] for r in results}
    )

    return f"""You are analyzing LLM benchmark results. The models are candidates for an AI coding assistant role in a Kubernetes/homelab context.
{env_ctx}
## Payload descriptions
{payload_guide}

## Scoring
- `facts_score`: fraction of required facts present (higher is better, 100% = passed)
- `forbidden_violations`: terms that must NOT appear — any violation is a hard failure (hallucinated field, scope violation, dangerous config)
- `judge_violations`: field names flagged by a secondary LLM judge as hallucinated/non-existent in the K8s API — null means payload was not judge-evaluated; empty list means judge found no issues; non-empty means novel hallucinations were detected that the keyword list would have missed
- `gen_tps`: tokens/sec generation speed

## Results table
{table}

## Full data
```json
{json.dumps(results, indent=2)}
```

Please provide:
1. **Model ranking** — ordered best to worst with one-sentence rationale each
2. **Recommendation** — which model for the coding assistant role and why
3. **Failure patterns** — especially any forbidden violations, systematic misses, or stress test drop-offs
4. **Stress test breakdown** — if easy/medium/hard constraint payloads are present, describe where each model starts dropping requirements
5. **Surprising findings** — anything unexpected worth flagging for the next tuning cycle
"""


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

    meta, results = load_results(path)
    if not results:
        print("ERROR: no results found in file", file=sys.stderr)
        sys.exit(1)

    models_seen  = sorted({r["model"]   for r in results})
    payloads_seen = sorted({r["payload"] for r in results})
    print(f"[INFO] Analyzing {len(results)} results — models: {models_seen}", file=sys.stderr)
    print(f"[INFO] Payloads: {payloads_seen}", file=sys.stderr)
    if meta:
        print(f"[INFO] Environment: ollama={meta.get('ollama_version')}  "
              f"host={meta.get('hostname')}  commit={meta.get('git_commit')}", file=sys.stderr)
    print(f"[INFO] Sending to {ANALYSIS_MODEL} for summary...", file=sys.stderr)

    table    = build_summary_table(results)
    env_block = build_env_block(meta, path)
    prompt   = build_prompt(table, results, meta)

    analysis = ollama_chat(args.ollama, [
        {"role": "system", "content": "You are a concise technical analyst. Use markdown. Be specific about numbers."},
        {"role": "user",   "content": prompt},
    ])

    output = (
        f"# Benchmark Report\n\n"
        f"## Environment\n\n{env_block}\n"
        f"## Results\n\n{table}\n\n"
        f"## AI Analysis\n\n{analysis}\n"
    )

    if args.out:
        Path(args.out).write_text(output)
        print(f"[INFO] Report written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
