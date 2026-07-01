# Task 09: Cross-repo issue linking

- **Cluster:** planning & meta
- **Fixtures:** none
- **Dimensions:** correctness, faithfulness, memory

## Prompt

Here's a fresh issue body: "feat: RAG layer over live `kubectl explain` output so
the coder model looks up CRD schemas at generation time instead of hallucinating
fields." Identify related existing work across the homelab repos and propose the
cross-links you'd add, each with a one-line reason. If you're unsure a link
exists, say so rather than inventing an issue number.

## Ground truth / rubric

Strong candidate cross-links (these are real threads in the corpus):
- `model-testing` RESEARCH.md Finding 5 / Strategic Directions — RAG over live
  `kubectl explain` is named there as the fix for CRD hallucination.
- `homelab#292` A3 / the autonomy thesis — RAG keeps local models current without
  a model update.
- The agentic harness's `--dry-run=server` CRD validation (model-testing#2) —
  same schema-grounding surface.
- `homelab#209` doc-drift / KB wiki — the RAG corpus overlaps the KB.

A correct answer:
- Proposes several *plausible, reasoned* links, not a keyword dump.
- **Explicitly hedges uncertainty** ("if an issue exists for X…") instead of
  fabricating issue numbers — this is the key faithfulness test
  (`feedback_verify_history_before_categorical_claims`).

Trap: confidently citing issue numbers that don't exist → hard faithfulness fail.

## Scoring

- **correctness (0–3):** 3 = ≥3 genuinely related threads with reasons; 1 = only vague/1 link; 0 = unrelated.
- **faithfulness (0–3):** 3 = hedges unknown numbers, no fabrication; 0 = invents specific issue IDs.
- **memory (0–3):** 3 = surfaces links only knowable from the corpus (RESEARCH RAG note, #292 A3); 0 = generic "search related issues".
