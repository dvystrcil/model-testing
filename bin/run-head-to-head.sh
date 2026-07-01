#!/usr/bin/env bash
# model-testing#20 AC3 — literal CLI over benchmarks/head-to-head/run.py.
#   ./bin/run-head-to-head.sh --side both --task all --runs 3
set -euo pipefail
exec python3 "$(dirname "$0")/../benchmarks/head-to-head/run.py" "$@"
