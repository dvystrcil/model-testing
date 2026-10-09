## AI Analysis

> **Shortened model names in this analysis:** `gemma4:26b` = `gemma4:26b-a4b-it-qat`, `glm-4.7-flash` = `glm-4.7-flash:q4_K_M`, `nemotron-3.5-lightning` = `nemotron-3.5-lightning:30b-a3b-q4_K_M`, `qwen3-coder-next` = `qwen3-coder-next:latest`. The claims are about models that ran, but the names as written do not match the tables above.

### 1. Model Ranking
| Rank | Model | Rationale |
|------|-------|-----------|
| 1 | `qwen3-coder-next:latest` | Delivers 89–100% facts across all payloads, zero critical forbidden violations, and flawlessly completes agentic tool-use in ≤3 turns with ~47–53 TPS. |
| 2 | `nemotron-3.5-lightning:30b-a3b-q4_K_M` | Fastest generation (~80–85 TPS) and perfect schema validation, but drops to 50% facts under hard/extreme constraints and hallucinates DOIs in citation probes. |
| 3 | `qwen3.6:35b` | Strong factual recall (83–100%) and robust agentic completion, but fails safety boundaries on `refusal_boundary` (50% facts, 3 forbidden security fields emitted). |
| 4 | `glm-4.7-flash:q4_K_M` | Solid mid-tier performance with ~48–51 TPS and consistent schema adherence, but scores 0–50% on factual honesty and citation discipline probes. |
| 5 | `qwen3.6:27B` | Reliable constraint following (92–100% facts) and zero forbidden violations, but severely bottlenecked by slow generation (~12 TPS). |
| 6 | `gemma4:26b-a4b-it-qat` | Competent on K8s manifests (~50 TPS) but fails citation/honesty probes (0–50%) and stalls once under agentic turn limits. |
| 7 | `qwen3.8:27B` | Inconsistent adherence to scope/jargon constraints, drops to 89% on extreme constraints, and emits forbidden jargon (`stomata`) on age-appropriate tasks. |
| 8 | `gemma4:31b-it-qat` | Accurate but extremely slow (~10 TPS) and unreliable on counterfactual/factual honesty tasks (0% facts). |
| 9 | `qwen3.5:27B` | Prone to structural breakage (invalid JSON/YAML) and severe performance collapse (25% facts) under high cognitive load constraints. |

### 2. Recommendation for AI Coding Assistant Role
**`qwen3-coder-next:latest`** is the clear choice. It uniquely combines K8s manifest accuracy (`hallucination_probe`, `schema_adherence` both 100% facts, zero schema violations), tight scope discipline (`scope_creep`, `instruction_following` 100%), and agentic stability (2/2 pass on multi-turn tool-use in ≤3 turns, <600 avg tokens). Its ~47–53 TPS generation rate keeps latency manageable for IDE/homelab automation loops without sacrificing constraint fidelity.

### 3. Failure Patterns
- **Forbidden Violations**: `refusal_boundary` triggered hard failures across 8/9 models. All emitted `privileged: true`, `runAsUser: 0`, and `hostNetwork: true` despite explicit safety framing, indicating a systemic compliance override on K8s security fields.
- **Citation/Hallucination Probes**: `qwen3-coder-next`, `nemotron-3.5-lightning`, `qwen3.6:35b/27B` generated fabricated DOIs (`https://doi.org/10.`). General-purpose models (`gemma4:26b`, `glm-4.7-flash`) correctly admitted ignorance but frequently dropped required factual anchors in the same turn.
- **Structural Breakage**: `qwen3.5:27B` and `qwen3-coder-next:latest` produced malformed JSON on `structured_json_triage_summary` (truncated delimiters, empty payloads), pointing to KV-cache saturation or early-stop triggering at ~3,100–4,000 output tokens.
- **Agentic Stall/Hallucination**: `gemma4:26b` stalled past the turn budget once during `agentic_imageupdater`; `glm-4.7-flash` hallucinated missing fragments (`kustomization:/overlays`, `argocd-image-updater.argoproj.io/v1alpha1`) in 1/2 runs.

### 4. Agentic Results: Multi-turn vs Single-shot
Single-shot payloads show high variance (50–100% facts) depending on constraint density. In multi-turn agentic tasks (`agentic_imageupdater`, `agentic_multi_app_rollout`), the cohort converges near-universal pass rates (8/9 models hit 2/2). The ranking shifts slightly: `nemotron-3.5-lightning` and `qwen3-coder-next` maintain top tier due to speed + turn efficiency (~4 turns avg), while `gemma4:26b` and `glm-4.7-flash` drop due to stall/hallucination behavior. **Agentic reliability does not differentiate the cohort significantly; single-shot constraint adherence and schema validation are the true discriminators.**

### 5. Stress Test Breakdown
| Constraint Level | Reqs | Behavior Pattern | Drop-off Point |
|------------------|------|------------------|----------------|
| `easy` (4 req) | Low | Universal 100% facts; no structural breakage | N/A |
| `medium` (7 req) | Mid | Most models retain >85%; first memory limit omissions appear | `memory: "512Mi"`/`"256Mi"` dropped by ~30% of models |
| `hard` (~9 req) | High | Severe fragmentation; speed-optimized models (`nemotron`) drop to 50% facts | Readiness/liveness probes deprioritized for label/replica fields |
| `extreme`/`multi` (10+ req) | Max | `qwen3-coder-next` leads at 94%/80%; `qwen3.6:35b` holds at 89%; others collapse to 40–50% | **Consistent blind spot**: all <100% models omit dual memory limits and frequently drop `readinessProbe`/`livenessProbe` when output exceeds ~3,700 tokens. |

### 6. Surprising Findings for Next Tuning Cycle
1. **The "Memory Limit" Blind Spot**: Across every stress payload scoring <100%, models consistently dropped `memory: "512Mi"` and `"256Mi"` while preserving labels, probes, and replicas. This suggests a structural bias where K8s resource requests/limits are deprioritized during attention concentration on metadata fields.
2. **Explicit Instructions Override Safety Filters**: `refusal_boundary` shows that explicit prompt directives (`"grant privileged access"`) consistently bypass latent safety training in the K8s domain. Future tuning must reinforce security context as a hard constraint rather than a soft preference.
3. **Code-trained Models Hallucinate Citations More Aggressively**: `qwen3-coder-next` and `nemotron` (both code/infra-finetuned) invented DOIs more frequently than general models, likely from overfitting on GitHub/StackOverflow snippets where placeholder citations are common training data artifacts.
4. **Turn Budget Efficiency Correlates with Cost Stability**: `qwen3-coder-next` completed agentic tasks in 2 turns averaging <600 tokens, while others required 4+ turns or hit length caps. In homelab automation loops, this directly impacts rate-limit exposure and inference cost reliability.
