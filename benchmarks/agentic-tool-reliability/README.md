# Agentic tool reliability

A small, repeatable end-to-end tool-use benchmark for local Ollama models. It complements (does not replace) `toolcall-regress.py`, the OWUI Tool Search benchmark, and passive production health monitoring.

## What it tests

The harness runs a simulated recipe-library tool server inside the benchmark process. It does not connect to Mealie, Kroger, or any production MCP service.

| Case | Expected behavior |
|---|---|
| `multi_step` | Search for a recipe, use its returned slug to fetch it, then report facts from the result |
| `error_recovery` | Receive a simulated transient 503 from recipe_get, retry the same request, and summarize the returned recipe |
| `result_grounding` | Search for sourdough, fetch the exact recipe, and report the 78% hydration value |
| `no_tool` | Answer a simple arithmetic question without calling a tool |

A case passes only if the exact expected tool sequence is emitted, the checked arguments are correct, the final answer contains required facts, and no transport/loop error occurred. Results include end-to-end latency and summed prompt/output token counts.

## Run

Ollama must be reachable and each model must already be pulled. The defaults include the models previously compared in this repository; override them as needed.

```bash
python3 bin/agentic-tool-reliability.py \
  --ollama http://localhost:11434 \
  --models qwen3.6:35b,qwen3.8:27B,nemotron-3.5-lightning:30b-a3b-q4_K_M,glm-4.7-flash \
  --runs 2 \
  --report benchmarks/reports/agentic-tool-reliability/$(date +%F)-initial
```

For a quick smoke test:

```bash
python3 bin/agentic-tool-reliability.py --models qwen3.6:35b --cases multi_step,error_recovery --runs 1
```

The report directory contains `results.jsonl` (one record per model/case/run) and `summary.md`. A nonzero model failure is a benchmark result, not a harness crash. The final marker is `AGENTIC-TOOL-RELIABILITY-COMPLETE`.

## Limits / interpretation

- This is a controlled tool-loop test against two small, synthetic function schemas, not a reproduction of Open WebUI's 100+ tool production surface. Use the existing OWUI Tool Search benchmark for that.
- Tool outputs are deterministic. The recovery scenario injects exactly one transient failure.
- Required final facts use case-insensitive substring checks. Review `final_answer` in JSONL for semantic correctness; substring checks are not a substitute for human review.
- A model may call a correct tool with a correct argument and still misunderstand the task. Inspect raw records before making a model-selection decision.
- Seeds and prompts are fixed; repeated runs help expose sampling variability but do not prove reliability for every prompt.
