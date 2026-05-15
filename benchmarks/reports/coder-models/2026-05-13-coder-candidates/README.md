# Smaller coder-model candidates vs qwen3-coder-next

Date: 2026-05-13
Trigger: looking for VRAM-lighter alternatives to qwen3-coder-next:latest (51 GB)
that triggers the gfx1151 wedge under multi-model load (`project_ollama_wedge_blocker`).

## Method

- In-cluster alpine probe pod, curl → Ollama service
- Two prompts: minimal ping + a non-trivial coding task (LRUCache class)
- temperature=0.1, num_gpu=-1
- Quality probe: response must contain `class LRUCache` or `def get` or `def put`
- Run after switching to `MAX_LOADED_MODELS=2` + `WARMUP_MODELS=qwen3.6:35b`

## Results

| Model | Size | Cold load | Gen t/s | Tokens | Wall-clock | Quality |
|---|---:|---:|---:|---:|---:|---|
| qwen3-coder-next:latest (BASELINE) | 51 GB | 8.7s | 37.1 | 154 | **4.15s** | ✓ |
| qwen2.5-coder:32b | 19.8 GB | 8.2s | 10.9 | 150 | 13.8s | ✓ |
| **qwen2.5-coder:14b** | **9 GB** | **3.4s** | **23.6** | **148** | **6.3s** | **✓** |
| qwen3.6-opencode:latest | 23 GB | 5.3s | 43.9 | 2451 | 55.8s | ✓ |
| qwen3.6:35b-a3b | 23 GB | 7.2s | 43.9 | 2580 | 58.8s | ✓ |

## Surprises

1. **qwen3.6 family generates 16x more tokens** — thinking blocks blow up wall-clock to 50-60s even though t/s is high (44). Not competitive for daily coding without thinking-mode suppression.
2. **qwen2.5-coder:32b is slow** at 10.9 t/s gen — almost 4x slower than qwen3-coder-next despite being 2.5x smaller. Flash-attention support may differ.
3. **qwen2.5-coder:14b is the standout** — 9 GB (5.5x smaller than baseline), 6.3s wall-clock (50% slower than baseline), valid output. The math: with baseline gone, we'd have ~100 GiB free for everything else.

## Recommendation

**Keep both loaded for routing.** The realistic path:

- **Daily-driver coding**: qwen3-coder-next:latest (4.2s, 51 GB) for serious tasks
- **Fast-tier fallback**: qwen2.5-coder:14b (6.3s, 9 GB) for quick code questions
- Together they use 60 GB which fits comfortably under the wedge threshold with MAX_LOADED=2

Or **single-model swap**: just promote qwen2.5-coder:14b as the daily driver. 50% slower per request but 5x smaller VRAM, freeing 42 GB for other models in concurrent flows. The wedge risk drops to near-zero.

Decision dependent on whether the user values:
- a. The 2-second latency advantage of qwen3-coder-next per chat turn → keep both, route by task
- b. Lower wedge risk + simpler memory budget → promote qwen2.5-coder:14b

## What this sweep DIDN'T test

- Multi-turn agentic tasks (the harness has `agentic_*` payloads I skipped for speed)
- Long-context performance (`stress_constraint_extreme`, `factual_recall`)
- Schema adherence under load (kubectl-validated payloads)

For the qwen2.5-coder:14b promotion decision, the full model-testing
benchmark suite should run before flipping the dmf+n8n preset. That's
~30 min and produces JSONL the analyze.py step can summarize.

---

## Update: devstral:24b added to cohort

Mistral's coding model, trained on SWE-bench / agentic tasks.

| Metric | qwen3-coder-next | qwen2.5-coder:14b | **devstral:24b** | qwen3.6:35b-a3b |
| --- | ---: | ---: | ---: | ---: |
| Size | 51 GB | 9 GB | **14 GB** | 23 GB |
| Cold load | 8.7s | 3.4s | **2.5s** | 7.2s |
| Prompt-eval (LRU) | 226 t/s | 602 t/s | **6,910 t/s** | 333 t/s |
| Gen t/s | 37.1 | 23.6 | 14.9 | 43.9 |
| Gen tokens | 154 | 148 | 153 | 2,580 |
| Wall-clock | **4.2s** | 6.3s | 10.5s | 58.8s |
| Thinking bloat | none | none | none | yes |
| Quality probe | ✓ | ✓ | ✓ | ✓ |

**What's unique about devstral:**

- **No thinking-block bloat** — like the qwen2.5-coder family, generates direct code
- **Highest prompt-eval speed in the cohort** (6,910 t/s) — wins on long-context inputs
- **Slowest generation** of the non-thinking models (14.9 t/s) — that's the trade
- **Agentic specialty** — trained on SWE-bench. This sweep doesn't test that surface.

The 6,910 t/s prompt-eval speed is genuinely interesting. For a chat where the input is a long codebase context (10K+ tokens) and the output is a focused patch (200-500 tokens), devstral could end up FASTER end-to-end than qwen3-coder-next even with slower gen. Math: 10K-token input → qwen3-coder-next pays 10K/226 = 44s prompt-eval; devstral pays 10K/6910 = 1.4s. That's a 42s win on long-context that swamps the 6s gen-speed loss.

## What I'd run next

Full model-testing sweep on devstral:24b + qwen2.5-coder:14b against the
agentic payloads to validate quality. Both are candidate replacements
for qwen3-coder-next:latest in the dmf+n8n preset. Decision rule:

1. **If devstral passes the agentic stress payloads** → promote devstral
   as the daily-driver coder model
2. **If devstral fails but qwen2.5-coder:14b passes** → promote qwen2.5-coder:14b
3. **If both fail on agentic** → keep qwen3-coder-next, file an issue
   to revisit when devstral-medium or a smaller coder-next variant ships
