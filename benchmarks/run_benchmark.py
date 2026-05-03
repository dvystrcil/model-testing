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
import textwrap
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


def extract_yaml(text: str) -> str:
    """Strip markdown code fences and return the raw YAML content."""
    lines = text.strip().splitlines()
    # If there are no fences, return as-is
    if not any(l.strip().startswith("```") for l in lines):
        return text.strip()
    result, in_fence = [], False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            result.append(line)
    return "\n".join(result).strip()


def wrap_container_spec(yaml_str: str) -> str:
    """
    Embed a container spec snippet into a minimal Pod manifest so kubectl
    can validate it against the full K8s schema.

    Handles two common model output shapes:
      - starts with 'containers:' (full key included)
      - starts with '- name:' (just the list item)
    """
    stripped = yaml_str.strip()
    if stripped.startswith("containers:"):
        containers_block = stripped
    else:
        # Assume it's a list item; add the key
        containers_block = "containers:\n" + textwrap.indent(stripped, "  ")

    pod = (
        "apiVersion: v1\nkind: Pod\n"
        "metadata:\n  name: schema-validate\n"
        "spec:\n"
        + textwrap.indent(containers_block, "  ")
    )
    return pod


def validate_kubectl(yaml_str: str, dry_run: str = "client", namespace: str | None = None) -> list[str]:
    """
    Run `kubectl apply --dry-run=<dry_run>` against yaml_str.
    Returns a list of unknown field names found in the error output.
    An empty list means clean; a non-empty list means violations found.
    Failures in kubectl itself (binary not found, timeout) are logged and
    return an empty list so the sweep continues.

    namespace: passed as -n when using --dry-run=server so the SA's
    RoleBinding scope matches the target namespace.
    """
    cmd = ["kubectl", "apply", f"--dry-run={dry_run}", "-f", "-"]
    if namespace:
        cmd.extend(["-n", namespace])
    try:
        result = subprocess.run(
            cmd,
            input=yaml_str.encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            return []
        stderr = result.stderr.decode()
        fields = re.findall(r'unknown field[s]? "([^"]+)"', stderr)
        if not fields:
            # kubectl errored for a non-schema reason (e.g. malformed YAML);
            # surface the raw message so it's visible in logs
            info(f"[validate] kubectl error (non-schema): {stderr[:200]}")
        return fields
    except FileNotFoundError:
        info("[validate] kubectl not found — skipping schema validation")
        return []
    except subprocess.TimeoutExpired:
        info("[validate] kubectl timed out")
        return []
    except Exception as e:
        info(f"[validate] unexpected error: {e}")
        return []


AGENTIC_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file from the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path to read"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path to write"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def execute_tool(name: str, args: dict, mock_fs: dict, writes: dict) -> str:
    if name == "read_file":
        path = args.get("path", "")
        return mock_fs[path] if path in mock_fs else f"ERROR: file not found: {path}"
    if name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        writes[path] = content
        mock_fs[path] = content
        return f"OK: wrote {len(content)} bytes to {path}"
    return f"ERROR: unknown tool: {name}"


def run_agentic_once(spec: dict, ollama_url: str, model: str) -> dict:
    mock_fs = dict(spec.get("mock_fs", {}))
    scoped = set(spec.get("scoped_files", []))
    expected = spec.get("expected_writes", {})
    forbidden = spec.get("quality_forbidden", [])
    max_turns = spec.get("max_turns", 8)

    messages = [
        {"role": "system", "content": spec.get("system", "You are a Kubernetes configuration assistant.")},
        {"role": "user", "content": spec["task"]},
    ]

    writes: dict[str, str] = {}
    turns_used = 0
    stalled = False
    total_ec = 0

    for turn in range(max_turns):
        payload = {
            "model": model,
            "stream": False,
            "temperature": spec.get("temperature", 0.1),
            "options": {"num_gpu": -1},
            "messages": messages,
            "tools": AGENTIC_TOOLS,
        }
        try:
            resp = ollama_chat(ollama_url, payload)
        except Exception as e:
            info(f"[agentic] turn={turn+1} ollama error: {e}")
            stalled = True
            break

        msg = resp.get("message", {})
        total_ec += resp.get("eval_count", 0)
        tool_calls = msg.get("tool_calls") or []

        # Preserve assistant turn in history
        assistant_entry: dict = {"role": "assistant", "content": msg.get("content") or ""}
        if tool_calls:
            assistant_entry["tool_calls"] = tool_calls
        messages.append(assistant_entry)

        if not tool_calls:
            turns_used = turn
            break

        turns_used = turn + 1

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            result = execute_tool(name, args, mock_fs, writes)
            info(f"[agentic] turn={turns_used}  tool={name}  path={args.get('path', '?')}  → {result[:60]}")
            messages.append({"role": "tool", "name": name, "content": result})
    else:
        stalled = True

    scope_violations = [p for p in writes if p not in scoped]

    all_written = "\n".join(writes.values())
    forbidden_found = [f for f in forbidden if f.lower() in all_written.lower()]

    content_violations: list[str] = []
    for path, frags in expected.items():
        if path not in writes:
            content_violations.append(f"missing_file:{path}")
        else:
            for frag in frags:
                if frag.lower() not in writes[path].lower():
                    content_violations.append(f"missing_fragment:{frag}")

    if stalled:
        outcome = "stall"
    elif scope_violations:
        outcome = "scope_violation"
    elif content_violations or forbidden_found:
        outcome = "hallucination"
    else:
        outcome = "pass"

    return {
        "outcome": outcome,
        "turns_used": turns_used,
        "stalled": stalled,
        "writes": list(writes.keys()),
        "scope_violations": scope_violations,
        "content_violations": content_violations,
        "forbidden_found": forbidden_found,
        "total_ec": total_ec,
    }


def run_agentic_one(spec: dict, ollama_url: str, model: str, runs: int, dry_run: bool) -> dict | None:
    name = spec["name"]
    info(f"payload={name}  model={model}  type=agentic  max_turns={spec.get('max_turns', 8)}  runs={runs}")

    if dry_run:
        print(f"  [dry-run] agentic harness: tools=read_file,write_file  scoped={spec.get('scoped_files')}")
        return None

    raw: list[dict] = []
    for i in range(runs):
        run_label = f"run {i+1}/{runs}  " if runs > 1 else ""
        info(f"{run_label}starting agentic turn loop")
        r = run_agentic_once(spec, ollama_url, model)
        raw.append(r)
        info(f"{run_label}done  outcome={r['outcome']}  turns={r['turns_used']}  tokens={r['total_ec']}"
             + (f"  scope={r['scope_violations']}" if r["scope_violations"] else "")
             + (f"  content={r['content_violations']}" if r["content_violations"] else ""))

    outcomes = [r["outcome"] for r in raw]
    pass_count = outcomes.count("pass")

    return {
        "type": "agentic",
        "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "payload": name,
        "model": model,
        "runs": runs,
        "pass_count": pass_count,
        "outcome_counts": {o: outcomes.count(o) for o in set(outcomes)},
        "avg_turns": round(sum(r["turns_used"] for r in raw) / len(raw), 1),
        "avg_tokens": round(sum(r["total_ec"] for r in raw) / len(raw)),
        "scope_violations": list({v for r in raw for v in r["scope_violations"]}),
        "content_violations": list({v for r in raw for v in r["content_violations"]}),
        "forbidden_found": list({v for r in raw for v in r["forbidden_found"]}),
        "writes": list({w for r in raw for w in r["writes"]}),
    }


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

    schema_violations: list[str] = []
    validate_cfg = spec.get("validate")
    if validate_cfg:
        raw_yaml = extract_yaml(content)
        if validate_cfg.get("yaml_type") == "container_spec":
            raw_yaml = wrap_container_spec(raw_yaml)
        dry_run = validate_cfg.get("kubectl_dry_run", "client")
        namespace = validate_cfg.get("namespace")
        schema_violations = validate_kubectl(raw_yaml, dry_run, namespace)
        if schema_violations:
            info(f"[validate] unknown K8s fields: {schema_violations}")

    return {
        "pec": resp.get("prompt_eval_count", 0),
        "ped": resp.get("prompt_eval_duration", 0),
        "ec":  resp.get("eval_count", 0),
        "ed":  resp.get("eval_duration", 0),
        "td":  resp.get("total_duration", 0),
        "content": content,
        "schema_violations": schema_violations,
        **g,
    }


def run_one(spec: dict, ollama_url: str, model: str, runs: int,
            snippet_len: int, dry_run: bool, save_responses: bool = False) -> dict | None:
    if spec.get("type") == "agentic":
        return run_agentic_one(spec, ollama_url, model, runs, dry_run)

    name = spec["name"]
    facts = spec.get("quality_facts", [])
    forbidden = spec.get("quality_forbidden", [])
    validates = bool(spec.get("validate"))

    msg_count = len(spec["payload"].get("messages", []))
    approx_tokens = sum(
        len(str(m.get("content", ""))) // 4
        for m in spec["payload"].get("messages", [])
    )

    info(f"payload={name}  model={model}  msgs={msg_count}  ~{approx_tokens} tokens  runs={runs}"
         + ("  +kubectl-validate" if validates else ""))
    if forbidden:
        print(f"         forbidden terms: {len(forbidden)}")

    if dry_run:
        print("  [dry-run] would POST to", f"{ollama_url}/api/chat")
        print("  [dry-run] quality_facts:", facts)
        print("  [dry-run] quality_forbidden:", forbidden)
        if validates:
            cfg = spec["validate"]
            print(f"  [dry-run] kubectl validate: dry_run={cfg.get('kubectl_dry_run', 'client')}  yaml_type={cfg.get('yaml_type', 'full_resource')}")
        return None

    raw_results = []
    for i in range(runs):
        run_label = f"run {i+1}/{runs}  " if runs > 1 else ""
        info(f"{run_label}sending request → ollama ({ollama_url})")
        r = run_once(spec, ollama_url, model)
        if r is None:
            continue
        raw_results.append(r)
        if runs > 1:
            violation_str = f"  VIOLATIONS={r['violations']}" if r["violations"] else ""
            schema_str = f"  SCHEMA={r['schema_violations']}" if r.get("schema_violations") else ""
            info(f"{run_label}done  P-eval={r['ped']/1e9:.2f}s  gen={r['ed']/1e9:.2f}s  Q={r['facts_score']:.0%}{violation_str}{schema_str}")
        else:
            info(f"done  P-eval={r['ped']/1e9:.2f}s  gen={r['ed']/1e9:.2f}s  total={r['td']/1e9:.2f}s  "
                 f"facts={r['facts_score']:.0%} ({len(r['hits'])}/{len(facts)})")
            if r["misses"]:
                print(f"         misses={r['misses']}")
            if r["violations"]:
                print(f"         FORBIDDEN VIOLATIONS: {r['violations']}")
            if r.get("schema_violations"):
                print(f"         SCHEMA VIOLATIONS: {r['schema_violations']}")

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
    # Union of schema violations seen across all runs (None if validation not configured)
    all_schema_violations = list({v for r in raw_results for v in r.get("schema_violations", [])}) if validates else None

    prompt_tps = pec / (avg_ped / 1e9) if avg_ped > 0 else 0
    gen_tps    = avg_ec / (avg_ed / 1e9) if avg_ed > 0 else 0

    if runs > 1:
        print(f"\n  avg: prompt_eval={avg_ped/1e9:.2f}s (±{stddev(peds)/1e9:.2f}s)  "
              f"gen_tokens={avg_ec:.0f} (±{stddev(ecs):.0f})  "
              f"gen={avg_ed/1e9:.2f}s (±{stddev(eds)/1e9:.2f}s)  "
              f"facts={avg_facts:.0%}")
        if all_violations:
            print(f"  FORBIDDEN VIOLATIONS (any run): {all_violations}")
        if all_schema_violations:
            print(f"  SCHEMA VIOLATIONS (any run): {all_schema_violations}")

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
        "schema_violations": all_schema_violations,
        "response_snippet": raw_results[-1]["content"][:snippet_len] if snippet_len > 0 else "",
        "response_text": raw_results[-1]["content"] if save_responses else None,
    }


