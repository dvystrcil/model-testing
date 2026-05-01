#!/usr/bin/env python3
"""
Model sweep benchmark runner.

Usage:
    python benchmarks/run_benchmark.py
    python benchmarks/run_benchmark.py --model gemma4:26b
    python benchmarks/run_benchmark.py --payload schema_adherence --runs 3
    python benchmarks/run_benchmark.py --dry-run
"""

import argparse
import datetime
import json
import math
import re
import sys
import urllib.request
from pathlib import Path

import yaml

PAYLOADS_DIR = Path(__file__).parent / "payloads"
RESULTS_DIR = Path(__file__).parent / "results"
MODELS_FILE = Path(__file__).parent.parent / "models.yaml"
DEFAULT_OLLAMA = "http://localhost:11434"
HTTP_TIMEOUT = 300


def load_models(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text())
    return [m["name"] for m in data["models"]]


def load_payloads(name: str) -> list[dict]:
    if name == "all":
        files = sorted(PAYLOADS_DIR.glob("*.json"))
    else:
        files = list(PAYLOADS_DIR.glob(f"{name}.json"))
        if not files:
            print(f"ERROR: No payload file matching '{name}' in {PAYLOADS_DIR}", file=sys.stderr)
            sys.exit(1)
    return [json.loads(f.read_text()) for f in files]


