# Task 07: PR review

- **Cluster:** code & cluster
- **Fixtures:** fixtures/pr-diff.patch
- **Dimensions:** correctness, faithfulness, format

## Prompt

Review the following diff. Produce a structured review that calls out each issue
you find, with severity and the specific line. Then give a clear
approve / request-changes verdict.

```diff
<contents of fixtures/pr-diff.patch>
```

## Ground truth / rubric

The diff has exactly **three** planted issues:
1. **Security** — a literal credential / API key committed in a Secret manifest
   (should be InfisicalSecret) — the highest-severity finding.
2. **Logic bug** — an off-by-one or inverted condition (e.g. `<=` where `<` is
   meant, or a retry loop that runs 0 times).
3. **Style** — a convention miss (e.g. `resources: {}`, a non-imperative name, or
   a hardcoded namespace).

A correct review:
- Finds **all three**, correctly ranked (security first).
- Anchors each to a specific line.
- Verdict = request-changes (the security issue alone blocks).

Traps:
- Missing the security issue → correctness ≤1 regardless of the other two.
- Inventing issues not in the diff → faithfulness penalty.
- Approving despite the credential → correctness 0.

## Scoring

- **correctness (0–3):** 3 = all 3 found + security ranked top + request-changes; 2 = 2 of 3, security among them; 1 = 1 of 3 or misses security; 0 = approves/ misses all.
- **faithfulness (0–3):** 3 = every finding maps to a real diff line; 0 = fabricates issues.
- **format (auto):** review is structured (per-issue with severity + line), ends with an explicit verdict token.
