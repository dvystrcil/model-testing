# Reports index

Auto-curated catalog of all evaluation reports in this directory.
Newest first within each topic.

## task-model

Picks for OWUI's `TASK_MODEL` env (handles follow-up suggestions, title
generation, and similar small background classification calls).

- [2026-07-27 — health-event-router-triage](task-model/2026-07-27-health-event-router-triage/README.md)
  `llama3.2:1b` vs `lfm2.5-thinking:1.2b` on Health Event Router's exact
  one-line-classification prompt shape → **winner: llama3.2:1b** (both
  models answered wrong, but llama's wrong answer is caught by an existing
  production safety net while lfm2.5's isn't — and lfm2.5 took 70x longer).
  Overrides the general-sweep recommendation to swap to lfm2.5-thinking for
  this specific role. GHA run `30292609330`.
- [2026-05-15 — followup-formatting](task-model/2026-05-15-followup-formatting/README.md)
  `qwen3:1.7b` vs `llama3.2:3b` → **winner: llama3.2:3b** (qwen had per-token
  garbage output + a 5-min timeout; llama produced clean JSON)

## coder-models

Picks for the daily-driver coding model (opencode, dmf-coder, etc.).

- [2026-06-08 — gemma4-qat-swap-eval](coder-models/2026-06-08-gemma4-qat-swap-eval/README.md)
  `gemma4:26b-a4b-it-qat` (Ollama 0.30.6's new QAT MoE variant) evaluated
  as a `qwen3.6:35b` replacement. Three stages: capability + TPS smoke (3
  candidates), single-model sweep on the survivor, head-to-head vs the
  2026-06-03 baseline. Gemma matches qwen on TPS (53.5 tok/s each), beats
  it on three constraint-stress payloads (80→100, 86→100, 85→100), but
  **fails refusal_boundary by emitting `hostNetwork: true`, `runAsUser: 0`,
  `privileged: true`**. **Recommendation: role-dependent — keep qwen for
  direct K8s emission, consider gemma for dmf reasoner.** GHA run `27144695096`.
- [2026-06-03 — full-sweep](coder-models/2026-06-03-full-sweep/README.md)
  9 models × 15 payloads on Ollama 0.30.2 (canonical post-upgrade baseline)
  → primary winner `qwen3-coder-next:latest`, throughput winner `qwen3.6:35b`
  at ~56 TPS, 5 of 9 models clear both agentic payloads. GHA run
  `26863706501`. Re-validates 2026-05-13 ranking on a 3× larger lineup.
- [2026-05-13 — full-sweep](coder-models/2026-05-13-full-sweep/README.md)
  `qwen3-coder-next` vs `devstral:24b` vs `qwen2.5-coder:14b` × 14 payloads
  → quality winner devstral, throughput winner qwen3-coder-next, VRAM
  winner qwen2.5-coder:14b. GHA runs `25833326679`, `25833783544`,
  `25834314691`.
- [2026-05-13 — coder-candidates](coder-models/2026-05-13-coder-candidates/README.md)
  Quick in-cluster probes against 5 candidates → recommend keeping both
  qwen3-coder-next and qwen2.5-coder:14b for routing, or promoting 14b for
  lower wedge risk.

## security

Refusal-boundary and other security-shaped behaviors observed in sweeps.
Tracked separately from coder-models because the audience for "which model
do I pick" and "which models leak privilege escalation" is different.

- [2026-06-03 — privilege-escalation](security/2026-06-03-privilege-escalation/README.md)
  Baseline (no system_prompt mitigation) `refusal_boundary` results on
  Ollama 0.30.2 → 2 of 9 models (`gemma4:31b`, `qwen2.5:72b`) actively emit
  `allowPrivilegeEscalation: true` beyond what the prompt asked for. Other
  7 models comply but don't add extra hardening defeats. Companion to the
  2026-06-03 full-sweep; see RESEARCH.md Finding 4 for the with-mitigation
  side of the picture.

## kv-quantization

Investigations into KV cache quant settings, gfx1151 wedge behavior,
and adjacent hardware-specific tuning.

- [2026-05-13 — wedge-resolution](kv-quantization/2026-05-13-wedge-resolution/README.md)
  Corrected attribution: linux-firmware 2.27 was **not** the variable for
  the gfx1151 wedge improvement (zero amdgpu changes in its changelog).
  Likely cause: reboot after 6.7 days uptime. Next hypothesis:
  `amdgpu.cwsr_enable=0`.
- [2026-05-13 — q4_0-vs-q8_0](kv-quantization/2026-05-13-q4_0-vs-q8_0/README.md)
  KV cache q4_0 vs q8_0 is speed-indistinguishable on gfx1151 (±3% noise).
  Use q4_0 — same speed, frees ~5 GB VRAM headroom.

## How to query this index from an AI agent

Each report has a `meta.yaml` alongside its `README.md`. To find prior
evaluations of a specific model or topic without reading prose:

```bash
# All reports that touched qwen3:1.7b
find benchmarks/reports -name meta.yaml | \
  xargs grep -l "qwen3:1.7b"

# All TASK_MODEL picks ever
find benchmarks/reports/task-model -name meta.yaml | \
  xargs yq '.date + " " + .slug + " → " + .outcome.winner'

# All reports with a gfx1151-related outcome
find benchmarks/reports -name meta.yaml | \
  xargs grep -l "gfx1151"
```

When dvystrcil/homelab#98 (dmf-driven model-eval workflow) is built, it
will query this index before kicking off a new evaluation to avoid
redundant work.
