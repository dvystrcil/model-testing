# Reports index

Auto-curated catalog of all evaluation reports in this directory.
Newest first within each topic.

## task-model

Picks for OWUI's `TASK_MODEL` env (handles follow-up suggestions, title
generation, and similar small background classification calls).

- [2026-05-15 — followup-formatting](task-model/2026-05-15-followup-formatting/README.md)
  `qwen3:1.7b` vs `llama3.2:3b` → **winner: llama3.2:3b** (qwen had per-token
  garbage output + a 5-min timeout; llama produced clean JSON)

## coder-models

Picks for the daily-driver coding model (opencode, dmf-coder, etc.).

- [2026-05-13 — full-sweep](coder-models/2026-05-13-full-sweep/README.md)
  `qwen3-coder-next` vs `devstral:24b` vs `qwen2.5-coder:14b` × 14 payloads
  → quality winner devstral, throughput winner qwen3-coder-next, VRAM
  winner qwen2.5-coder:14b. GHA runs `25833326679`, `25833783544`,
  `25834314691`.
- [2026-05-13 — coder-candidates](coder-models/2026-05-13-coder-candidates/README.md)
  Quick in-cluster probes against 5 candidates → recommend keeping both
  qwen3-coder-next and qwen2.5-coder:14b for routing, or promoting 14b for
  lower wedge risk.

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
