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
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml


def info(msg: str) -> None:
    ts = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
    print(f"[INFO {ts}] {msg}", flush=True)

PAYLOADS_DIR = Path(__file__).parent / "payloads"
RESULTS_DIR = Path(__file__).parent / "results"
MODELS_FILE = Path(__file__).parent.parent / "models.yaml"
DEFAULT_OLLAMA = "http://localhost:11434"
HTTP_TIMEOUT = 300


def collect_metadata(ollama_url: str) -> dict:
    meta: dict = {
        "ollama_url": ollama_url,
        "ollama_version": "unknown",
        "hostname": socket.gethostname(),
        "git_commit": "unknown",
        "git_dirty": False,
    }
    try:
        req = urllib.request.Request(f"{ollama_url}/api/version")
        with urllib.request.urlopen(req, timeout=10) as r:
            meta["ollama_version"] = json.loads(r.read()).get("version", "unknown")
    except Exception:
        pass
    try:
        meta["git_commit"] = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=Path(__file__).parent.parent,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        meta["git_dirty"] = bool(dirty)
    except Exception:
        pass
    return meta


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


def judge_once(response_text: str, judge_context: str, judge_model: str, ollama_url: str) -> list[str] | None:
    """Call a judge model to detect hallucinated field names. Returns list of violations, or None on failure."""
    prompt = (
        f"Context: {judge_context}\n\n"
        f"Output to validate:\n{response_text}\n\n"
        "List any field names in this output that do not exist in the schema described above. "
        "Only flag field name hallucinations (invented keys), not value errors.\n"
        'Respond ONLY with JSON: {"violations": ["field1"], "reasoning": "one sentence"}\n'
        'If there are no violations: {"violations": [], "reasoning": "all fields are valid"}'
    )
    payload = {
        "model": judge_model,
        "stream": False,
        "options": {"num_gpu": -1},
        "messages": [
            {"role": "system", "content": "You are a strict schema validator. Respond only with the JSON asked for. No extra text."},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        resp = ollama_chat(ollama_url, payload)
        raw = strip_think(resp.get("message", {}).get("content", ""))
        # Extract JSON even if the model wraps it in a code block
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            info(f"[judge] could not parse JSON from response: {raw[:120]}")
            return None
        data = json.loads(match.group())
        return data.get("violations", [])
    except Exception as e:
        info(f"[judge] error: {e}")
        return None


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


def warmup_model(ollama_url: str, model: str) -> None:
    info(f"warming up {model} — ensuring model is fully loaded into VRAM...")
    payload = {
        "model": model,
        "stream": False,
        "options": {"num_gpu": -1},
        "messages": [{"role": "user", "content": "hi"}],
    }
    try:
        resp = ollama_chat(ollama_url, payload)
        load_ms = resp.get("total_duration", 0) / 1e6
        info(f"warmup done  total={load_ms:.0f}ms")
    except Exception as e:
        info(f"warmup failed (continuing anyway): {e}")


def run_once(spec: dict, ollama_url: str, model: str, judge_model: str | None = None) -> dict | None:
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

    judge_violations: list[str] | None = None
    if judge_model and spec.get("use_judge") and spec.get("judge_context"):
        judge_violations = judge_once(content, spec["judge_context"], judge_model, ollama_url)

    return {
        "pec": resp.get("prompt_eval_count", 0),
        "ped": resp.get("prompt_eval_duration", 0),
        "ec":  resp.get("eval_count", 0),
        "ed":  resp.get("eval_duration", 0),
        "td":  resp.get("total_duration", 0),
        "content": content,
        "judge_violations": judge_violations,
        **g,
    }


def run_one(spec: dict, ollama_url: str, model: str, runs: int,
            snippet_len: int, dry_run: bool, judge_model: str | None = None) -> dict | None:
    name = spec["name"]
    facts = spec.get("quality_facts", [])
    forbidden = spec.get("quality_forbidden", [])

    msg_count = len(spec["payload"].get("messages", []))
    approx_tokens = sum(
        len(str(m.get("content", ""))) // 4
        for m in spec["payload"].get("messages", [])
    )

    info(f"payload={name}  model={model}  msgs={msg_count}  ~{approx_tokens} tokens  runs={runs}")
    if forbidden:
        print(f"         forbidden terms: {len(forbidden)}")

    if dry_run:
        print("  [dry-run] would POST to", f"{ollama_url}/api/chat")
        print("  [dry-run] quality_facts:", facts)
        print("  [dry-run] quality_forbidden:", forbidden)
        return None

    raw_results = []
    for i in range(runs):
        run_label = f"run {i+1}/{runs}  " if runs > 1 else ""
        info(f"{run_label}sending request → ollama ({ollama_url})")
        r = run_once(spec, ollama_url, model, judge_model=judge_model)
        if r is None:
            continue
        raw_results.append(r)
        if runs > 1:
            violation_str = f"  VIOLATIONS={r['violations']}" if r["violations"] else ""
            judge_str = f"  JUDGE={r['judge_violations']}" if r.get("judge_violations") else ""
            info(f"{run_label}done  P-eval={r['ped']/1e9:.2f}s  gen={r['ed']/1e9:.2f}s  Q={r['facts_score']:.0%}{violation_str}{judge_str}")
        else:
            info(f"done  P-eval={r['ped']/1e9:.2f}s  gen={r['ed']/1e9:.2f}s  total={r['td']/1e9:.2f}s  "
                 f"facts={r['facts_score']:.0%} ({len(r['hits'])}/{len(facts)})")
            if r["misses"]:
                print(f"         misses={r['misses']}")
            if r["violations"]:
                print(f"         FORBIDDEN VIOLATIONS: {r['violations']}")
            if r.get("judge_violations"):
                print(f"         JUDGE VIOLATIONS: {r['judge_violations']}")

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
    # Aggregate judge violations: None if judge was never run, list (possibly empty) if it ran
    judge_ran = any(r.get("judge_violations") is not None for r in raw_results)
    all_judge_violations = list({v for r in raw_results for v in (r.get("judge_violations") or [])}) if judge_ran else None

    prompt_tps = pec / (avg_ped / 1e9) if avg_ped > 0 else 0
    gen_tps    = avg_ec / (avg_ed / 1e9) if avg_ed > 0 else 0

    if runs > 1:
        print(f"\n  avg: prompt_eval={avg_ped/1e9:.2f}s (±{stddev(peds)/1e9:.2f}s)  "
              f"gen_tokens={avg_ec:.0f} (±{stddev(ecs):.0f})  "
              f"gen={avg_ed/1e9:.2f}s (±{stddev(eds)/1e9:.2f}s)  "
              f"facts={avg_facts:.0%}")
        if all_violations:
            print(f"  FORBIDDEN VIOLATIONS (any run): {all_violations}")
        if all_judge_violations:
            print(f"  JUDGE VIOLATIONS (any run): {all_judge_violations}")

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
        "judge_violations": all_judge_violations,
        "response_snippet": raw_results[-1]["content"][:snippet_len] if snippet_len > 0 else "",
    }


def print_summary(results: list[dict]) -> None:
    if not results:
        return
    has_judge = any(r.get("judge_violations") is not None for r in results)
    width = 100 if has_judge else 90
    print("\n" + "=" * width)
    header = f"{'Model':<25} {'Payload':<25} {'P-Eval':>8} {'Gen-Tok':>8} {'Gen':>7} {'Facts':>6} {'Viol':>5}"
    if has_judge:
        header += f" {'Judge':>6}"
    print(header)
    print("-" * width)
    for r in results:
        viol = len(r.get("forbidden_violations", []))
        viol_str = f"{'!'+str(viol):>5}" if viol else f"{'0':>5}"
        line = (
            f"{r['model']:<25} {r['payload']:<25} "
            f"{r['prompt_eval_duration']/1e9:>7.2f}s "
            f"{r['eval_count']:>8} "
            f"{r['eval_duration']/1e9:>6.2f}s "
            f"{r['facts_score']:>5.0%} "
            f"{viol_str}"
        )
        if has_judge:
            jv = r.get("judge_violations")
            if jv is None:
                judge_str = f"{'—':>6}"
            elif jv:
                judge_str = f"{'!'+str(len(jv)):>6}"
            else:
                judge_str = f"{'0':>6}"
            line += f" {judge_str}"
        print(line)
    print("=" * width)


def main():
    p = argparse.ArgumentParser(description="Model sweep benchmark runner")
    p.add_argument("--payload", default="all",          help="Payload name or 'all'")
    p.add_argument("--ollama",  default=DEFAULT_OLLAMA, help="Ollama base URL")
    p.add_argument("--model",   default=None,           help="Single model override (default: all from models.yaml)")
    p.add_argument("--runs",    type=int, default=1,    help="Runs per model/payload pair")
    p.add_argument("--snippet-len", type=int, default=300, help="Response snippet length in results (0=omit)")
    p.add_argument("--dry-run",   action="store_true",    help="Print without executing")
    p.add_argument("--no-warmup", action="store_true",    help="Skip per-model warmup request")
    p.add_argument("--judge-model", default="qwen3.6:35b",
                   help="Model used to judge hallucinated fields (default: qwen3.6:35b). Set to '' to disable.")
    args = p.parse_args()
    judge_model = args.judge_model.strip() or None

    models = [args.model] if args.model else load_models(MODELS_FILE)
    specs = load_payloads(args.payload)

    judged_payloads = [s["name"] for s in specs if s.get("use_judge")]
    print(f"Benchmark sweep: models={models}  payloads={[s['name'] for s in specs]}  runs={args.runs}")
    if judge_model and judged_payloads:
        info(f"LLM judge enabled: model={judge_model}  payloads={judged_payloads}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"sweep_{ts}.jsonl"

    meta = {} if args.dry_run else collect_metadata(args.ollama)
    if meta:
        info(f"ollama={meta['ollama_version']}  host={meta['hostname']}  commit={meta['git_commit']}"
             + ("  [dirty]" if meta.get("git_dirty") else ""))
        with open(out_path, "a") as f:
            f.write(json.dumps({"type": "metadata", "ts": ts, **meta}) + "\n")

    results = []
    for model in models:
        info(f"{'='*50}")
        info(f"MODEL: {model}")
        info(f"{'='*50}")
        if not args.dry_run and not args.no_warmup:
            warmup_model(args.ollama, model)
        for spec in specs:
            result = run_one(spec, args.ollama, model, args.runs, args.snippet_len, args.dry_run, judge_model=judge_model)
            if result is not None:
                result.update(meta)  # embed ollama_version, hostname, git_commit into every row
                results.append(result)
                with open(out_path, "a") as f:
                    f.write(json.dumps(result) + "\n")
                    f.flush()

    print_summary(results)
    if not args.dry_run and results:
        info(f"Results written to {out_path}")
        # Write path to a sidecar file so CI can read it without piping stdout
        (RESULTS_DIR / ".last_result").write_text(str(out_path))


if __name__ == "__main__":
    main()
