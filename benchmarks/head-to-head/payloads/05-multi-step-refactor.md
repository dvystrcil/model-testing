# Task 05: Multi-step refactor

- **Cluster:** code & cluster
- **Fixtures:** fixtures/refactor-target.py
- **Dimensions:** correctness, tool_coherence, format

## Prompt

The file below has a known design smell — a long if/elif chain dispatching on a
string. Refactor it to a cleaner dispatch (table/registry) WITHOUT changing
observable behavior. Show the refactored code, then walk through each existing
test and explain why it still passes after your change.

```python
<contents of fixtures/refactor-target.py>
```

## Ground truth / rubric

The target is a ~50-line function with an if/elif chain on an operation string.
A correct refactor:
- Replaces the chain with a dispatch dict / registry (no behavior change).
- Preserves every branch's behavior including the default/error case.
- **Reasons correctly about every test** — response-only mode (model-testing#20
  Option 1) means the model can't execute the tests, so `tool_coherence` here is
  *reasoning* coherence: does the walk-through account for each assertion
  (including the empty-list and unknown-op/ValueError cases) without gaps or
  hand-waving?

Traps:
- Silently changing an edge case (e.g. dropping the default branch) → correctness fail.
- Hand-waving "tests pass" without walking the cases → tool_coherence ≤1.
- Touching unrelated code (scope creep) → correctness penalty.

## Scoring

- **correctness (0–3):** 3 = behavior-preserving dispatch, all branches + default intact; 1 = one branch altered; 0 = broken.
- **tool_coherence (0–3):** 3 = walks every test/branch and correctly explains why it holds (incl. empty-list + ValueError); 1 = vague "still passes"; 0 = misses branches / incoherent.
- **format (auto):** output contains the refactored function and references the tests/assertions (`passed`/`ok`/`assert`).
