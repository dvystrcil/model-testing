# KV cache quantization: q4_0 vs q8_0 — speed comparison

Date: 2026-05-13
Hardware: gfx1151 (Strix Halo) ROCm
Ollama: 0.30.0-rc12 (homelab rebuild)
Test method: in-cluster alpine probe pod → ollama Service (eliminates port-forward variability)
Models probed: qwen3-coder-next:latest (Q4_K_M weights), qwen3.6:35b

## Raw

| Model | Case | KV setting | P-eval t/s | Gen t/s | Gen tokens | Gen wall |
|---|---|---|---:|---:|---:|---:|
| qwen3-coder-next | short (3 tok output) | q4_0 | 152.7 | 52.8 | 3 | 0.06s |
| qwen3-coder-next | short (3 tok output) | q8_0 | 151.3 | 51.6 | 3 | 0.06s |
| qwen3-coder-next | code gen | q4_0 | 231.6 | 35.9 | 260 | 7.23s |
| qwen3-coder-next | code gen | q8_0 | 229.1 | 36.3 | 193 | 5.32s |
| qwen3.6:35b | short | q4_0 | 210.5 | 45.4 | 142 | 3.13s |
| qwen3.6:35b | short | q8_0 | 209.7 | 45.6 | 204 | 4.47s |
| qwen3.6:35b | code gen | q4_0 | 351.1 | 43.7 | 2714 | 62.15s |
| qwen3.6:35b | code gen | q8_0 | 354.3 | 44.9 | 3676 | 81.84s |

## Conclusion

q4_0 vs q8_0 KV cache quantization makes essentially no measurable
difference in generation speed on gfx1151. Every delta is within ±3%
which is well within run-to-run noise.

**The KV quant trade-off is memory, not speed**: q4_0 saves ~5 GB VRAM
vs q8_0 (verified empirically 2026-05-12). Speed cost is zero on this
hardware. The right choice for our setup is q4_0 — same speed, free
VRAM headroom for multi-model loading.

## What this DOESN'T tell us

These were warm-pod tests with short-to-medium prompts (≤200 input
tokens). KV quant matters more as context grows because the KV cache
is per-token-per-head. To see a real difference we'd need to probe at
real-world context lengths (qwen3-coder-next sees p95=242K).

If "models feel slower" persists despite the KV match, the cost is
elsewhere — see the dmf+n8n small-model-pre-pass latency probe below
or the open Ollama wedge / VRAM pressure tracking.

## References

- `feedback_sqlite_to_pgo_default` — adjacent: when to optimize memory vs latency
- `project_qwen3_coder_next_sizing` — context-length decisions
- `project_ollama_wedge_blocker` — separate concern, same hardware

---

## Side investigation: the actual cause of perceived slowness

User reported "models running slower" after yesterday's KV q8_0 → q4_0
change. The data above shows KV quant isn't the speed cost. While probing
the qwen3:0.6b sidecar (dmf+n8n's small classification model) for
additional latency overhead, I hit something more interesting:

**Repeated `q4 → q8 → q4` flips with concurrent multi-model load
triggered the Ollama wedge.**

Symptom (verified end-to-end on 2026-05-13 ~22:48 UTC):

| Endpoint | Latency |
| --- | --- |
| `GET /api/tags` | 1-2 ms (healthy) |
| `POST /api/chat` (any model) | 30+ s timeout, no body |

This is exactly the `project_ollama_wedge_blocker` pattern. The wedge
clears on rolling-restart (1-2 min cycle including warmup) and stays
clear until the next stressor.

**Likely real source of "slower" feeling:** the wedge is engaging
silently and intermittently in normal dmf+n8n use (model swaps under
VRAM pressure are exactly the stressor that wedges gfx1151 ROCm).
Chats sit on /api/chat for 30s+ until the timeout. Restarting Ollama
brings speed "back" — but it's not the speed that changed, it's the
wedge that broke.

The KV quant change (q8 → q4) yesterday was speed-neutral, but it
WAS one less GB of pressure on VRAM. So if anything it should help
the wedge, not hurt.

## Recommendation

1. **Keep KV cache at q4_0** — speed-neutral, ~5 GB VRAM win
2. **Run the qwen3:0.6b sidecar latency probe** when the cluster is
   not just-restarted — current probe was unreliable because the wedge
   engaged mid-probe; recipe in `bin/tests/probe-ollama-latency.sh`
   (TBD — file as follow-up)
3. **Track wedge frequency** — if the wedge is engaging silently more
   often than the user notices, it's the dominant performance
   bottleneck. See `project_ollama_wedge_mode` for the three-tier probe
   that should already be telling us this.

## Probe script that hit the wedge mid-run

The probe (alpine pod, curl) tried:
- 3 warm `/api/chat` calls to qwen3:0.6b
- 2 warm `/api/chat` calls to qwen3.6:35b
- Simulated dmf turn (0.6b classify + 35b answer)

The first two qwen3:0.6b calls returned empty/timeout. /api/tags
continued answering. Classic wedge fingerprint.

---

## Update: wedge can engage on SINGLE model load too

2026-05-13 ~18:18 UTC: ran the new `bin/tests/probe-ollama-latency.sh`
on a freshly-restarted Ollama (qwen3.6:35b warmup-loaded, qwen3:0.6b
not yet loaded, MAX_LOADED=2).

First `/api/chat` to qwen3:0.6b — which would just LOAD the model into
the unused slot — hit the wedge:
  HTTP:000 time:60.002s  (curl exit 28, model never responded)

So the trigger isn't multi-model VRAM pressure specifically. It's
**any model-load operation under certain conditions on gfx1151**.

The configmap changes shipped this session (MAX_LOADED=2,
warmup=qwen3.6:35b) reduce frequency but don't eliminate. The wedge is
fundamentally a model-load-path bug, not a VRAM-budget bug. That
changes the upstream issue framing in homelab#78.

Recovery: still `kubectl rollout restart deployment/ollama`. ~2 min cycle.
