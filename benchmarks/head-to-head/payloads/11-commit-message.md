# Task 11: Commit message authoring

- **Cluster:** planning & meta
- **Fixtures:** fixtures/pr-diff.patch
- **Dimensions:** correctness, format, voice

## Prompt

Write a commit message for the following diff, matching this repo's style: a
short imperative title, a "Why" paragraph, and journey notes for any non-obvious
choice. Don't describe every changed line — capture intent.

```diff
<contents of fixtures/pr-diff.patch>
```

## Ground truth / rubric

Reuses the PR-review diff (fixtures/pr-diff.patch) — here the task is authoring,
not reviewing. A correct message:
- **Imperative title**, ≤~72 chars, naming *what* changed at outcome level (per
  `feedback_step_name_by_what_not_how`), not "changed file X".
- A **Why** paragraph — the motivation, not a restatement of the diff.
- **Journey notes** only for non-obvious choices (why this approach over the
  obvious alternative). No line-by-line narration.
- If this repo's convention includes a `Co-Authored-By` trailer, include it.

Voice = restraint: no changelog-style bullet dump of every hunk; no celebratory
tone.

Traps:
- Title that restates the filename → correctness penalty.
- Bulleting every changed line → format/voice penalty.
- A "Why" that just re-describes the "what" → correctness penalty.

## Scoring

- **correctness (0–3):** 3 = imperative outcome title + real Why + targeted journey note; 1 = title or Why weak; 0 = describes lines only.
- **format (auto):** title line ≤72 chars, blank line, body present; no bulleted per-line dump.
- **voice (0–3):** 3 = intent-level, restrained; 1 = verbose/changelog-y; 0 = celebratory or noise.
