# benchmarks/

Model evaluation harness, payloads, and results. Two parallel organizational
schemes live here — one for raw GHA artifact dumps, one for human-curated
analyses. Both are intentional; pick the right one for what you're doing.

## Layout

```
benchmarks/
  payloads/                     ← input specs (one JSON per scenario)
  run_benchmark.py              ← sweep runner
  analyze.py                    ← AI-powered cross-run summary

  results/                      ← raw GHA-run artifacts (machine-organized)
    run-<github-run-id>/
      <payload>.jsonl           ← raw JSONL per payload
      report.md                 ← AI-generated cross-payload summary

  reports/                      ← human-curated analyses (topic-organized)
    <topic>/
      <date>-<slug>/
        README.md               ← human narrative
        meta.yaml               ← AI-readable structured metadata
        raw/                    ← (optional) JSONLs from local runs
    INDEX.md                    ← catalog of all reports
```

## When to use which

| Scheme | Write when | Read when |
|---|---|---|
| `results/run-*` | GHA Action publishes sweep artifacts | "What did the May 4 N=3 sweep return?" |
| `reports/<topic>/...` | You ran a focused evaluation and synthesized findings | "What's our TASK_MODEL pick history?" or "Have we evaluated X for Y before?" |

The two schemes are **not duplicates** — `results/run-*` is the machine-organized
ledger of every GHA-triggered sweep (cited from `RESEARCH.md`); `reports/`
is the curated, topic-indexed knowledge base. A `reports/<topic>/<date-slug>/`
may reference one or more `results/run-*` dirs as its raw data source, or it
may carry its own local-run JSONLs in `raw/`.

## AI consumption

Each `reports/<topic>/<date-slug>/meta.yaml` is structured for query.
Example: "what models have we evaluated for follow-up formatting":

```bash
find benchmarks/reports -name meta.yaml | xargs yq '.topic + " " + .slug + ": " + (.models | join(", "))' | grep -i task-model
```

The intent is that the dmf-eval workflow ([dvystrcil/homelab#98](https://github.com/dvystrcil/homelab/issues/98))
will both write new entries to this structure AND read prior entries to avoid
redundant evaluation.

## Adding a new report

1. Decide the topic folder (`coder-models/`, `task-model/`, `kv-quantization/`,
   `task-summary/`, etc.). Create a new one if none fit.
2. Make a directory: `reports/<topic>/<YYYY-MM-DD>-<short-slug>/`
3. Write `README.md` (the narrative — what, why, how, findings, action taken).
4. Write `meta.yaml` matching the schema below.
5. If the evaluation produced JSONL artifacts, place them in `raw/`.
6. Append a line to `reports/INDEX.md`.
7. Commit.

## meta.yaml schema

```yaml
topic: <coder-models | task-model | kv-quantization | ...>
date: <YYYY-MM-DD>
slug: <kebab-case-short-name>
title: <one-line human-readable title>
trigger: <one-sentence motivation — what made this evaluation happen>
method: <one-sentence how — sweep runner, in-cluster probe, GHA artifact, etc.>
hardware: <e.g. gfx1151 (Strix Halo)>
models:
  - <model-id>
payloads:
  - <payload-name | "ad-hoc">
outcome:
  winner: <model-id or finding>
  evidence: <list of bullet points>
  action_taken: <list, if any>
raw_artifacts:                   # optional
  - raw/<filename>.jsonl
links:                           # optional
  related_memory:
    - <memory-file-stem>
  related_issues:
    - <https://github.com/...>
  related_commits:
    - <https://github.com/.../commit/...>
```