def print_summary(results: list[dict]) -> None:
    if not results:
        return

    agentic = [r for r in results if r.get("type") == "agentic"]
    standard = [r for r in results if r.get("type") != "agentic"]

    if agentic:
        print("\n" + "=" * 90)
        print(f"{'Model':<25} {'Payload':<28} {'Outcome':<18} {'Turns':>6} {'Tokens':>8}")
        print("-" * 90)
        for r in agentic:
            counts = r.get("outcome_counts", {})
            outcome_str = "/".join(f"{k}:{v}" for k, v in counts.items())
            pass_frac = f"{r['pass_count']}/{r['runs']}"
            print(f"{r['model']:<25} {r['payload']:<28} {pass_frac+' '+outcome_str:<18} {r['avg_turns']:>6.1f} {r['avg_tokens']:>8}")
        print("=" * 90)

    if not standard:
        return
    has_schema = any(r.get("schema_violations") is not None for r in standard)
    width = 100 if has_schema else 90
    print("\n" + "=" * width)
    header = f"{'Model':<25} {'Payload':<25} {'P-Eval':>8} {'Gen-Tok':>8} {'Gen':>7} {'Facts':>6} {'Viol':>5}"
    if has_schema:
        header += f" {'Schema':>7}"
    print(header)
    print("-" * width)
    for r in standard:
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
        if has_schema:
            sv = r.get("schema_violations")
            if sv is None:
                schema_str = f"{'—':>7}"
            elif sv:
                schema_str = f"{'!'+str(len(sv)):>7}"
            else:
                schema_str = f"{'0':>7}"
            line += f" {schema_str}"
        print(line)
    print("=" * width)


