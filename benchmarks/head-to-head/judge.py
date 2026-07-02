#!/usr/bin/env python3
"""Machine-judge the human-judgment dims for a head-to-head run (model-testing#20).

Option 1 / "machine-judged v1": a fresh `claude -p` judge scores each row's human
dims (correctness, faithfulness, tool_coherence) against that task's rubric. Auto
dims come from score.py deterministically. Writes:

  - a scored JSONL (each row's scores filled) -- the operator's later interactive
    pass resumes from this and OVERRIDES the machine scores per row.
  - a report .md, stamped PROVISIONAL / MACHINE-JUDGED with the bias disclosure.

The judge is a fresh session per row (no cross-row context bleed). It is NOT the
operator and NOT unbiased -- Claude scoring a Claude-vs-local comparison favors
Side A; that caveat is stamped on every report this produces.

Usage:
    python judge.py --in <run.jsonl> --out-report <report.md> --out-scored <scored.jsonl>
"""

import argparse
import datetime
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import score  # noqa: E402
import run  # noqa: E402  (clean_output for opencode chrome)

PAYLOADS_DIR = Path(__file__).parent / "payloads"
# --tools "" disables ALL tools: claude -p is agentic and will otherwise try to
# investigate the rubric's file/command references (read memory_store.py, run
# kubectl) instead of grading from the text -> stalls. The judge must grade only
# from the message.
JUDGE_TIMEOUT = 180
# Default judge: a NEUTRAL frontier model on opencode's free tier -- neither
# Claude (Side A) nor qwen (Side B), so it isn't grading its own team. Bypasses
# the OWUI memory filter (opencode/ provider, not the openwebui/ path).
# Alternatives: opencode/nemotron-3-ultra-free, or 'claude' (biased: pro Side A).
DEFAULT_JUDGE = "opencode/deepseek-v4-flash-free"
# claude judge is variadic-flag-sensitive: --tools BEFORE -p or it eats the prompt.
CLAUDE_JUDGE_CMD = 'claude --tools "" -p {prompt}'
# opencode judge sandboxes writes; no clean --no-tools flag, so the prompt tells
# it not to use tools (it's agentic) -- belt for both backends.
OPENCODE_JUDGE_CMD = 'opencode run -m {model} --dir {dir} {prompt}'
NO_TOOLS = ("Do NOT use any tools, do not read files or run commands. Grade ONLY "
            "from the text in this message.\n\n")

PROVENANCE = (
    "> **PROVISIONAL — MACHINE-JUDGED v1 (judge: `{judge}`).** The human-judgment "
    "dims (correctness, faithfulness, tool_coherence) were scored by a fresh "
    "`{judge}` judge against each task's rubric, NOT by the operator. **Residual "
    "bias:** a neutral judge removes the pro-Side-A tilt of a Claude judge, but "
    "the rubrics/prompts are Claude-lineage, N=1 (no variance), and the `memory` "
    "metric is a keyword heuristic -- so read Side-A/B *gaps* as directional, not "
    "final. Re-score with operator judgment (resumable, overrides these) for the "
    "authoritative version. Judged {when} by `{judge}`; {ok}/{total} rows clean."
)


def rubric_for(task_id: str) -> str:
    """The '## Ground truth / rubric' + '## Scoring' sections of the payload."""
    path = PAYLOADS_DIR / f"{task_id}.md"
    text = path.read_text()
    m = re.search(r"^## Ground truth.*", text, re.M | re.S)
    return m.group(0).strip() if m else text


def judge_prompt(task_id: str, dims: list, output: str) -> str:
    return (
        NO_TOOLS +
        "You are an impartial grader. Score a candidate answer to a benchmark task "
        "against its rubric. Be strict and literal.\n\n"
        f"Score ONLY these dimensions, each an integer 0-3: {', '.join(dims)}.\n"
        "Anchors: 3 = fully meets the rubric; 2 = right shape, one real weakness; "
        "1 = partially there, a significant miss; 0 = wrong, missing, or off-task.\n\n"
        f"## Task rubric\n{rubric_for(task_id)}\n\n"
        f"## Candidate answer\n{output}\n\n"
        f"Respond with ONLY a JSON object with exactly these integer keys: "
        f"{', '.join(dims)}. Example: {{\"correctness\": 2}}. No prose, no fences."
    )


def call_judge(prompt: str, judge: str, sandbox_dir: str) -> dict | None:
    if judge == "claude":
        cmd = CLAUDE_JUDGE_CMD.replace("{prompt}", shlex.quote(prompt))
    else:  # neutral opencode model, e.g. opencode/deepseek-v4-flash-free
        cmd = (OPENCODE_JUDGE_CMD
               .replace("{model}", shlex.quote(judge))
               .replace("{dir}", shlex.quote(sandbox_dir))
               .replace("{prompt}", shlex.quote(prompt)))
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=JUDGE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None
    out = run.clean_output(proc.stdout)  # strip opencode ANSI/header chrome
    m = re.search(r"\{[^{}]*\}", out, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Machine-judge a head-to-head run (#20)")
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out-report", type=Path, required=True)
    ap.add_argument("--out-scored", type=Path, required=True)
    ap.add_argument("--judge", default=DEFAULT_JUDGE,
                    help="judge model: an opencode model key (neutral, default) or 'claude'")
    args = ap.parse_args(argv)

    rows = [json.loads(l) for l in args.inp.read_text().splitlines() if l.strip()]
    sandbox_dir = tempfile.mkdtemp(prefix="h2h-judge-")
    ok = 0
    try:
        with args.out_scored.open("w") as sink:
            for i, row in enumerate(rows, 1):
                auto = score.auto_scores(row)
                dims = score.human_dims_for(row["task_id"])
                judged = {}
                if dims:
                    res = call_judge(judge_prompt(row["task_id"], dims, row["raw_output"]),
                                     args.judge, sandbox_dir)
                    if res is not None:
                        judged = {d: int(res[d]) for d in dims if d in res
                                  and str(res[d]).lstrip("-").isdigit()}
                if not dims or len(judged) == len(dims):
                    ok += 1
                else:
                    print(f"  ! {row['task_id']} {row['side']}: judge missed "
                          f"{set(dims) - set(judged)}", file=sys.stderr)
                row["scores"] = {**auto, **judged}
                sink.write(json.dumps(row) + "\n")
                sink.flush()
                print(f"[{i}/{len(rows)}] {row['task_id']} {row['side']}: "
                      f"{ {k: row['scores'].get(k) for k in dims} }")
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)

    agg = score.aggregate(rows)
    prov = PROVENANCE.format(
        judge=args.judge, when=datetime.date.today().isoformat(), ok=ok, total=len(rows))
    score.write_report(agg, args.out_report, provenance=prov)
    print(f"\njudged {ok}/{len(rows)} rows cleanly")
    print(f"scored JSONL -> {args.out_scored}")
    print(f"report       -> {args.out_report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
