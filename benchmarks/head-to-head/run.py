#!/usr/bin/env python3
"""Head-to-head runner: Claude Code vs opencode->OWUI on the 18 tasks.

model-testing#20 AC3/AC4/AC5. Assembles each payload's prompt (substituting
fixtures), invokes the requested side(s), captures latency + token proxy, and
appends one JSONL row per (task, side, run).

Usage:
    python run.py --side both --task all --runs 3
    python run.py --side claude --task 05-multi-step-refactor --runs 1
    python run.py --side opencode --task all --runs 1 --dry-run

The thin CLI wrapper bin/run-head-to-head.sh preserves #20's literal interface.

The two side-invocation commands and the memory-probe target are the pieces most
likely to need tuning for the local environment -- they are constants at the top,
not buried in the code.
"""

import argparse
import datetime
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PAYLOADS_DIR = HERE / "payloads"
FIXTURES_DIR = HERE / "fixtures"
RESULTS_DIR = HERE.parent / "results"

# --- environment-specific knobs (verified against the live env 2026-07-01) ----
# {prompt} is substituted with the assembled task prompt.
CLAUDE_CMD = 'claude -p {prompt}'
# opencode -m takes the config dict KEY, not the display name (qwen3-coder-next
# surfaces as this key). `opencode models` lists them.
OPENCODE_CMD = 'opencode run -m openwebui/qwen3-coder-next-opencode {prompt}'
# opencode side memory-injection probe: filter activity must appear in the
# pipelines pod logs within this window, or the run is aborted (AC4).
PIPELINES_NS = "open-webui"
PIPELINES_SELECTOR = "app=owui-pipelines"
FILTER_MARKER = "memory_loader_postgres/filter/inlet"
PROBE_WINDOW_SECS = 30
PROBE_PROMPT = "Reply with the single word: ok"

FIXTURE_RE = re.compile(r"<contents of (fixtures/[^>]+)>")


def utcnow():
    return datetime.datetime.now(datetime.UTC)


def approx_tokens(text: str) -> int:
    """Cheap token proxy (~4 chars/token). Approximate by design (AC5)."""
    return max(1, len(text) // 4)


def load_prompt(task_id: str) -> str:
    """Extract the verbatim `## Prompt` section and substitute fixtures."""
    path = PAYLOADS_DIR / f"{task_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"no payload {path}")
    text = path.read_text()
    m = re.search(r"^## Prompt\s*$(.*?)^## ", text, re.M | re.S)
    if not m:
        raise ValueError(f"{task_id}: no '## Prompt' section")
    prompt = m.group(1).strip()

    def sub(match):
        rel = match.group(1)
        fpath = HERE / rel
        if not fpath.exists():
            raise FileNotFoundError(f"{task_id} references missing {rel}")
        return fpath.read_text().rstrip("\n")

    return FIXTURE_RE.sub(sub, prompt)


def all_task_ids():
    return sorted(p.stem for p in PAYLOADS_DIR.glob("*.md"))


def run_cli(cmd_template: str, prompt: str, dry_run: bool):
    """Invoke a side's CLI with the prompt. Returns (raw_output, latency_s)."""
    cmd = cmd_template.replace("{prompt}", shlex.quote(prompt))
    if dry_run:
        return f"[dry-run] would run: {cmd[:120]}...", 0.0
    start = utcnow()
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    latency = (utcnow() - start).total_seconds()
    out = proc.stdout.strip()
    if proc.returncode != 0:
        out = f"[exit {proc.returncode}] {proc.stderr.strip()}\n{out}"
    return out, latency


def probe_memory_injection(dry_run: bool) -> bool:
    """AC4: confirm memory_loader_postgres filter fired recently. Abort if not."""
    if dry_run:
        return True
    # Nudge the pipeline so a fresh filter line lands, then inspect the logs.
    run_cli(OPENCODE_CMD, PROBE_PROMPT, dry_run=False)
    cmd = (
        f"kubectl logs -n {PIPELINES_NS} -l {PIPELINES_SELECTOR} "
        f"--since={PROBE_WINDOW_SECS}s --tail=500"
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return FILTER_MARKER in proc.stdout


def run_one(task_id: str, side: str, run_n: int, dry_run: bool) -> dict:
    prompt = load_prompt(task_id)
    cmd_template = CLAUDE_CMD if side == "claude" else OPENCODE_CMD
    output, latency = run_cli(cmd_template, prompt, dry_run)
    return {
        "timestamp": utcnow().isoformat(),
        "side": side,
        "task_id": task_id,
        "run_n": run_n,
        "latency_seconds": round(latency, 3),
        "input_tokens": approx_tokens(prompt),
        "output_tokens": approx_tokens(output),
        "raw_output": output,
        "scores": {},  # filled in by score.py
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Head-to-head runner (model-testing#20)")
    ap.add_argument("--side", choices=["claude", "opencode", "both"], default="both")
    ap.add_argument("--task", default="all", help="task id (e.g. 05-multi-step-refactor) or 'all'")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path, default=None, help="JSONL output path")
    ap.add_argument("--dry-run", action="store_true", help="assemble + echo, don't invoke CLIs")
    args = ap.parse_args(argv)

    sides = ["claude", "opencode"] if args.side == "both" else [args.side]
    tasks = all_task_ids() if args.task == "all" else [args.task]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (RESULTS_DIR / f"head-to-head-{utcnow():%Y%m%dT%H%M%S}.jsonl")

    n = 0
    with out_path.open("w") as f:
        for task_id in tasks:
            for side in sides:
                if side == "opencode" and not probe_memory_injection(args.dry_run):
                    print(
                        f"ABORT: memory injection not confirmed for opencode "
                        f"(no '{FILTER_MARKER}' in {PIPELINES_NS} logs in "
                        f"{PROBE_WINDOW_SECS}s). Fix routing before counting runs.",
                        file=sys.stderr,
                    )
                    return 2
                for run_n in range(1, args.runs + 1):
                    row = run_one(task_id, side, run_n, args.dry_run)
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                    n += 1
                    print(f"[{n}] {task_id} {side} run {run_n} "
                          f"({row['latency_seconds']}s, {row['output_tokens']} tok)")

    print(f"\nwrote {n} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
