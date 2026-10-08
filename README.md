# model-testing

Benchmark suite for evaluating local LLMs as AI coding assistants in a homelab Kubernetes context. Models are tested against realistic tasks — YAML generation, schema adherence, instruction scope, and factual recall — and scored automatically. An AI analysis step summarizes results and recommends a winner.

## Why this exists

The homelab runs an AI coding assistant pipeline (`dual_model_filter`) that routes tasks between a fast model (coder) and a reasoning model. When a new model is released, it needs to pass these benchmarks before being promoted to production. The tests are designed to catch the real failure modes we've observed: hallucinated YAML fields, scope violations (touching files outside the task), and forgetting constraints under cognitive load.

Read [`LEARNED.md`](LEARNED.md) before proposing a model swap or a new sweep — it's the terse record of findings that turned out to matter, including the wrong hypotheses that were chased before being disproven. Cheaper to read than to re-discover.

## Repo layout

```
model-testing/
├── benchmarks/
│   ├── payloads/           # One JSON file per test scenario
│   ├── results/            # JSONL output from each sweep run
│   ├── run_benchmark.py    # Sweep runner — hits all models × all payloads
│   └── analyze.py          # AI-powered summary using qwen3.6:35b
├── bin/
│   ├── toolcall-regress.py # Fixed tool-call probe for ollama upgrades (homelab#1205)
│   ├── toolcall-health.py  # Daily real-use tool-call outcomes from Open WebUI
│   └── rolling-issue.py    # One rolling GitHub issue per alert lane
├── tests/                  # Unit tests (hermetic: no ollama, no network)
├── .github/
│   └── workflows/
│       └── model-sweep.yaml  # CI: runs on ARC runner co-located with Ollama
├── models.yaml             # List of models to sweep
└── requirements.txt
```

## Payloads

| Name | What it tests |
|------|---------------|
| `factual_recall` | Retains exact numbers from a long conversation after many intervening turns |
| `schema_adherence` | Creates a new YAML config from a reference without hallucinating fields |
| `instruction_following` | Creates a new config without touching an existing out-of-scope one |
| `scope_creep` | Output a single changed line — tests whether the model silently touches out-of-scope configs |
| `hallucination_probe` | Add GPU support using correct K8s fields — detects invented/non-existent fields like `gpuAccelerator` |
| `stress_constraint_easy` | K8s manifest with 4 simultaneous requirements — baseline, all models should pass |
| `stress_constraint_medium` | K8s manifest with 7 requirements — mid-point of the cognitive load curve |
| `stress_multi_constraint` | K8s manifest with 10 requirements — full load, reveals which constraints get dropped |
| `refusal_boundary` | Request for privileged root container — tests whether the model blindly complies or pushes back |

### Adding a new payload

Create `benchmarks/payloads/<name>.json`:

```json
{
  "name": "my_test",
  "description": "What this tests",
  "quality_facts": ["term that must appear in the response"],
  "quality_forbidden": ["term that must NOT appear"],
  "payload": {
    "model": "REPLACED_BY_RUNNER",
    "stream": false,
    "temperature": 0.1,
    "messages": [
      {"role": "system", "content": "..."},
      {"role": "user",   "content": "..."}
    ]
  }
}
```

- `quality_facts`: substring matches in the response — each hit adds to the score
- `quality_forbidden`: any match is flagged as a hard failure (scope violation, hallucination)

## Scoring

- **facts_score**: fraction of required facts present (`hits / total`)
- **forbidden_violations**: list of forbidden terms found — any non-empty list is a red flag
- **gen_tps**: generation tokens/sec — latency indicator
- **P-eval time**: prompt evaluation time — spikes indicate the model was cold (evicted from VRAM)

## Running locally

### Prerequisites

```bash
brew install act          # Run GitHub Actions locally
brew install ollama       # Or point at your cluster via port-forward
```

### Port-forward to cluster Ollama (if not running locally)

```bash
kubectl port-forward -n ollama svc/ollama 11434:80
```

### Run the sweep directly

```bash
pip install -r requirements.txt

# All models, all payloads
python benchmarks/run_benchmark.py --ollama http://localhost:11434

# Single model
python benchmarks/run_benchmark.py --model gemma4:26b

# Single payload, 3 runs for averaging
python benchmarks/run_benchmark.py --payload schema_adherence --runs 3

# Skip warmup (faster, but first result may include model load time)
python benchmarks/run_benchmark.py --no-warmup

# Dry run (no Ollama calls)
python benchmarks/run_benchmark.py --dry-run
```

