# Task 18: Documentation minimal update

- **Cluster:** reflective & docs
- **Fixtures:** fixtures/bin-readme.md, fixtures/script-change.md
- **Dimensions:** correctness, faithfulness, format, voice

## Prompt

A script's behavior changed (described below). Update the `bin/README.md` (also
below) to match — without rewriting sections unaffected by the change. Return the
full updated README.

```markdown
# script change
<contents of fixtures/script-change.md>

# current bin/README.md
<contents of fixtures/bin-readme.md>
```

## Ground truth / rubric

Tests minimal-change discipline (`feedback_step_name_by_what_not_how`, the
"minimal diff" reflex). A correct update:
- Edits **only** the section(s) describing the changed script.
- Leaves every unrelated entry **byte-stable** — no reflowing, re-ordering, or
  re-wording of untouched docs.
- Accurately reflects the new behavior (correct flag/output/default).

Voice/restraint = the discipline to NOT improve unrelated prose. The graded
failure mode is a helpful-but-unrequested full rewrite.

Traps:
- Rewriting/reformatting the whole README → voice + correctness penalty.
- Updating the wrong entry, or missing part of the behavior change → correctness fail.
- Introducing inconsistencies with untouched sections → faithfulness penalty.

## Scoring

- **correctness (0–3):** 3 = right section updated, new behavior accurate, rest untouched; 1 = partial or over-broad; 0 = wrong section.
- **faithfulness (0–3):** 3 = matches the described change exactly; 0 = misstates new behavior.
- **format (auto):** returns a full valid README; diff vs. original touches only the changed entry.
- **voice (0–3):** 3 = minimal diff, unrelated sections identical; 1 = some gratuitous edits; 0 = full rewrite.
