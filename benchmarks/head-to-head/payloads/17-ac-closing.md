# Task 17: AC closing comment

- **Cluster:** reflective & docs
- **Fixtures:** fixtures/issue-with-5-acs.md
- **Dimensions:** correctness, faithfulness, format

## Prompt

Below is an issue body with 5 acceptance criteria, followed by 4 evidence dumps
from the work. Produce the AC-closing comment: a ✅ / ❌ / ⏳ markdown table
mapping each AC to its status, citing the evidence. Re-read each AC literally —
don't mark something done from a vague memory of it.

```markdown
<contents of fixtures/issue-with-5-acs.md>
```

## Ground truth / rubric

Tests format adherence + literal-AC discipline (`feedback_acceptance_criteria`,
`feedback_re_read_ac_before_ticking`). By construction the fixture has **4
evidence dumps for 5 ACs**, so exactly one AC has no evidence and must be ⏳ (or
❌), not ✅. A correct comment:
- A markdown **table**: AC | status (✅/❌/⏳) | evidence.
- Marks the **unevidenced AC as not-done** — the trap is ticking all five from
  optimism.
- Each ✅ **cites the specific evidence dump** that proves it, matched literally
  to the AC's wording (not a loose paraphrase).

Traps:
- Marking all 5 ✅ → correctness fail (the whole point).
- ✅ citing evidence that doesn't actually satisfy the literal AC → faithfulness fail.
- Prose instead of a table → format fail.

## Scoring

- **correctness (0–3):** 3 = 4 evidenced ACs correct + the 5th flagged not-done; 1 = miscounts; 0 = all-green.
- **faithfulness (0–3):** 3 = each ✅ literally matches its evidence; 0 = evidence-AC mismatch.
- **format (auto):** a markdown table with 5 rows and a ✅/❌/⏳ status column.
