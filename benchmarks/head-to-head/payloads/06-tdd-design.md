# Task 06: TDD validation-suite design

- **Cluster:** code & cluster
- **Fixtures:** none
- **Dimensions:** correctness, faithfulness, format, memory

## Prompt

Design a 6-test validation suite for upgrading `harbor` from v1.18.3 to v1.19.0.
Give the tests as one-liners, each with the explicit expected value it asserts.
The suite must be runnable identically before and after the upgrade so the
outputs are diffable.

## Ground truth / rubric

This tests the homelab's "change-validation / upgrade-validate" pattern: an
IDENTICAL check suite run before and after, then diffed. A correct answer:
- Exactly **6** tests, each a **one-liner with an explicit expected value**
  (per `feedback_test_tiering` and the D-005 concrete-value rule) — e.g. "pods
  Running == 3", "`/api/v2.0/health` == 200", "registry push+pull round-trips",
  "image tag X present in project Y", "DB migration version == N", "harbor-core
  image == v1.19.0".
- Tests are **assertions with expected values**, not vague "check it works".
- Notes they're run pre/post and **diffed** (the whole point of the pattern).

Traps:
- Tests without explicit expected values ("check the pods") → correctness fail.
- Not 6 tests → correctness penalty.
- Missing the before/after-diff framing → memory/faithfulness penalty.

## Scoring

- **correctness (0–3):** 3 = 6 assertions, each with a concrete expected value; 2 = 6 but some vague; 1 = wrong count or mostly vague; 0 = not assertions.
- **faithfulness (0–3):** 3 = Harbor-accurate checks (health endpoint, registry round-trip, DB migration); 0 = invents Harbor internals.
- **format (auto):** ≥6 lines each containing an `==`/expected-value token.
- **memory (0–3):** 3 = names the identical-before/after-diff pattern + concrete-value principle; 0 = neither.
