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
| Inference stack | Ollama 0.22.1 via ROCm/HSA (AMD compute, not CUDA) |
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
| `laguna-xs.2:q4_K_M` | 34 | 3/3 | 2.0 | **418** | No — `latest` tag inertia, schema gaps |
| `qwen3.6:35b` | 44 | 3/3 | 2.0 | 1,021 | **Yes** |
| `gemma4:31b` | 10 | 3/3 | 2.0 | 1,196 | **Yes** |
| `qwen3.6:27B` | 11 | 3/3 | 2.0 | 1,693 | Yes |
| `gemma4:26b` | 50 | 3/3 | 4.7 | 2,385 | No — tool-loop inefficiency |

All five models solve the task in 2 turns except `gemma4:26b` (4.7 turns). `laguna-xs.2` is the most token-efficient by a large margin (418 tokens — less than half of any other model) but is disqualified by systematic forbidden field violations in single-shot payloads (see Finding 6).

**Important:** The AI analysis tool that generates the sweep report weights TPS heavily and produces incorrect rankings. The agentic harness is the authoritative signal for coding assistant selection. **`qwen3.6:35b` or `gemma4:31b` are the correct choices** — both complete in 2 turns with clean single-shot scores.

### Finding 2: The stress curve is a U-shape, not linear decay

We extended the stress tests from 10 to 13 and 18 constraints. The expected linear decay did not materialise. Instead, scores recover at higher constraint counts:

| Payload | Constraints | qwen3.6:35b | qwen3.6:27B | gemma4:26b | gemma4:31b | laguna-xs.2 |
|---------|-------------|-------------|-------------|------------|------------|-------------|
| easy | 4 | 100% | 100% | 100% | 100% | 92%† |
| medium | 7 | 86% | 90% | 86% | 86% | 90%† |
| multi | 10 | 80% | 80% | 80% | 80% | 77%† |
| hard | 13 | 85% | 90% | 85% | 85% | 85%† |
| extreme | 18 | 93% | — | 89% | 89% | 76%† |

† laguna-xs.2 scores carry a `latest` tag forbidden violation across all stress payloads — see Finding 6.

The U-shape is not a sign of improvement. It is an artifact of how quality_facts are scored: the new constraints added at 13 and 18 (startup probes, service accounts, volumes, env vars, annotations, init containers) are fields models reliably produce. Adding them raises the denominator in ways that help the percentage — even though models are *still* dropping the same memory fields at every level.

**The actual failure signature is stable, not improving:** `memory: "512Mi"` and `memory: "256Mi"` are dropped at every stress level from 7 constraints onward, regardless of how many other constraints are added. The score percentage moves because other constraints are satisfied, not because memory retention improves.

`qwen3.6:27B` has no result for `stress_constraint_extreme` (18 constraints). At 11.3 TPS with N=3 runs, that payload likely exceeded the 120-minute per-job timeout. This is a data gap, not a model failure.

**Neither cliff nor linear decay** — the failure mode is better described as *selective semantic dropout*: a small set of field types (resource constraints) are consistently deprioritized regardless of how many other constraints are present or how high the total constraint count goes.

### Finding 3: Resource constraint dropout is semantic, not positional — with one exception

The `stress_positional_bias` payload ran the same 7 constraints as `stress_constraint_medium` with resources listed **first** instead of fifth.

| Model | Medium (resources 5th) | Positional bias (resources 1st) | Change |
|-------|------------------------|----------------------------------|--------|
| `qwen3.6:35b` | 86% | 86% | none |
| `qwen3.6:27B` | 90% | 90% | none |
| `gemma4:26b` | 86% | 86% | none |
| `gemma4:31b` | 86% | **90%** | +4% |
| `laguna-xs.2` | 90%† | 81% | −9% |

† laguna-xs.2 carries a `latest` tag violation in medium but not in positional_bias. The score drop in positional_bias reflects fact misses on other fields, not the resource position. Not interpretable as a positional sensitivity result — see Finding 6 for the `latest` tag confound.

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

6. **Can `laguna-xs.2`'s `latest` tag prior be suppressed?** Negative prompting ("never use latest, always use the exact version specified"), system prompt injection, or a post-generation filter may be sufficient. If it can be suppressed reliably, laguna's token efficiency (418 avg tokens) makes it the strongest agentic candidate. Untested.

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

- Test system prompt security injection on `refusal_boundary` — does adding `securityContext: { runAsNonRoot: true, privileged: false }` override explicit user instructions?
- Add a harder agentic payload with longer history context and ambiguous task description to attempt to reproduce the `manifestTargets` hallucination
- Test `qwen3.6:27B` on `stress_constraint_extreme` with a higher per-job timeout (or N=1) to fill the missing data point
- Investigate whether chain-of-thought prompting ("list every constraint before generating YAML") reduces resource dropout universally, or only for models that responded to positional reordering
- ~~Evaluate `laguna-xs.2:q4_K_M`~~ — **done** (Finding 6, Finding 7). Disqualified by `latest` tag inertia; token efficiency (418 avg) makes it worth retesting if the prior can be suppressed via system prompt
- Test negative prompting on `laguna-xs.2` to determine if the `latest` tag prior can be suppressed without fine-tuning
- Add a `kubectl_explain` tool to the agentic harness (Finding 7 — structured tool constraints beat prose guardrails; dynamic schema lookup could eliminate hallucination class entirely)

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