### Run with act (mirrors CI exactly)

```bash
# Requires a .env file or secrets configured
act workflow_dispatch -W .github/workflows/model-sweep.yaml \
  --var OLLAMA_URL=http://localhost:11434
```

### Run unit tests

```bash
python tests/test_benchmark.py
```

## What the report tells you

After a sweep, `analyze.py` sends all results to `qwen3.6:35b` and produces a markdown report. Here is how to read it:

**Facts score** tells you how thoroughly the model followed instructions. 100% means it hit every required term. Below 80% on a simple payload is a bad sign — the model is cutting corners or misunderstanding scope.

**Forbidden violations** are the most important signal. A violation means the model either broke scope (modified something it shouldn't), hallucinated a field that doesn't exist in the schema, or used a dangerous default (like `latest` tags or `privileged: true`). Even one violation disqualifies a model for the coder role.

**Gen TPS** tells you how fast the model generates. For an interactive assistant, below ~10 tok/s feels slow. The gemma4 family generates 3-5× faster than qwen3.6:35b at the cost of reliability.

**P-eval time** is the prompt evaluation time. A spike (e.g. 8s vs normally 0.5s) means the model was evicted from VRAM and had to reload. The warmup step prevents this from contaminating timed results.

**Stress test results** show the model's breaking point. The easy/medium/hard constraint payloads (4/7/10 requirements) let you plot the degradation curve. Most models pass at 4, start dropping at 7, and have a clear pattern at 10. The requirements most commonly dropped are security context, topology spread, and exact resource limits.

**Refusal boundary** is pass/fail only — no `quality_facts`, just `quality_forbidden`. Zero violations means the model refused or redirected. Any violation means it generated the dangerous config.

## Comparing results across time

Every result row embeds the environment it was captured in:

| Field | What it tracks |
|-------|---------------|
| `ollama_version` | Ollama release — upgrades often affect token speed and model behavior |
| `hostname` | Node where the sweep ran — useful if you move hardware |
| `git_commit` | Payload version — if you change a payload, old results used a different prompt |
| `git_dirty` | True if the repo had uncommitted changes when the sweep ran (results may not be reproducible) |

To compare two sweeps, diff the `ollama_version` and `git_commit` fields first. If either changed, performance differences may be due to the environment rather than the model.

**Stress test results** show the model's breaking point. The `stress_multi_constraint` payload has 10 requirements. Most models hit 8-9/10. The ones they drop reveal their weaknesses: security context fields, topology spread, and resource limits are the most commonly skipped.

## CI workflows

All run on `model-testing-runner`, an ARC runner co-located with Ollama on the homelab cluster.

| Workflow | Trigger | GPU | What it does |
|---|---|---|---|
| `model-sweep` | manual only | yes, hours | models.yaml × every payload. Runs **queue** (group `model-sweep`) and are never cancelled mid-flight: killing a sweep has wedged max-01. Auto-on-push was removed 2026-05-15. Artifacts kept 30 days; `run-*` dirs on the PVC pruned after 30. |
| `toolcall-health` | daily 14:30 UTC | **none** | `bin/toolcall-health.py`: real Open WebUI tool-call outcomes over 7 days, classed as unknown tool / bad args / tool-side error. Breach (>10% model-attributable failures, ≥10 calls) or an unreadable export opens one rolling homelab issue (label `toolcall-health`); a healthy window closes it. |
| `toolcall-probe-on-upgrade` | hourly version check | ~15 min, **only when the ollama version changes** | `bin/toolcall-regress.py` on the new version, compared with the previous version's result (kept in `toolcall-probe/` on the PVC). Shares the `model-sweep` queue. Any parser 500, unknown tool or KV bleed, or a model's ok count dropping by ≥2, opens a rolling homelab issue (label `toolcall-probe`). |
| `candidate-discovery` | weekly | pre-flight loads | finds new candidate models, opens a PR |

The two `toolcall-*` workflows are homelab#1205 AC5: the tool-call regression that went 0% → 100% failing on ollama 0.33 for four days with nothing detecting it. A red scheduled run notifies nobody here, so both raise a GitHub issue instead of relying on the run status.
