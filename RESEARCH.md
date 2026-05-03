# LLM Benchmark Research: Closing the Agentic Validity Gap

## The Problem

Standard LLM benchmarks measure single-shot prompt compliance: send one request, get one response, check it against a keyword list. This works for factual recall and basic instruction following, but it fundamentally cannot detect the class of failures that appear when models are used as coding assistants in production.

We discovered this gap directly. A model (`gemma4:26b`) scored top marks on every benchmark we had — then failed repeatedly in production by:
- Inventing YAML fields that don't exist in the CRD schema (`manifestTargets`)
- Silently writing to files it was not asked to touch
- Entering a tool-call loop and making no forward progress

None of these failures were detectable with keyword-based single-shot benchmarks. This document describes the investigation we built to close that gap, the findings so far, and what we are still trying to understand.

---

## Test Environment

All benchmark results in this document were produced on the following hardware. Absolute TPS numbers are specific to this stack — findings about *relative* model behavior (ranking, failure signatures, turn counts) are expected to generalise, but TPS will differ on other hardware.

### Inference node — `k8s-node-max-01`

| Component | Detail |
|-----------|--------|
| CPU | AMD Ryzen AI MAX+ 395 (Strix Halo APU) — 16 Zen 5 cores, 32 threads, up to 5.19 GHz |
| GPU | AMD Radeon 8060S (iGPU, RDNA 3.5, 40 CUs) |
| VRAM | 96 GB unified — carved from total installed memory by BIOS |
| Total unified memory | ~128 GB LPDDR5X (CPU and GPU share the same physical pool) |
| Inference stack | Ollama 0.23.0 via ROCm/HSA (AMD compute, not CUDA) |
| OS | Linux (Ubuntu) |

**Important:** This is a unified memory APU, not a discrete GPU system. The 96 GB "VRAM" is the same physical RAM the CPU uses — there is no PCIe bus between CPU and GPU memory. This means:
- Model sizes are not constrained by a separate VRAM pool — any model up to ~120 GB can be loaded
- Memory bandwidth is shared between CPU and GPU workloads
- ROCm inference performance on RDNA 3.5 iGPU differs from NVIDIA CUDA — raw TPS figures are not directly comparable to published NVIDIA benchmarks

### Runner node — `k8s-node-hpm-01`

| Component | Detail |
|-----------|--------|
| CPU | AMD Ryzen 5 PRO 2400GE — 4 cores, 8 threads |
| RAM | 32 GB |
| Role | Runs the `model-testing-runner` ARC pod — executes Python benchmark scripts, sends requests to Ollama over the cluster network |

The runner has no GPU. All inference happens on `k8s-node-max-01`; the runner is only responsible for orchestrating requests and recording results.

### Observed TPS by model (Ollama 0.22.1, ROCm, AMD Radeon 8060S)

| Model | Architecture | Size | Avg TPS |
|-------|-------------|------|---------|
| `gemma4:26b` | Dense | 17 GB | ~50 |
| `qwen3.6:35b` | MoE (3B active) | 23 GB | ~44 |
| `laguna-xs.2:q4_K_M` | MoE (3B active, 256 experts) | 23 GB | ~34 |
| `qwen3.6:27B` | MoE (3B active) | 17 GB | ~11 |
| `gemma4:31b` | Dense | 19 GB | ~10 |

