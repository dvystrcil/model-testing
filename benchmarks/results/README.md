# Raw sweep results

JSONL output from `benchmarks/run_benchmark.py`, one file per payload per sweep run. Plus the AI-generated `report.md` per run.

These are the data files that back the findings in [`../../RESEARCH.md`](../../RESEARCH.md). The synthesized comparisons, tables, and rankings in RESEARCH.md are derived from these JSONLs.

## Layout

```
run-<github-run-id>/
  <payload-name>.jsonl       # one JSONL per payload (factual_recall, schema_adherence, ...)
  report.md                  # AI-generated cross-payload summary for the run
```

Each JSONL has two record kinds:

- **First line**: `{"type": "metadata", ...}` — run metadata (timestamp, ollama URL, runner hostname, git commit + dirty flag).
- **Subsequent lines**: one row per (model × payload), each with timing measurements (`prompt_eval_*`, `eval_*`, `total_duration`, `*_tps`), the model's response excerpt, and the scored fields (`facts_score`, `quality_hits`, `quality_misses`, `forbidden_violations`, `schema_violations`, etc.).

## Runs included

| Run | Date | Files | Cited as |
|---|---|---|---|
| `run-25290681464` | 2026-05-03 | 14 payloads + report.md | "Step 7 — N=3 full sweep" referenced throughout RESEARCH.md |
| `run-25331490483` | 2026-05-04 | 14 payloads + report.md | Follow-on N=3 cross-check |
| `run-25332432604` | 2026-05-04 | 14 payloads + report.md | Follow-on N=3 cross-check |

## Reproducibility

To reproduce one of these runs from scratch:

```bash
# Local (subset of models, smaller N):
python benchmarks/run_benchmark.py \
    --payload benchmarks/payloads/factual_recall.json \
    --model qwen3.6:35b \
    --runs 3 \
    --out benchmarks/results/local-test.jsonl

# Full sweep (via the model-sweep.yaml GHA workflow):
gh workflow run model-sweep.yaml -f payloads=all -f n=3 --repo dvystrcil/model-testing
```

To re-analyze any run as a markdown report:

```bash
python benchmarks/analyze.py benchmarks/results/run-25290681464/*.jsonl \
    --out reanalysis.md
```

## Hardware caveat

Absolute TPS numbers in these JSONLs reflect the specific inference node used (AMD Ryzen AI MAX+ 395 + Radeon 8060S iGPU via ROCm — see RESEARCH.md "Test Environment"). *Relative* rankings between models are expected to generalize; absolute TPS will differ on different hardware.

## Not in this directory

- Earlier exploratory runs (less than N=3, partial payloads) are not committed — see the cluster's `benchmark-results` PVC for the full history.
- The Phase 1 deterministic-schema-validation results (predating the agentic harness) are referenced in RESEARCH.md but archived separately.