def ollama_chat(ollama_url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{ollama_url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def grade(response_text: str, facts: list[str], forbidden: list[str]) -> dict:
    low = response_text.lower()
    hits = [f for f in facts if f.lower() in low]
    misses = [f for f in facts if f.lower() not in low]
    violations = [f for f in forbidden if f.lower() in low]
    facts_score = len(hits) / len(facts) if facts else 1.0
    return {
        "facts_score": facts_score,
        "hits": hits,
        "misses": misses,
        "violations": violations,
    }


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((x - mean) ** 2 for x in values) / (len(values) - 1))


def run_once(spec: dict, ollama_url: str, model: str) -> dict | None:
    payload = json.loads(json.dumps(spec["payload"]))
    payload["model"] = model
    payload["stream"] = False
    payload.setdefault("options", {})["num_gpu"] = -1

    try:
        resp = ollama_chat(ollama_url, payload)
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return None

    content = strip_think(resp.get("message", {}).get("content", ""))
    facts = spec.get("quality_facts", [])
    forbidden = spec.get("quality_forbidden", [])
    g = grade(content, facts, forbidden)

    return {
        "pec": resp.get("prompt_eval_count", 0),
        "ped": resp.get("prompt_eval_duration", 0),
        "ec":  resp.get("eval_count", 0),
        "ed":  resp.get("eval_duration", 0),
        "td":  resp.get("total_duration", 0),
        "content": content,
        **g,
    }


def run_one(spec: dict, ollama_url: str, model: str, runs: int,
            snippet_len: int, dry_run: bool) -> dict | None:
    name = spec["name"]
    facts = spec.get("quality_facts", [])
    forbidden = spec.get("quality_forbidden", [])

    msg_count = len(spec["payload"].get("messages", []))
    approx_tokens = sum(
        len(str(m.get("content", ""))) // 4
        for m in spec["payload"].get("messages", [])
    )

    print(f"\n  payload={name}  msgs={msg_count}  ~{approx_tokens} tokens  model={model}  runs={runs}")
    if forbidden:
        print(f"  forbidden terms: {len(forbidden)}")

    if dry_run:
        print("  [dry-run] would POST to", f"{ollama_url}/api/chat")
        print("  [dry-run] quality_facts:", facts)
        print("  [dry-run] quality_forbidden:", forbidden)
        return None

    raw_results = []
    for i in range(runs):
        if runs > 1:
            print(f"  run {i+1}/{runs}...", end=" ", flush=True)
        r = run_once(spec, ollama_url, model)
        if r is None:
            continue
        raw_results.append(r)
        if runs > 1:
            violation_str = f"  VIOLATIONS={r['violations']}" if r["violations"] else ""
            print(f"P-eval={r['ped']/1e9:.2f}s  gen={r['ed']/1e9:.2f}s  Q={r['facts_score']:.0%}{violation_str}")
        else:
            print(f"  prompt_tokens={r['pec']}  prompt_eval={r['ped']/1e9:.2f}s  "
                  f"gen_tokens={r['ec']}  gen={r['ed']/1e9:.2f}s  total={r['td']/1e9:.2f}s")
            print(f"  facts={r['facts_score']:.0%} ({len(r['hits'])}/{len(facts)})  "
                  f"hits={r['hits']}  misses={r['misses']}")
            if r["violations"]:
                print(f"  FORBIDDEN VIOLATIONS: {r['violations']}")

    if not raw_results:
        return None

    pec  = raw_results[0]["pec"]
    peds = [r["ped"] for r in raw_results]
    ecs  = [r["ec"]  for r in raw_results]
    eds  = [r["ed"]  for r in raw_results]
    tds  = [r["td"]  for r in raw_results]

    avg_ped = sum(peds) / len(peds)
    avg_ec  = sum(ecs)  / len(ecs)
    avg_ed  = sum(eds)  / len(eds)
    avg_td  = sum(tds)  / len(tds)

    avg_facts = sum(r["facts_score"] for r in raw_results) / len(raw_results)
    all_violations = list({v for r in raw_results for v in r["violations"]})
    all_misses = list({m for r in raw_results for m in r["misses"]})
    all_hits   = [f for f in facts if f not in all_misses]

    prompt_tps = pec / (avg_ped / 1e9) if avg_ped > 0 else 0
    gen_tps    = avg_ec / (avg_ed / 1e9) if avg_ed > 0 else 0

    if runs > 1:
        print(f"\n  avg: prompt_eval={avg_ped/1e9:.2f}s (±{stddev(peds)/1e9:.2f}s)  "
              f"gen_tokens={avg_ec:.0f} (±{stddev(ecs):.0f})  "
              f"gen={avg_ed/1e9:.2f}s (±{stddev(eds)/1e9:.2f}s)  "
              f"facts={avg_facts:.0%}")
        if all_violations:
            print(f"  FORBIDDEN VIOLATIONS (any run): {all_violations}")

    return {
        "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "payload": name,
        "model": model,
        "runs": runs,
        "payload_messages": msg_count,
        "approx_payload_tokens": approx_tokens,
        "prompt_eval_count": pec,
        "prompt_eval_duration": round(avg_ped),
        "prompt_eval_duration_sd": round(stddev(peds)),
        "eval_count": round(avg_ec),
        "eval_count_sd": round(stddev(ecs)),
        "eval_duration": round(avg_ed),
        "eval_duration_sd": round(stddev(eds)),
        "total_duration": round(avg_td),
        "prompt_tps": round(prompt_tps, 1),
        "gen_tps": round(gen_tps, 1),
        "facts_score": round(avg_facts, 4),
        "quality_hits": all_hits,
        "quality_misses": all_misses,
        "forbidden_violations": all_violations,
        "response_snippet": raw_results[-1]["content"][:snippet_len] if snippet_len > 0 else "",
    }


def print_summary(results: list[dict]) -> None:
    if not results:
        return
    print("\n" + "=" * 90)
    print(f"{'Model':<25} {'Payload':<25} {'P-Eval':>8} {'Gen-Tok':>8} {'Gen':>7} {'Facts':>6} {'Viol':>5}")
    print("-" * 90)
    for r in results:
        viol = len(r.get("forbidden_violations", []))
        viol_str = f"{'!'+str(viol):>5}" if viol else f"{'0':>5}"
        print(
            f"{r['model']:<25} {r['payload']:<25} "
            f"{r['prompt_eval_duration']/1e9:>7.2f}s "
            f"{r['eval_count']:>8} "
            f"{r['eval_duration']/1e9:>6.2f}s "
            f"{r['facts_score']:>5.0%} "
            f"{viol_str}"
        )
    print("=" * 90)


def main():
    p = argparse.ArgumentParser(description="Model sweep benchmark runner")
    p.add_argument("--payload", default="all",          help="Payload name or 'all'")
    p.add_argument("--ollama",  default=DEFAULT_OLLAMA, help="Ollama base URL")
    p.add_argument("--model",   default=None,           help="Single model override (default: all from models.yaml)")
    p.add_argument("--runs",    type=int, default=1,    help="Runs per model/payload pair")
    p.add_argument("--snippet-len", type=int, default=300, help="Response snippet length in results (0=omit)")
    p.add_argument("--dry-run", action="store_true",    help="Print without executing")
    args = p.parse_args()

    models = [args.model] if args.model else load_models(MODELS_FILE)
    specs = load_payloads(args.payload)

    print(f"Benchmark sweep: models={models}  payloads={[s['name'] for s in specs]}  runs={args.runs}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"sweep_{ts}.jsonl"

    results = []
    for model in models:
        print(f"\n{'='*60}\nModel: {model}")
        for spec in specs:
            result = run_one(spec, args.ollama, model, args.runs, args.snippet_len, args.dry_run)
            if result is not None:
                results.append(result)
                with open(out_path, "a") as f:
                    f.write(json.dumps(result) + "\n")
                    f.flush()

    print_summary(results)
    if not args.dry_run and results:
        print(f"\nResults written to {out_path}")
        print(out_path)  # last line: path for CI to capture


if __name__ == "__main__":
    main()
