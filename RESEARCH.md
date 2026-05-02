# LLM Benchmark Research: Closing the Agentic Validity Gap

## The Problem

Standard LLM benchmarks measure single-shot prompt compliance: send one request, get one response, check it against a keyword list. This works for factual recall and basic instruction following, but it fundamentally cannot detect the class of failures that appear when models are used as coding assistants in production.

We discovered this gap directly. A model (`gemma4:26b`) scored top marks on every benchmark we had — then failed repeatedly in production by:
- Inventing YAML fields that don't exist in the CRD schema (`manifestTargets`)
- Silently writing to files it was not asked to touch
- Entering a tool-call loop and making no forward progress

None of these failures were detectable with keyword-based single-shot benchmarks. This document describes the investigation we built to close that gap, the findings so far, and what we are still trying to understand.

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

| Model | Single-shot rank | Agentic rank | Avg turns | Avg tokens |
|-------|-----------------|--------------|-----------|------------|
| `gemma4:31b` | #4 (10 TPS) | **#1** | 2.0 | 999 |
| `qwen3.6:27B` | #3 (11 TPS) | #2 | 2.3 | 1431 |
| `qwen3.6:35b` | #2 (46 TPS) | #3 | 2.7 | 1433 |
| `gemma4:26b` | #1 (50 TPS) | **#4** | 4.7 | 2553 |

`gemma4:26b` consumed 2.5× more tokens and 2.35× more turns than `gemma4:31b` for the same task. In production an agentic coding assistant, extra turns mean extra latency, growing context windows, and more opportunities to drift out of scope. The speed advantage measured in single-shot is consumed by tool-loop inefficiency.

`gemma4:31b` solved the task in exactly 2 turns in every run: `read_file` → `write_file`. That is the optimal strategy.

### Finding 2: All models share the same stress failure signature

Under constraint load, all four models dropped the same field at the same threshold:

| Constraints | Score | Dropped |
|-------------|-------|---------|
| 4 | 100% | — |
| 7 | 86% | `memory: "512Mi"` |
| 10 | 80% | `memory: "512Mi"` + `memory: "256Mi"` |

The identical failure signature across four distinct models suggests this is not a model-specific weakness — it is a universal behavior where resource constraints are semantically deprioritized relative to structural YAML keys (probes, topology, labels). Models appear to treat resource fields as "fill in later" and run out of context before doing so.

**This is a platform-level finding, not a model-selection finding.** It suggests a mitigation strategy: always place resource constraints at the top of the prompt, not the bottom.

### Finding 3: All models comply with dangerous security requests

The `refusal_boundary` payload asked models to create a privileged root container. All four models generated `privileged: true`, `runAsUser: 0`, and `hostNetwork: true` with the identical violation signature. This is likely a prompt-alignment gap in the Ollama serving layer rather than a model architecture difference. Mitigation: include `securityContext: { runAsNonRoot: true, privileged: false }` in the system prompt baseline for all production deployments.

---

## Current Investigation: The Stress Curve

The 10-constraint plateau tells us *where* models start failing but not *how* they fail. We are now extending the stress tests to trace the full degradation curve.

### What we are measuring

```
Constraints →   4    7    10    13    16    18    20
gemma4:31b    100%  86%   80%    ?     ?     ?     ?
qwen3.6:35b   100%  86%   80%    ?     ?     ?     ?
gemma4:26b    100%  86%   80%    ?     ?     ?     ?
qwen3.6:27B   100%  86%   80%    ?     ?     ?     ?
```

### Two failure modes we are looking for

**Cliff failure:** Model holds near 80% through constraint 15, then collapses suddenly to 30% at constraint 16. The model has a hard context limit it hits all at once.

**Linear decay:** Model degrades steadily — 75% at 13, 65% at 16, 50% at 18. The model compresses evenly as load increases.

Linear decay is more predictable and therefore more useful in production. A cliff failure means the model is unreliable in ways you cannot anticipate — it passes until it suddenly doesn't.

### The positional bias experiment

We noticed that `memory: "512Mi"` (constraint #5 in a 7-constraint prompt) is always the first field dropped. Is this because:

**A) Positional forgetting** — the field appears late in the prompt and falls outside the model's effective attention window

**B) Semantic deprioritization** — models have learned that resource constraints are less critical than structural keys (probes, labels, topology) regardless of where they appear

We test this by running the identical 7 constraints with resources listed **first** instead of fifth (`stress_positional_bias`). If memory stops being dropped, the failure is positional and can be mitigated by prompt reordering. If it still drops, the failure is semantic and requires a different mitigation (e.g. chain-of-thought prompting, split manifests).

### New stress payloads

| Payload | Constraints | New requirements added |
|---------|-------------|----------------------|
| `stress_constraint_easy` | 4 | Baseline |
| `stress_constraint_medium` | 7 | Resources, probes |
| `stress_multi_constraint` | 10 | Security context, topology spread |
| `stress_constraint_hard` | 13 | Startup probe, terminationGracePeriodSeconds, serviceAccountName |
| `stress_constraint_extreme` | 18 | Volume + mount, env vars, annotations, init container |
| `stress_positional_bias` | 7 | Same as medium, resources listed first |

---

## What We Still Don't Know

1. **Where does the divergence happen?** All four models fail identically at 10 constraints. At some higher count, we expect them to separate. We don't know yet whether that separation comes at 13, 18, or beyond.

2. **Is the failure mode a cliff or linear decay?** We have two data points (7, 10) that show uniform behavior. We need 13 and 18 to see the shape.

3. **Is the production failure reproducible?** The agentic harness at N=3 with temperature=0.2 has not yet reproduced the specific `manifestTargets` hallucination that triggered this investigation. This may require a longer history context, more ambiguous task description, or higher temperature.

4. **Does system prompt injection fix the security compliance failure?** All models comply with dangerous security requests. We have not yet tested whether adding `securityContext: { runAsNonRoot: true, privileged: false }` to the system prompt overrides this behavior.

---

## Proposed Next Steps

- Run the full stress curve (easy → medium → multi → hard → extreme) in a single sweep with N=3 to get a complete degradation profile per model
- Run `stress_positional_bias` to determine if constraint ordering is a viable mitigation
- Add a harder agentic payload with a longer context window and ambiguous task description to attempt to reproduce the `manifestTargets` hallucination
- Test system prompt security injection on `refusal_boundary`
