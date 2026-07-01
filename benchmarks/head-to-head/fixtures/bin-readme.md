# bin/

Operational scripts for the model-testing harness.

## `run-sweep.sh`

Runs the full model sweep locally against the in-cluster Ollama. Reads
`models.yaml` and every payload in `benchmarks/payloads/`.

```
./bin/run-sweep.sh [--runs N]
```

Output: timestamped JSONL under `benchmarks/results/`.

## `analyze.py`

Merges the JSONL result files from a sweep and produces a Markdown report.

```
python bin/analyze.py --run <run-dir>
```

By default it writes the report to stdout.

## `notify-n8n.sh`

POSTs a sweep-completion summary to the n8n `model-sweep-complete` webhook.
Sources `OWUI_API_KEY` from Infisical. Non-fatal on failure (warns, exits 0).

## `seed-memory.py`

Seeds the Postgres-backed memory store from the `feedback_*.md` files. Idempotent;
safe to re-run.

```
python bin/seed-memory.py
```
