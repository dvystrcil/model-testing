# Wedge investigation — outcome of 2026-05-13 investigation

## Final attribution (HONEST — corrected after external review)

The Ollama wedge on gfx1151 was NOT caused by:
- KV cache quantization (q4_0 vs q8_0 — both indistinguishable on speed/wedge)
- Runtime HSA_XNACK env var (=0 vs =1 — both wedged identically)
- Kernel-level XNACK retry (`amdgpu.noretry=1` test — still wedged)
- Multi-model VRAM pressure as primary trigger (single model load triggered it)

The wedge severity DID improve dramatically after today's reboots
(from "permanent — pod restart required" to "transient — 5-min
Ollama-side queue timeout self-clears"). My initial attribution to
the `linux-firmware` 2.26 → 2.27 upgrade was **WRONG on inspection**:

- 2.27 changelog has ZERO amdgpu changes (Cirrus audio + Intel
  BT/WiFi/IPU7 only). Verified by `apt changelog linux-firmware`.
- `ubuntu-drivers-common` 3.7 changelog: AMD+Nvidia boot_vga fix
  only — irrelevant (no Nvidia in box).
- gfx1151 firmware blobs (`gc_11_5_*`, `sdma_6_1_1`, `psp_14_0_4`,
  `vcn_4_0_5`) carry mtime `Apr 15 14:30` on disk — they did NOT
  change in 2.27.

**The likely variable**: the node had been up 6.7 days continuously
before today. Today's reboots cleared accumulated kernel state.
This is consistent with the "reboot fixes ROCm wedges" folklore
for Strix Halo. The improvement is probably NOT stable over a
multi-day uptime window — worth re-testing wedge engagement at
3 / 7 / 14 day uptimes to see if it returns.

The leading next-hypothesis (from external reviewer): test
**`amdgpu.cwsr_enable=0`** (disable wave compute save/restore).
Per [ROCm #5590](https://github.com/ROCm/ROCm/issues/5590) and
multiple Strix Halo developer reports, the residual qwen3:0.6b
cold-load wedge signature (long-running compute kernel, cold path,
intermittent, recovers on timeout) fits the CWSR-bug signature
exactly. Clean single-variable kernel-cmdline test via the existing
`ansible/playbooks/gpu-xnack-kernel-test.yml` pattern.

## Observed today

| Aspect | Pre-investigation (uptime 6.7d) | Post-2-reboots |
| --- | --- | --- |
| Wedge trigger | Various model-load operations | Specifically `qwen3:0.6b` cold-load (first 1-2 attempts) |
| Wedge severity | Permanent — pod restart required | Transient — Ollama's 5-min queue timeout clears |
| Wedge after-effect | Cluster stuck until manual intervention | Subsequent loads work normally |
| 51 GB qwen3-coder-next cold-load | Always wedged today | **Loaded in 10.6s** |
| Operator burden | Multiple rollout-restarts/day | Wait 5 min, system clears itself |

## What we don't know yet

- Whether the improvement persists past ~7 days uptime
- Whether `amdgpu.cwsr_enable=0` eliminates the residual qwen3:0.6b
  wedge (testable in one reboot)
- Whether HSA_XNACK=0 is still the right runtime setting now that
  the wedge-attribution narrative has shifted (the 2026-05-11 A/B
  data was sidecar-contaminated; a clean re-run would resolve)

## How we got here

1. Investigated KV cache quant → no effect on wedge
2. Investigated process-level HSA_XNACK → no effect on wedge
3. Discovered `amdgpu.xnack=1` was silently rejected by the kernel
   (`dmesg: amdgpu: unknown parameter 'xnack' ignored`) — the doc was wrong
4. Investigated kernel-level `amdgpu.noretry=1` via node reboot →
   wedge still engaged → XNACK ruled out at every layer
5. While the node was already drained, ran `apt update` — found
   `linux-firmware` had an update available (GPU firmware blobs)
6. Upgraded `linux-firmware` + `ubuntu-drivers-common`, rolled back
   the `amdgpu.noretry=1` test, rebooted to a clean kernel cmdline
7. Wedge behavior changed dramatically: from permanent (requires
   pod restart) to transient (self-clears in 5 min)

## What's still open

- The qwen3:0.6b cold-load specifically still triggers the 5-min
  wedge sequence. Other models load cleanly. Need to figure out
  whether it's a qwen3:0.6b-quirk or a "first cold load after pod
  start" thing.
- The `linux-firmware` 0ubuntu2.27 changelog should tell us what
  AMD firmware blob actually changed. If it was a Strix Halo /
  gfx1151 specific fix, that's actionable for upstream tracking.
- Eventually swap the dmf+n8n small-model from qwen3:0.6b to
  something else and retest.

## Action items shipped this investigation

- `linux-firmware` 2.26 → 2.27 + `ubuntu-drivers-common` upgrade on max-01
- Kernel cmdline cleanup: removed dead `amdgpu.xnack=1`, removed test
  `amdgpu.noretry=1`. Now: `cgroup_enable=memory swapaccount=1`
- `homelab/architecture/gpu.md` corrected (PR #81)
- `ansible/playbooks/gpu-xnack-kernel-test.yml` committed for future
  XNACK-related experiments
- Memory rule `feedback_verify_kernel_param_name` added
- 5 follow-up issues open (homelab#76, #77, #78; model-testing#22, #23)

## Lesson

The 2026-05-11 diary called the wedge "ruled out as XNACK" because the
sidecar fix made symptoms stop. The actual XNACK ruling-out happened
today, two days later, after a clean experiment. **Convenient
conclusions (the bug went away when I fixed the sidecar; therefore the
sidecar was the only bug) survive longer than rigorous ones.**