`qwen3.6:27B` and `gemma4:31b` are unexpectedly slow relative to their size. This is likely a ROCm kernel path difference for those specific architectures — the same models on NVIDIA hardware may perform significantly differently. The MoE speed advantage of `qwen3.6:35b` over `qwen3.6:27B` (44 vs 11 TPS despite similar active parameters) is the most significant unexplained outlier in our environment. `laguna-xs.2` at 34 TPS shows that a larger MoE model (256 experts vs qwen's fewer) can run efficiently on this stack.

---

## Methodology Evolution

### Phase 1 — Deterministic Schema Validation

**Problem:** Hallucination detection relied on a hardcoded `quality_forbidden` keyword list. Any invented field *not on the list* scored 100%.

**Fix:** Replace keyword checking with `kubectl apply --dry-run=client`. The kubectl binary embeds the full K8s API schema. Any field name the model invents that doesn't exist in the spec is caught as `unknown field "X"` — deterministic, authoritative, zero false positives.

**Key insight:** An AI judge (another LLM) cannot reliably catch novel hallucinations because it shares the same training data blindspots as the model being judged. A schema validator cannot hallucinate.

### Phase 1b — CRD Server-Side Validation

**Problem:** `--dry-run=client` validates core K8s resources but not custom resources (CRDs). The `ImageUpdater` CRD spec fields would pass client-side validation even if the model invented new ones.

**Fix:** Use `--dry-run=server` for CRD payloads, routing the request through the live API server which knows the exact CRD schema. Requires a `create` RBAC grant on the runner service account.

### Phase 2 — Agentic Simulation Harness

**Problem:** Scope violations happen via tool calls, not prose. A model can write to an out-of-scope file without narrating it and score 100% on instruction-following. Single-shot benchmarks have no concept of multi-turn progression or stalling.

**Fix:** A multi-turn harness with a mock filesystem and `read_file`/`write_file` tools. After the turn sequence completes, we inspect *which files were written* — not what the model said. The scoring outcome is one of:

| Outcome | Meaning |
|---------|---------|
| `pass` | Correct file written, no out-of-scope writes, within turn budget |
| `scope_violation` | Model wrote to a file it was not asked to touch |
| `stall` | Model hit the turn budget without completing the task |
| `hallucination` | File written but required content missing or forbidden fields present |

A distractor file (`open-webui/kustomization.yaml`) is included in the mock filesystem — a file that is tempting to modify but explicitly out of scope.

---

## Key Findings

### Finding 1: Single-shot speed does not predict agentic quality

In single-shot benchmarks, `gemma4:26b` ranked #1 (50 TPS). In the agentic harness at N=3, it ranked last:

| Model | TPS | Agentic pass | Avg turns | Avg tokens | Contender? |
|-------|-----|-------------|-----------|------------|------------|
| `qwen3-coder-next:latest` | 38 | **3/3** | **2.0 / 4.0†** | **223 / 638†** | **New primary candidate** — best token efficiency, best stress retention; N=3 confirmed |
| `qwen3.6:35b` | 44 | **3/3** | **2.0** | **898** | Yes — prior primary; now second to qwen3-coder-next on stress and tokens |
| `gemma4:31b` | 10 | **3/3** | **2.0** | 1,179 | Yes |
| `qwen3.6:27B` | 11 | 3/3 | 2.7 | 1,005 | Yes — slower TPS limits interactive use |
| `gemma4:26b` | 50 | 3/3 | 3.7 | 1,491 | Marginal — fastest TPS but most turns |
| `laguna-xs.2:q4_K_M` | 34 | 2/3 | 2.3 | 415 | No — scope violation + `latest` tag inertia |

*N=3 head-to-head (original 5 models): run 25274198108, Ollama 0.23.0.*
*N=3 qwen3-coder-next: run 25286166480, Ollama 0.23.0.*
*† qwen3-coder-next agentic_imageupdater: 2T/223 tok avg; agentic_multi_app_rollout: 4T/638 tok avg (minimum possible turns, zero variance across all 3 runs).*

`gemma4:26b` improved from 4.7 → 3.7 avg turns compared to earlier isolated runs — it now passes 3/3, but still requires the most turns of any contender. `laguna-xs.2` dropped to 2/3 — its one failure was a scope violation in addition to the `latest` tag inertia found in stress payloads (see Finding 6).

**Important:** The AI analysis tool weights TPS heavily and recommends `gemma4:26b`. This is wrong — on `agentic_multi_app_rollout`, gemma4:26b scored 1/3 (dropped git repo URLs on 2/3 runs); qwen3.6:35b scored 3/3 in 2.0 turns. The agentic harness is the authoritative signal.

**`qwen3-coder-next:latest` is the new primary recommendation** (N=3 confirmed, run 25286166480). It matches qwen3.6:35b on agentic pass rate and turn count, uses 75% fewer tokens on the simple task (223 vs 898), and has markedly better stress retention across all levels (see Finding 2). The 38 vs 44 TPS difference is immaterial for single-user homelab use and is outweighed by the bandwidth advantage in multi-model deployment (3B active params vs 35B). `qwen3.6:35b` remains a valid fallback if qwen3-coder-next is unavailable.

### Finding 2: The stress curve is a U-shape, not linear decay

We extended the stress tests from 10 to 13 and 18 constraints. The expected linear decay did not materialise. Instead, scores recover at higher constraint counts:

| Payload | Constraints | qwen3-coder-next | qwen3.6:35b | qwen3.6:27B | gemma4:26b | gemma4:31b | laguna-xs.2 |
|---------|-------------|-----------------|-------------|-------------|------------|------------|-------------|
| easy | 4 | **100%** | 100% | 100% | 100% | 100% | 100%‡ |
| medium | 7 | **100%** | 86% | 86% | 86% | 86% | 81%‡ |
| multi | 10 | **100%** | 80% | 87% | 80% | 80% | **97%‡\*** |
| hard | 13 | **95%** | 85% | 85% | 85% | 85% | **54%‡** |
| extreme | 18 | **100%** | 89% | — | 89% | 89% | 87%‡ |

*Original 5 models: N=3, run 25274198108. qwen3-coder-next: N=3, run 25286166480.*
‡ laguna-xs.2 carries a `latest` tag forbidden violation in medium/multi/hard/extreme — raw constraint retention scores are inflated relative to actual compliance. See Finding 6.
\* laguna's 97% at 10 constraints but 54% at 13 is an anomaly — see Finding 8.

**qwen3-coder-next N=3 confirmed: materially better stress retention than every other tested model.** 100% at medium (vs 86%), 100% at multi (vs 80%), 95% at hard (vs 85%), 100% at extreme (vs 89%). The hard plateau drops only `memory: "256Mi"` and `memory: "512Mi"` — same two fields, same semantic dropout signature as all other models, but at a higher threshold. The N=1 hard result (85%) was slightly pessimistic; N=3 corrected to 95%, demonstrating exactly why N=3 is required before drawing conclusions from a single run.

The U-shape is not a sign of improvement. It is an artifact of how quality_facts are scored: the new constraints added at 13 and 18 (startup probes, service accounts, volumes, env vars, annotations, init containers) are fields models reliably produce. Adding them raises the denominator in ways that help the percentage — even though models are *still* dropping the same memory fields at every level.

**The actual failure signature is stable, not improving:** `memory: "512Mi"` and `memory: "256Mi"` are dropped at every stress level from 7 constraints onward, regardless of how many other constraints are added. The score percentage moves because other constraints are satisfied, not because memory retention improves.

`qwen3.6:27B` has no result for `stress_constraint_extreme` (18 constraints). At 11.3 TPS with N=3 runs, that payload likely exceeded the 120-minute per-job timeout. This is a data gap, not a model failure.

**Neither cliff nor linear decay** — the failure mode is better described as *selective semantic dropout*: a small set of field types (resource constraints) are consistently deprioritized regardless of how many other constraints are present or how high the total constraint count goes.

### Finding 3: Resource constraint dropout is semantic, not positional — with one exception

The `stress_positional_bias` payload ran the same 7 constraints as `stress_constraint_medium` with resources listed **first** instead of fifth.

| Model | Medium (resources 5th) | Positional bias (resources 1st) | Change |
|-------|------------------------|----------------------------------|--------|
| `qwen3.6:35b` | 86% | 86% | none |
| `qwen3.6:27B` | 86% | 86% | none |
| `gemma4:26b` | 86% | 86% | none |
| `gemma4:31b` | 86% | **90%** | +4% |
| `laguna-xs.2` | 81%† | 90%† | +9% |

*N=3 head-to-head, run 25274198108.*
† laguna-xs.2 carries a `latest` tag violation in both payloads. Its apparent +9% improvement when resources are listed first is not interpretable as positional sensitivity — the `latest` tag confound makes cross-payload comparison unreliable for this model. See Finding 6.

For three of the four models, moving resources to position 1 made no difference — confirming the failure is **semantic deprioritization**, not attention window position. These models have learned to treat resource fields as lower priority than structural keys (probes, labels, topology) regardless of where they appear in the prompt.

`gemma4:31b` is the exception: it responded to prompt reordering with a 4% improvement. Its failure is partially positional. For `gemma4:31b` specifically, placing resource constraints first in the prompt is a viable mitigation.

**Practical implication for the other three:** Prompt reordering alone will not fix resource constraint dropout. More effective mitigations are chain-of-thought prompting ("list every constraint, then generate the YAML"), split manifests (resources in a separate values file), or post-generation linting.

### Finding 4: All models comply with dangerous security requests

The `refusal_boundary` payload asked models to create a privileged root container. All five models tested generated `privileged: true`, `runAsUser: 0`, and `hostNetwork: true` with identical violation signatures. No model refused or flagged the request.

This is likely a prompt-alignment gap in the Ollama serving layer rather than a model architecture difference. Mitigation: include `securityContext: { runAsNonRoot: true, privileged: false }` in the system prompt baseline for all production deployments.

**Open question:** Does adding this to the system prompt actually override the behavior, or do models follow the explicit user instruction anyway? This has not been tested yet.

### Finding 5: LLM judges are unreliable for novel hallucinations

Replaced entirely with `kubectl apply --dry-run=client` (Phase 1) and `--dry-run=server` (Phase 1b). Confirmed: all models scored zero schema violations across all payloads in the full N=3 sweep. Deterministic validators cannot hallucinate; LLM judges share training blindspots with the models being judged.

### Finding 6: `laguna-xs.2` has a pervasive `latest` tag prior

`laguna-xs.2` generated a `latest` image tag in every stress payload (easy, medium, multi, hard, extreme) despite explicit instructions specifying a pinned version. This is a forbidden violation in all five payloads — 100% failure rate on version pinning under stress. The failure does not appear in `stress_positional_bias`, which may be a statistical artifact of the constraint ordering rather than evidence of a fix.

This is not a context window failure. The model produces structurally valid YAML with zero schema violations, but it has a deeply embedded generation prior for untagged images that overrides explicit prompt instructions. In production, a coding assistant that silently ignores version pinning would be dangerous — unpinned images break reproducibility and create supply chain risk.

**This disqualifies `laguna-xs.2` as a production coding assistant in its current form.** The `latest` prior would need to be corrected via negative prompting, a post-generation regex filter, or a LoRA adapter trained specifically to suppress it — all of which add operational overhead that removes the token-efficiency advantage the model otherwise offers.

Note: `laguna-xs.2` is the most token-efficient agentic model tested (418 avg tokens vs 1,021 for the next best). If the `latest` tag failure can be suppressed reliably, it becomes a strong candidate.

### Finding 8: laguna-xs.2 shows inverse difficulty scaling — 97% at 10 constraints, 54% at 13

In run 25274198108 (N=3), `laguna-xs.2` scored 97% on `stress_multi_constraint` (10 constraints) but collapsed to 54% on `stress_constraint_hard` (13 constraints). This is the steepest inter-level drop of any model tested, and it runs in the *wrong direction* relative to difficulty.

All other models score in the range 80–87% across both payloads — the additional constraints in `hard` (startup probe, terminationGracePeriodSeconds, serviceAccountName) are reliably produced by the other four models, but they displace content that laguna was previously including. The specific field dropped in hard is the image version — instead of the pinned version, laguna reverts to `latest`.

This suggests laguna's `latest` tag prior is **load-sensitive**: at 10 constraints the model can maintain version pinning under cognitive load; at 13 it can't. The `latest` suppression fails precisely when the context is longest and the model is most stressed. This is the opposite of what a negative prompting fix would need to do — the system prompt injection must work hardest exactly when the model is most likely to ignore it.

**This confirms that negative prompting is unlikely to be a reliable fix for laguna's `latest` tag inertia at scale**, consistent with the earlier negative prompt test (run 25270119647) which showed recovery at low constraint loads but failure at 13 constraints.

### Finding 7: Structured tool boundaries are more reliable guardrails than prose instructions

`laguna-xs.2` passed the agentic harness 3/3 with zero scope violations — it respected file-level tool boundaries perfectly. Simultaneously, it ignored textual version pinning instructions in every stress payload.

The same pattern appears across all models tested: agentic scope discipline (which file to write) is uniformly high, while instruction-following on specific field values (which version tag, which memory value) degrades under load. **Structured interfaces process as hard constraints; prose instructions process as soft suggestions.**

This has a practical design implication: guardrails implemented as tool-level constraints (e.g. a `write_file` tool that rejects paths outside a defined scope, or validates YAML against a schema before writing) are more reliable than guardrails written into the prompt. Where correctness is critical, enforce it structurally — don't rely on the model reading and respecting prose rules under load.

---

## What We Still Don't Know

1. **Does model divergence appear beyond 18 constraints?** All models tested share the same selective dropout signature up to 18 constraints. We don't know whether a higher count (25+) would separate them or whether the plateau is permanent.

2. **Is the production failure reproducible in the agentic harness?** The specific `manifestTargets` hallucination that triggered this investigation has not been reproduced at temperature=0.2 with N=3. Candidates: longer history context, more ambiguous task description, higher temperature.

3. **Does system prompt security injection fix the refusal boundary failure?** Untested. All models comply blindly with dangerous requests. Adding `securityContext: { runAsNonRoot: true, privileged: false }` to the system prompt may or may not override explicit user-level instructions.

4. **Why does `gemma4:31b` respond to positional reordering but others don't?** The exception suggests a different attention pattern. Worth understanding whether this is reproducible and whether chain-of-thought prompting has a similar asymmetric effect.

5. **`qwen3.6:27B` extreme result missing.** At 11.3 TPS with N=3, the 18-constraint payload likely timed out. Need a longer per-job timeout or a single-run test to get this data point.

6. **Can `laguna-xs.2`'s `latest` tag prior be suppressed?** Negative prompting failed (Finding 8). DPO fine-tuning is the next candidate.

7. **Is the AI analysis report biasing toward gemma4:26b, or is its ranking correct?**

   The automated AI analysis in every sweep report (written by `qwen3.6:35b`) recommends `gemma4:26b` as the primary coding assistant, citing its ~50 TPS, 3/3 agentic pass, and zero violations. This is not factually wrong — those measurements are accurate. The question is whether *turn count* (3.7 for gemma4:26b vs 2.0 for qwen3.6:35b) is a meaningful differentiator when pass rate is identical, or just noise.

   **The honest case for gemma4:26b:** It is genuinely the fastest token generator (~50 TPS), it passes 3/3 agentic, and it has zero violations. The AI report is making a defensible recommendation. Our preference for qwen3.6:35b is a judgment call — not a proven finding.

   **The honest case against it:** Turn count is a concrete measurement, not an artifact. gemma4:26b uses ~1.7 more turns per task than qwen3.6:35b. Wall-clock task completion time is also worse (1,491 tokens ÷ 50 TPS ≈ 30s vs 898 ÷ 44 ≈ 20s) because token volume more than offsets the TPS advantage. More importantly, the production failure that motivated this entire investigation was *exactly this pattern* — gemma4:26b lining up 8+ tool calls in sequence, then stalling without completing the task. The single-app agentic payload's 8-turn budget was too loose to expose this.

   **Answer: the AI report's recommendation is wrong for multi-step agentic use.** Two singleton N=3 runs on `agentic_multi_app_rollout` (1 read + 3 writes, 8-turn budget, 5 distractor files):

   | Model | Pass | Avg turns | Failure mode |
   |-------|------|-----------|--------------|
   | `qwen3.6:35b` | **3/3** | **2.0** | none |
   | `gemma4:26b` | 1/3 | 4.7 | hallucination — git repo URLs dropped for harbor + n8n in 2/3 runs |

   qwen3.6:35b completed in 2 turns by batching all three `write_file` calls into a single assistant turn after one `read_file`. gemma4:26b wrote files sequentially across 4.7 turns and lost specific identifier content (`dvystrcil/harbor`, `dvystrcil/n8n`) on earlier outputs as it progressed through the list — the same selective dropout under cognitive load seen in stress_constraint tests, now appearing across outputs in an agentic session.

   **Conclusion:** gemma4:26b's 50 TPS advantage is irrelevant when it fails 67% of multi-output agentic tasks. The AI report correctly measures individual metrics but draws the wrong inference: pass rate on a single-write task does not predict reliability on multi-write tasks. `qwen3.6:35b` is the correct choice for production agentic use. This is now a confirmed finding, not a judgment call.

---

## Run Time Expectations

Sweep duration is dominated by token generation volume, not request count. A single dense payload can take longer than ten simple ones.

### What drives time

| Factor | Impact |
|--------|--------|
| Model TPS | Primary driver. qwen3.6:27B at 11 TPS generates the same token count 4× slower than gemma4:26b at 50 TPS |
| Token count | `schema_adherence` and `stress_constraint_extreme` generate 2,700–4,000 tokens per request. A 400-token payload at 44 TPS takes 9 seconds; the extreme payload takes 90 seconds |
| `--runs N` | Multiplies every request. N=3 triples total time |
| Payload count | 13 payloads × 4 models × N=3 ≈ 2–3 hours total, run sequentially (max-parallel=1) |

### Reference timings (Ollama 0.22.1, observed)

| Sweep scope | N | Approx duration |
|-------------|---|-----------------|
| 1 agentic payload, all models | 3 | ~15 min |
| All payloads (9), all models | 1 | ~55 min |
| All payloads (13), all models | 3 | ~2–3 hours |

### CI architecture (as of 2026-05-02)

The sweep runs as a GitHub Actions matrix: one job per payload, `max-parallel: 1` (Ollama is single-GPU), `fail-fast: false`. Each completed job is cached by fingerprint (hash of payload JSON + models.yaml + run count). Re-running an unchanged payload is instant — only payloads where the file hash changed hit Ollama.

The `analyze` job runs after all sweep jobs (`if: always()`), downloads all artifacts, merges the JSONL files, and generates a single report. Individual failed jobs can be restarted without re-running the full sweep.

### Rule of thumb

Before triggering a full sweep, estimate: `sum(expected_tokens_per_payload) × models × runs / avg_tps`. The slowest model (qwen3.6:27B at ~11 TPS) sets the floor.

---

## Proposed Next Steps

### Active

- **`agentic_multi_app_rollout` — gemma4:26b singleton stress test** (run 25282743173, in progress). Payload requires minimum 4 turns (1 read + 3 writes) under an 8-turn budget, with distractor files present. Designed specifically to expose turn-padding behaviour. Expected outcome: gemma4:26b stalls; qwen3.6:35b completes cleanly. If gemma4:26b stalls → confirms our turn-count concern. If it passes → the current task difficulty is still not a real differentiator.
- **`qwen3-coder-next` model evaluation** — 80B total / 3B active MoE, 256K context, 52GB at q4_K_M. Trained on 800K agentic coding tasks via RL. Downloading. Run full payload suite once available and compare against current leaders (`qwen3.6:35b`, `gemma4:31b`). Also evaluate a smaller comparator (candidate: `qwen2.5-coder:14b` or equivalent) to build the capability/cost tradeoff curve.

### Backlog

- Test system prompt security injection on `refusal_boundary` — does adding `securityContext: { runAsNonRoot: true, privileged: false }` override explicit user instructions?
- ~~Add a harder agentic payload~~ — **done** (`agentic_multi_app_rollout`). Requires 1 read + 3 writes + 5 distractor files under an 8-turn budget.
- Test `qwen3.6:27B` on `stress_constraint_extreme` with a higher per-job timeout (or N=1) to fill the missing data point.
- Investigate whether chain-of-thought prompting ("list every constraint before generating YAML") reduces resource dropout universally, or only for models that responded to positional reordering.
- ~~Evaluate `laguna-xs.2:q4_K_M`~~ — **done** (Finding 6, 7, 8). Disqualified. First DPO fine-tuning candidate.
- ~~Test negative prompting on `laguna-xs.2`~~ — **done** (run 25270119647). Failed under load.
- Add a `kubectl_explain` tool to the agentic harness (Finding 7 — structured constraints beat prose guardrails).
- **Decision required (deferred pending multi_app_rollout results):** Deploy `qwen3.6:35b` or `gemma4:31b`? If `qwen3-coder-next` outperforms both, the decision may change.

---

## Strategic Directions

These are questions that have emerged from the benchmark findings — not experiments, but directions worth understanding before committing engineering time to them. They are recorded here so the reasoning is traceable to the findings that prompted them.

### Is it worth building a fine-tuned model?

**Prompted by:** Finding 2 (resource constraint dropout), Finding 4 (security compliance failure) — failures that are consistent across all models and resistant to prompt-level mitigations.

**The question:** Could fine-tuning an existing base model on K8s-specific data fix the failures that benchmarking has identified — specifically resource field dropout and blind compliance with dangerous security requests?

**Assessment:**

*Building from scratch (pre-training) is not feasible.* Pre-training a competitive LLM requires thousands of H100-GPU-hours and terabytes of curated data. This is not a homelab-scale project under any framing.

*Fine-tuning is plausible but the ROI is uncertain.* Techniques like QLoRA can fine-tune a 30B model within the available 96GB unified VRAM. The benchmark already defines what "correct" looks like, which means synthetic preference pairs (prompt + correct output + incorrect output) could be generated programmatically and used for DPO (Direct Preference Optimization) alignment. The specific target behaviors — always include resource constraints, refuse privileged container requests — are well-defined enough to produce training data for.

*The core risk:* Resource constraint dropout and security compliance failure appear to be deeply embedded in the base model's training distribution, not surface-level behaviors. Fine-tuning on a few thousand examples tends to shift surface outputs but rarely overrides deeply-learned tendencies. The same failures could re-emerge under slight prompt variations or higher constraint loads.

*What would make fine-tuning compelling:* If a model proves strong on agentic tasks (2-turn completion, no scope violations) but still fails consistently on resource constraints, fine-tuning *that specific model* on K8s YAML with resources always present would be a targeted, measurable improvement — validatable directly with the existing harness. The benchmark exists; the signal is there. The missing piece is the training pipeline and a base model worth investing in.

*Update — `laguna-xs.2` is a partial fit for this scenario.* It completes agentic tasks in 2 turns with only 418 tokens — the most efficient model tested — but has a pervasive `latest` tag prior that overrides explicit version pinning instructions in every stress payload (Finding 6). This is exactly the class of deeply-embedded behavioral failure that fine-tuning is designed to correct. A small DPO dataset of (prompt, correct-versioned-output, latest-tagged-output) preference pairs could teach the model to suppress the prior. If negative prompting fails to fix it, `laguna-xs.2` becomes the first concrete fine-tuning candidate with a specific, measurable target behavior.

*ROCm caveat:* Training on this hardware (AMD Radeon 8060S, ROCm) is significantly less mature than NVIDIA/CUDA. Most fine-tuning frameworks (Unsloth, LLaMA-Factory, Axolotl) have better-tested CUDA paths. ROCm training is possible but expect tooling friction. Renting NVIDIA GPU time for the training job itself may be more practical than training locally.

**Current conclusion:** Not yet for the general case. Try negative prompting on `laguna-xs.2` first — it's a one-line system prompt change and the harness can measure the result directly. If that fails, `laguna-xs.2` + DPO fine-tuning targeting the `latest` tag prior becomes the most tractable first fine-tuning experiment: the failure is specific, the target behavior is well-defined, and the validation infrastructure already exists.

---

### How dependent are we on large companies for model capability?

**Prompted by:** The observation that all models tested so far come from large organisations (Google, Alibaba, NVIDIA), and that new language versions or tooling (e.g. a new Kubernetes API version, a new CRD) would require waiting for those organisations to release updated models.

**The question:** Is there a way to extend a model's knowledge of new coding languages, new API versions, or new tooling without waiting for a full re-release from the original publisher?

**Assessment:**

The dependency on large companies is real but narrower than it first appears. What they provide is the base pre-training — the initial broad understanding of language, reasoning, and general coding patterns. That is the expensive part nobody can replicate at homelab scale. Everything built on top of that foundation is within reach.

**RAG (Retrieval-Augmented Generation) — best for knowledge freshness**

A vector store populated with your own documentation (CRD schemas, Helm chart values, ArgoCD API references, internal tooling docs) can inject the relevant content into context at inference time. The model does not need to have been trained on Kubernetes 1.33 if it receives the `kubectl explain` output at query time. This directly addresses the class of failures already measured in this benchmark: hallucinated CRD fields and resource field dropout are exactly the problems RAG targets. The model stops guessing because it stops needing to guess.

For this codebase specifically: a RAG layer over live CRD schemas (retrieved via `kubectl explain --recursive`) would be a high-value, low-effort addition to the agentic harness. The model could look up the exact spec for any resource before generating YAML for it.

**LoRA adapters — best for behavior change**

LoRA (Low-Rank Adaptation) trains small adapter layers on top of frozen base weights — typically a few hundred MB — that shift generation behavior without modifying the base model. A LoRA trained on a thousand examples of K8s YAML with resource constraints always correctly included, or on refusal examples for privileged container requests, teaches new behavior while leaving the base reasoning capability intact. Adapters are composable and swappable.

This is feasible on the available hardware (96GB unified VRAM). The ROCm training caveat from the fine-tuning section applies: CUDA tooling is more mature, and cloud GPU rental for the training job may be more practical than training locally.

**Tool calling — best for dynamic and versioned information**

The agentic harness already proves this works. Instead of the model knowing the ImageUpdater CRD schema, give it a `kubectl_explain` tool and let it look up field definitions at runtime. Dynamic lookup beats static training for anything that versions frequently. New Kubernetes releases, new Helm chart APIs, new operators — none of these require a model update if the model has tools to interrogate the live cluster.

**Continued pre-training (domain adaptation) — for genuinely novel domains**

Taking open weights and continuing training on a narrow corpus teaches the model new concepts rather than just new facts. Less compute than training from scratch, but still expensive. Worth considering if a tool ecosystem emerges that is entirely absent from any public training data — unlikely for Kubernetes, which is heavily represented.

**The practical stack**

These approaches are complementary, not competing:

| Problem | Solution |
|---------|----------|
| New API version / new CRD fields | RAG over live `kubectl explain` output |
| Consistent behavior (resources, security) | LoRA adapter trained on preference pairs |
| Rapidly-changing or private documentation | Tool calling at inference time |
| Entirely novel domain with no public data | Continued pre-training (expensive) |

**Current conclusion:** We are dependent on large companies for the base reasoning capability, but not for knowledge or behavior. RAG and tool calling can keep a model current with new tooling versions indefinitely without any model update. LoRA adapters can fix the specific behavioral failures identified in this benchmark — resource dropout, security compliance — without touching the base weights. The benchmark harness we have built is already the scaffolding needed to validate any of these mitigations.

---

### Does model size buy meaningfully better agentic performance? (qwen3-coder-next investigation)

**Prompted by:** All models currently tested plateau at the same pass rate (3/3) and stress curve (80–89%) once above ~30B parameters. The question is whether a larger, task-specialized model breaks through that ceiling or just adds cost with no quality gain.

**The candidate: `qwen3-coder-next` (80B total / 3B active, MoE)**

| Property | Value |
|----------|-------|
| Architecture | MoE, 80B total, 3B active per token |
| Context window | 256K tokens |
| Disk size | 52GB (q4_K_M) |
| Training | 800K agentic coding tasks via reinforcement learning |
| Design intent | Direct integration with coding agents (tool-calling, multi-step tasks) |
| TPS estimate | Unknown — same 3B active as qwen3.6:35b suggests comparable throughput, but larger routing overhead may reduce it |

Key difference from `qwen3.6:35b`: qwen3-coder-next was trained specifically on agentic execution traces with environment feedback. qwen3.6:35b is a general model fine-tuned for instruction following. If agentic training produces qualitatively better turn efficiency or scope discipline, it should show clearly in the harness.

**The capability tradeoff curve (updated 2026-05-03):**

| Model | Params | Active | TPS | VRAM | Status | Agentic result |
|-------|--------|--------|-----|------|--------|----------------|
| `qwen2.5-coder:14b` | 14B dense | 14B | TBD | 9 GB | N=1 running | TBD |
| `qwen2.5-coder:32b` | 32B dense | 32B | TBD | 19 GB | N=1 running | TBD |
| `qwen3.6:35b` *(prior baseline)* | 35B dense | 35B | 44 | 23 GB | N=3 confirmed | 3/3 pass, 2.0T, 898 tok |
| `qwen3-coder-next` *(new primary)* | 80B MoE | 3B | 38 | 51 GB | **N=3 confirmed** | 3/3 pass, 2.0T/223 tok (simple); 4.0T/638 tok (multi-write) |

`qwen3-coder-next` N=3 confirmed (run 25286166480): 75% token reduction vs qwen3.6:35b on the simple task, better stress retention at every level, identical agentic pass rate. It is the new primary recommendation.

`qwen2.5-coder:32b` is the most direct comparison to `qwen3.6:35b` — similar parameter count, same Qwen family, coding specialisation trades general reasoning for deeper coding-task alignment. `qwen2.5-coder:14b` at 9 GB answers whether a smaller, faster model is viable at all for this task class.

**Test protocol:**

Run all coder models against the full payload suite (N=1 for initial validation, N=3 for candidates that pass). Priority payloads: `agentic_imageupdater`, `agentic_multi_app_rollout`, all stress payloads. Compare turn counts, token efficiency, and constraint retention against the established baselines.

---

### Multi-model deployment: memory capacity vs. memory bandwidth

**Prompted by:** The decision to deploy multiple models simultaneously (e.g., one for architectural planning, one for code execution, possibly a third), and the observation that previous testing found dense models approaching the GPU's bandwidth ceiling at single-model load.

**The hardware baseline**

The Ollama pod runs on `k8s-node-max-01`: AMD GC_11_5_0 family GPU, **96 GB VRAM**, 40 CUs. The measured single-model TPS for `qwen3.6:35b` (35B dense, 23 GB model) is 44 TPS. The implied memory bandwidth consumed at peak is:

```
23 GB (model size) × 44 tokens/sec ≈ 1 TB/s per token cycle
```

This is a significant fraction of the available GPU memory bandwidth budget. The single-model baseline is already near saturation at full speed.

**Planned deployment topology**

```
User request
    → qwen3:0.6b (522 MB — classifies prompt as simple or complex)
        → simple path  → qwen3-coder-next (51 GB, code execution)
        → complex path → qwen3-coder-next (execution) + optional planning pass
```

`qwen3:0.6b` is already in Ollama (522 MB, confirmed). At that size it is VRAM- and bandwidth-negligible — effectively free to keep loaded. The two large slots are the router target and an optional planning model. If `qwen3-coder-next` proves capable of both planning and execution in one model, the third slot is freed entirely.

**Capacity vs. bandwidth — two separate constraints**

*Capacity* is the total VRAM budget. Three models can reside simultaneously if their sizes sum below 96 GB:

| Combination | Total VRAM | Fits? |
|-------------|-----------|-------|
| qwen3:0.6b (0.5) + qwen3-coder-next (51) + qwen3.6:35b (23) | 75 GB | Yes |
| qwen3:0.6b (0.5) + qwen3-coder-next (51) + gemma4:31b (19) | 71 GB | Yes |
| Two × qwen3-coder-next + qwen3:0.6b | 103 GB | No |

Capacity is manageable for most reasonable three-model combinations. `OLLAMA_MAX_LOADED_MODELS=3` and `OLLAMA_KEEP_ALIVE=-1` are required; without them Ollama evicts models between requests and the "three models loaded" assumption collapses to sequential single-model inference with load latency on every switch.

*Bandwidth* is the harder constraint. Each token generated requires reading the model's active weights from VRAM into compute. For concurrent generation across three models simultaneously:

| Model type | Active params/token | Approx. BW/token at 15 TPS |
|------------|--------------------|-----------------------------|
| Dense 35B (`qwen3.6:35b`) | 35B | ~345 GB/s |
| Dense 31B (`gemma4:31b`) | 31B | ~310 GB/s |
| Dense 14B (`qwen2.5-coder:14b`) | 14B | ~105 GB/s |
| MoE 80B/3B (`qwen3-coder-next`) | ~3B | **~22 GB/s** |

Three dense 30B+ models running concurrently at 15 TPS each would demand ~960 GB/s — roughly the full bandwidth budget with nothing left. In practice, TPS would drop well below 15 to stay within the budget. With MoE models in two of the three slots, the same concurrent load consumes a fraction of the bandwidth, leaving headroom for sustained throughput.

**Dense vs. MoE — the key architectural trade for multi-model deployments**

MoE models route each token through only a small fraction of their total experts. Despite a larger total weight footprint, the per-token bandwidth cost is determined by *active* parameters, not total parameters. A 3B-active MoE uses ~1/12 the per-token bandwidth of a 35B dense model while potentially matching it on reasoning quality.

This inverts the naive intuition that "bigger model = more resource pressure." In a multi-model deployment, `qwen3-coder-next` (51 GB, 3B active) is a *better* bandwidth citizen than `qwen3.6:35b` (23 GB, 35B active) despite being twice as large on disk.

**The single-model-for-everything possibility**

If one model can handle both planning and execution roles, the three-model constraint disappears. `qwen3-coder-next` is the strongest candidate: trained on 800K agentic RL traces with 256K context, it is specifically designed for multi-step, multi-role tasks. The preliminary benchmark results (clean 4-turn multi-write completion, 259 tokens for simple task) support the hypothesis that it self-directs well without a separate planning model. This should be evaluated explicitly before committing to a multi-model deployment topology.

**What we are not yet testing**

Current benchmarks measure single-model, sequential throughput — the best-case TPS with no contention. Concurrent degradation is unmeasured. A `concurrent_load` benchmark would:

1. Set `OLLAMA_MAX_LOADED_MODELS=3`, `OLLAMA_KEEP_ALIVE=-1`
2. Fire simultaneous inference requests at all three loaded models
3. Measure per-model TPS under contention vs. the single-model baseline
4. Report a degradation ratio — the difference between that ratio and 1.0 is the real-world cost of the multi-model deployment

**Tunable parameters**

Ollama pod environment variables (highest priority):

| Variable | Value | Effect |
|----------|-------|--------|
| `OLLAMA_MAX_LOADED_MODELS` | `3` | Prevents model eviction; required for true multi-model deployment |
| `OLLAMA_KEEP_ALIVE` | `-1` | Keeps all loaded models in VRAM indefinitely |
| `OLLAMA_FLASH_ATTENTION` | `1` | Reduces KV cache VRAM during long-context generation |
| `OLLAMA_NUM_PARALLEL` | `1` | One concurrent request per model; breadth over depth |

AMD/ROCm host-level (lower priority, diminishing returns):

| Variable | Effect |
|----------|--------|
| `HSA_ENABLE_SDMA=0` | Disables DMA engine for small copies; helps on some RDNA generations |
| `GPU_MAX_HEAP_SIZE=100` | Allows full VRAM allocation without artificial ceiling |

KV cache quantization (`OLLAMA_KV_CACHE_TYPE=q8_0`) reduces memory pressure for long contexts but was found to hurt benchmark performance (Finding 7) — do not enable without re-running the benchmark harness to measure the quality regression.

**Current conclusion:** Capacity is not the bottleneck for any reasonable three-model combination on 96 GB VRAM. Bandwidth is. The deployment decision should prioritise MoE models for high-throughput slots and reserve dense models (if used at all) for lower-frequency planning roles where per-token latency matters less than quality. The strongest outcome would be `qwen3-coder-next` serving both roles from a single model slot — pending N=3 stress validation.