def main():
    p = argparse.ArgumentParser(description="Model sweep benchmark runner")
    p.add_argument("--payload", default="all",          help="Payload name or 'all'")
    p.add_argument("--ollama",  default=DEFAULT_OLLAMA, help="Ollama base URL")
    p.add_argument("--model",   default=None,           help="Single model override (default: all from models.yaml)")
    p.add_argument("--runs",    type=int, default=1,    help="Runs per model/payload pair")
    p.add_argument("--snippet-len", type=int, default=300, help="Response snippet length in results (0=omit)")
    p.add_argument("--save-responses", action="store_true", help="Save full response text in JSONL (for DPO dataset generation)")
    p.add_argument("--dry-run",   action="store_true",    help="Print without executing")
    p.add_argument("--no-warmup", action="store_true",    help="Skip per-model warmup request")
    p.add_argument("--system-prompt", default=None,     help="Text appended to the system message of every payload")
    args = p.parse_args()

    models = [args.model] if args.model else load_models(MODELS_FILE)
    specs = load_payloads(args.payload)

    if args.system_prompt:
        info(f"System prompt injection: {args.system_prompt!r}")
        for spec in specs:
            if spec.get("type") == "agentic":
                base = spec.get("system", "You are a Kubernetes configuration assistant.")
                spec["system"] = f"{base}\n\n{args.system_prompt}"
            else:
                msgs = spec["payload"].setdefault("messages", [])
                sys_msgs = [m for m in msgs if m.get("role") == "system"]
                if sys_msgs:
                    sys_msgs[0]["content"] += f"\n\n{args.system_prompt}"
                else:
                    msgs.insert(0, {"role": "system", "content": args.system_prompt})

    validated_payloads = [s["name"] for s in specs if s.get("validate")]
    print(f"Benchmark sweep: models={models}  payloads={[s['name'] for s in specs]}  runs={args.runs}")
    if validated_payloads:
        info(f"kubectl schema validation enabled for: {validated_payloads}")

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
            result = run_one(spec, args.ollama, model, args.runs, args.snippet_len, args.dry_run, args.save_responses)
            if result is not None:
                result.update(meta)
                results.append(result)
                with open(out_path, "a") as f:
                    f.write(json.dumps(result) + "\n")
                    f.flush()

    print_summary(results)
    if not args.dry_run and results:
        info(f"Results written to {out_path}")
        (RESULTS_DIR / ".last_result").write_text(str(out_path))


if __name__ == "__main__":
    main()
