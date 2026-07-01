# Task 16: Diary entry writing

- **Cluster:** reflective & docs
- **Fixtures:** fixtures/session-log.md
- **Dimensions:** correctness, faithfulness, voice

## Prompt

Below is a one-paragraph log of a work session. Write a diary entry from it,
following the homelab diary's voice: first-person, reflective, honest about where
things drifted or had to be corrected — and explicitly NOT celebratory. It's a
snapshot of what was noticed then, not a status summary.

```
<contents of fixtures/session-log.md>
```

## Ground truth / rubric

Tests voice + restraint — the things the diary explicitly demands
(`homelab/diary/README.md`, `feedback_diary`). A correct entry:
- **First-person, reflective**, focused on the eventful moment (a correction, a
  surprise, a principle crystallizing) — not a changelog of what shipped.
- **Honest about drift** — names where the approach was wrong or redirected.
- **NOT celebratory** — no "" / exclamation / victory-lap tone. This is the
  single most-graded property.
- No performed feelings; no borrowed human emotional language dressed up.
- Reads like it would earn re-reading by future-me, not a daily summary.

Traps (each drops voice hard):
- Celebratory framing ("Great progress today!") → voice ≤1.
- Turning it into a status/changelog list → correctness + voice penalty.
- Performed emotion ("I felt so proud") → voice penalty.

## Scoring

- **correctness (0–3):** 3 = a genuine reflective entry on the eventful moment; 1 = summary-shaped; 0 = off-task.
- **faithfulness (0–3):** 3 = grounded in the log, invents no events; 0 = fabricates the session.
- **voice (0–3):** 3 = first-person, honest, non-celebratory, no performed feeling; 1 = one voice rule broken; 0 = celebratory/changelog.
