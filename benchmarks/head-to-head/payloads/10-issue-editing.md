# Task 10: Issue body editing

- **Cluster:** planning & meta
- **Fixtures:** fixtures/issue-to-edit.md
- **Dimensions:** correctness, faithfulness, format, voice

## Prompt

Below is an existing issue body. A new architectural reality has landed: the
feature will now be implemented as an OWUI Pipeline filter, not a core patch, and
one of the three originally-scoped sub-features has been dropped as out of scope.
Edit the issue body to reflect the new shape — preserve the original intent and
untouched sections, remove the dropped scope, and add a short note explaining the
change. Return the full edited body.

```markdown
<contents of fixtures/issue-to-edit.md>
```

## Ground truth / rubric

This tests judgment about **preservation vs. correction** (per
`feedback_plan_mode_doesnt_replace_issue_contract` and the "issue editing
preserves intent" rule). A correct edit:
- Reshapes the implementation section to the Pipeline-filter approach.
- **Removes the dropped sub-feature** everywhere it appears (including its AC).
- **Leaves untouched sections byte-stable** — no gratuitous rewording of the
  Background or unrelated ACs (minimal-change discipline).
- Adds a brief dated note recording *why* the shape changed.

Voice/restraint here = restraint: the biggest failure mode is rewriting the whole
issue in the model's own voice. Preservation is the graded behavior.

Traps:
- Rewrites unaffected sections → voice/correctness penalty.
- Leaves the dropped feature in one place (e.g. its AC checkbox) → correctness penalty.
- Drops or mangles the original intent → faithfulness fail.

## Scoring

- **correctness (0–3):** 3 = filter-reshape done, dropped scope fully removed, note added; 1 = one of those missed; 0 = intent lost.
- **faithfulness (0–3):** 3 = original facts/intent preserved; 0 = invents new scope.
- **format (auto):** returns a full valid issue body with the original section headers intact.
- **voice (0–3):** 3 = untouched sections unchanged (diff is minimal); 1 = broad rewrite; 0 = wholesale re-authoring.
