# model-testing

Benchmark suite for evaluating local LLMs as AI coding assistants in a homelab Kubernetes context. Models are tested against realistic tasks — YAML generation, schema adherence, instruction scope, and factual recall — and scored automatically. An AI analysis step summarizes results and recommends a winner.

## Why this exists

The homelab runs an AI coding assistant pipeline (`dual_model_filter`) that routes tasks between a fast model (coder) and a reasoning model. When a new model is released, it needs to pass these benchmarks before being promoted to production. The tests are designed to catch the real failure modes we've observed: hallucinated YAML fields, scope violations (touching files outside the task), and forgetting constraints under cognitive load.

## Repo layout

```
model-testing/
├── benchmarks/
│   ├── payloads/           # One JSON file per test scenario
│   ├── results/            # JSONL output from each sweep run
│   ├── run_benchmark.py    # Sweep runner — hits all models × all payloads
│   └── analyze.py          # AI-powered summary using qwen3.6:35b
├── tests/
│   └── test_benchmark.py   # Unit tests for grading logic
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
| `stress_multi_constraint` | Generates a K8s manifest satisfying 10 simultaneous requirements — finds which ones get dropped under cognitive load |

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

**Stress test results** show the model's breaking point. The `stress_multi_constraint` payload has 10 requirements. Most models hit 8-9/10. The ones they drop reveal their weaknesses: security context fields, topology spread, and resource limits are the most commonly skipped.

## CI workflow

The `model-sweep` workflow runs on a lightweight ARC runner (`model-testing-runner`) co-located with Ollama on the homelab cluster, so no egress traffic and no GPU sharing with cloud providers. A new run automatically cancels any in-progress run (concurrency group).

Trigger manually from the Actions tab, or it fires automatically on changes to `models.yaml` or any payload file.

Artifacts (JSONL results + markdown report) are retained for 30 days.
