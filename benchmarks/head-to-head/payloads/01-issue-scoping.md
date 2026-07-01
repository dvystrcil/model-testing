# Task 01: Issue scoping

- **Cluster:** code & cluster
- **Fixtures:** none
- **Dimensions:** correctness, faithfulness, format, memory

## Prompt

We want to add per-user spend caps to Open WebUI — a monthly token/dollar budget
per account, enforced at request time, with an admin-visible usage view. Produce
a properly-formatted GitHub issue body for the `dvystrcil/open-webui` repo.
Follow the homelab issue convention: Background, Pieces (the concrete units of
work), a TDD plan, and Acceptance Criteria with checkboxes. Do not write any
code — just the issue body.

## Ground truth / rubric

A correct issue body must contain, at minimum:
- **Background** framing the *why* (cost control / optionality), not just restating the title.
- **Pieces** decomposed into concrete units — e.g. a budget store, a request-time enforcement hook, an admin usage view. Vague "add feature" bullets fail.
- **TDD plan** — failing-test-first, matching `feedback_always_tdd`. At least names the test surface (enforcement returns 402/blocks when over cap; usage accrues correctly).
- **Acceptance Criteria** as `- [ ]` checkboxes, each independently verifiable (per `feedback_acceptance_criteria`).

Faithfulness checks (homelab-specific, only in memory/repo):
- OWUI secrets/config conventions (InfisicalSecret CR, not literal Secret) if it proposes storing an API budget key.
- Awareness that OWUI enforcement lives in Pipelines/Filters, not core (per the OWUI filter memories) — bonus, not required.

## Scoring

- **correctness (0–3):** 3 = all four sections present and substantive; 2 = one section thin/missing; 1 = two+ missing; 0 = not an issue body.
- **faithfulness (0–3):** 3 = respects OWUI/homelab conventions where relevant, invents nothing; 1 = generic SaaS framing; 0 = hallucinates repo facts.
- **format (auto):** regex for `## Background`, `## Pieces` (or equiv), `## Acceptance Criteria`, and ≥3 `- [ ]` lines.
- **memory (0–3):** 3 = references ≥2 homelab-only facts (Infisical, Pipelines-enforcement, TDD convention); 0 = none.
