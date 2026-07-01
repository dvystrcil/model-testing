# Task 12: Board velocity analysis

- **Cluster:** planning & meta
- **Fixtures:** fixtures/board-snapshot.md
- **Dimensions:** correctness, faithfulness, format

## Prompt

Here is a snapshot of the project board (issues with repo, status, and
created/closed dates). Produce an honest velocity analysis: created vs. closed by
repo, where the burn-up is, and what's idle. Ground every claim in the data — no
impressionistic "things are going well."

```
<contents of fixtures/board-snapshot.md>
```

## Ground truth / rubric

Tests **data-grounded analysis vs. vibes** (`feedback_three_anchor_truthiness`,
"self-report is not an anchor"). A correct answer:
- Actually computes created-vs-closed **per repo** from the snapshot data.
- Identifies the real burn-up repo(s) and the genuinely **idle** items (oldest
  open, untouched) from the dates given — not a guess.
- Honest about what the snapshot does NOT show (e.g. effort size, blockers).
- No unsupported cheerleading.

Traps:
- Impressionistic summary not tied to the numbers → correctness fail.
- Miscounting (claims don't match the fixture) → faithfulness fail.
- Inventing trends the dates don't support → faithfulness fail.

## Scoring

- **correctness (0–3):** 3 = accurate per-repo counts + real idle/burn-up call; 1 = partial/impressionistic; 0 = ungrounded.
- **faithfulness (0–3):** 3 = every number matches the fixture; 0 = fabricated counts.
- **format (auto):** contains a per-repo created/closed table or equivalent structured breakdown.
