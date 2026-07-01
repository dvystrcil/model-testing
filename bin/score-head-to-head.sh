#!/usr/bin/env bash
# model-testing#20 AC6 — literal CLI over benchmarks/head-to-head/score.py.
#   ./bin/score-head-to-head.sh --in <run.jsonl> --out results/head-to-head-<date>.md
set -euo pipefail
exec python3 "$(dirname "$0")/../benchmarks/head-to-head/score.py" "$@"
