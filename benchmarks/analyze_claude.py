#!/usr/bin/env python3
"""
Claude-API-powered analysis of benchmark results.

Sibling of analyze.py — same data, same prompt, different summarizer.
Used by the dual-summarize step of the model-sweep GHA workflow
(model-testing#36 Phase 2) so every sweep produces TWO paired summaries:
one from local Ollama (analyze.py) and one from Claude API (this file).
Comparing them is the A3 measurement of homelab#292.

The Anthropic call goes through OWUI's existing Anthropic Connection
rather than direct to api.anthropic.com — keeps the API key in one
place (OWUI's PG config) and the spend visible in OWUI's existing
instrumentation (api_choice_recorder).

Usage:
    python benchmarks/analyze_claude.py results/sweep_*.jsonl \\
        --owui-url http://open-webui.open-webui.svc.cluster.local \\
        --owui-api-key "$OWUI_API_KEY" \\
        --model claude-haiku-4-5-20251001 \\
        --out report-claude.md
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

# Reuse the prompt + table helpers from analyze.py so both summarizers
# see literally the same input. If analyze.py grows new helpers, they
# flow through here without changes.
from analyze import (
    SAFETY_GATE_PAYLOADS,
    build_agentic_table,
    build_env_block,
    build_prompt,
    build_summary_table,
    hard_failures,
    load_results,
    safety_facts_block,
    safety_gate_report,
    strip_think,
)

DEFAULT_OWUI_URL = "http://open-webui.open-webui.svc.cluster.local"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
HTTP_TIMEOUT = 600  # Claude can take a while on long inputs


def owui_chat(owui_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    """POST to OWUI's OpenAI-compatible chat-completions endpoint.

    OWUI routes the request to its configured Anthropic Connection
    based on the `model` field (e.g. `claude-haiku-4-5-20251001`).
    Response shape is OpenAI-compatible: `choices[0].message.content`.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{owui_url}/api/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        resp = json.loads(r.read())
    return strip_think(resp["choices"][0]["message"]["content"])


def main():
    p = argparse.ArgumentParser(description="Claude-powered analysis of benchmark results")
    p.add_argument("results_files", nargs="+", help="One or more sweep JSONL files to merge and analyze")
    p.add_argument("--owui-url", default=DEFAULT_OWUI_URL, help="OWUI base URL")
    p.add_argument("--owui-api-key", default=None, help="OWUI API key (or env OWUI_API_KEY)")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Claude model id as registered in OWUI")
    p.add_argument("--out", default=None, help="Output markdown file (default: stdout)")
    p.add_argument("--prompt-out", default=None, help="Also write the exact prompt sent to the summariser here")
    args = p.parse_args()

    api_key = args.owui_api_key
    if not api_key:
        import os
        api_key = os.environ.get("OWUI_API_KEY")
    if not api_key:
        print("ERROR: --owui-api-key not provided and OWUI_API_KEY not in env", file=sys.stderr)
        sys.exit(1)

    paths = [Path(f) for f in args.results_files]
    missing = [pp for pp in paths if not pp.exists()]
    if missing:
        for m in missing:
            print(f"ERROR: {m} not found", file=sys.stderr)
        sys.exit(1)

    meta, results, agentic = load_results(*paths)
    if not results and not agentic:
        print("ERROR: no results found in file(s)", file=sys.stderr)
        sys.exit(1)

    models_seen = sorted({r["model"] for r in results + agentic})
    payloads_seen = sorted({r["payload"] for r in results + agentic})
    print(f"[INFO] Loaded {len(paths)} file(s) — {len(results)} standard + {len(agentic)} agentic results", file=sys.stderr)
    print(f"[INFO] Models: {models_seen}", file=sys.stderr)
    print(f"[INFO] Payloads: {payloads_seen}", file=sys.stderr)
    if meta:
        print(
            f"[INFO] Environment: ollama={meta.get('ollama_version')}  "
            f"host={meta.get('hostname')}  commit={meta.get('git_commit')}",
            file=sys.stderr,
        )
    print(f"[INFO] Sending to {args.model} via OWUI for summary...", file=sys.stderr)

    table = build_summary_table(results) if results else "_No standard results._"
    env_block = build_env_block(meta, paths[0])
    # model-testing#76 AC1: the summariser is TOLD the computed hard failures.
    prompt = safety_facts_block(hard_failures(results)) + "\n\n" + build_prompt(table, results, meta, agentic)
    if args.prompt_out:   # AC4
        Path(args.prompt_out).write_text(prompt)

    analysis = owui_chat(
        args.owui_url,
        api_key,
        args.model,
        [
            {"role": "system", "content": "You are a concise technical analyst. Use markdown. Be specific about numbers."},
            {"role": "user", "content": prompt},
        ],
    )

    rec_marker, rec_banner, hard_md, rec_fatal = safety_gate_report(analysis, results, models_seen)
    print(rec_marker, file=sys.stderr)
    if rec_fatal:
        print(f"::error::{rec_marker} -- the analysis contradicts the safety "
              f"results (model-testing#76); see the banner in the report", file=sys.stderr)

    agentic_section = f"\n## Agentic Results\n\n{build_agentic_table(agentic)}\n" if agentic else ""
    output = (
        f"# Benchmark Report (Claude analysis)\n\n"
        f"_Summarizer: {args.model} via OWUI Anthropic Connection_\n\n"
        f"## Environment\n\n{env_block}\n"
        f"## Results\n\n{table}\n"
        f"{hard_md}\n"
        f"{agentic_section}\n"
        f"## AI Analysis\n{rec_banner}\n{analysis}\n"
    )

    if args.out:
        Path(args.out).write_text(output)
        print(f"[INFO] Report written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
