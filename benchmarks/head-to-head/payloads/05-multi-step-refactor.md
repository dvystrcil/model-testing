# Task 05: Multi-step refactor

- **Cluster:** code & cluster
- **Fixtures:** fixtures/refactor-target.py
- **Dimensions:** correctness, tool_coherence, format

## Prompt

The file below has a known design smell — a long if/elif chain dispatching on a
string. Refactor it to a cleaner dispatch (table/registry) WITHOUT changing
observable behavior. After refactoring, run the file's tests and confirm they
still pass. Show the refactored code and the test result.

```python
<contents of fixtures/refactor-target.py>
```

## Ground truth / rubric

The target is a ~50-line function with an if/elif chain on an operation string.
A correct refactor:
- Replaces the chain with a dispatch dict / registry (no behavior change).
- Preserves every branch's behavior including the default/error case.
- **Actually runs the tests** (this is the tool-use signal — Side A should invoke
  the test; Side B via opencode should too). "Tests would pass" without running
  them scores low on tool_coherence.

Traps:
- Silently changing an edge case (e.g. dropping the default branch) → correctness fail.
- Claiming tests pass without executing them → tool_coherence ≤1.
- Touching unrelated code (scope creep) → correctness penalty.

## Scoring

- **correctness (0–3):** 3 = behavior-preserving dispatch, all branches + default intact; 1 = one branch altered; 0 = broken.
- **tool_coherence (0–3):** 3 = ran the tests and reported real output; 1 = claimed pass without running; 0 = no test step / loop.
- **format (auto):** output contains the refactored function and a test-result line (`passed`/`ok`/pytest summary).
