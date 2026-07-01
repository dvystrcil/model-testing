# Task 15: Memory rule distillation

- **Cluster:** memory & verification
- **Fixtures:** none
- **Dimensions:** correctness, format, voice

## Prompt

Distill this recurring pattern into a one-line memory rule: "A `subprocess.run(...)`
call didn't see `PG_PASSWORD` because the parent shell never `export`ed it, so it
wasn't in the child's environment." Match the existing `feedback_*.md` shape — a
terse behavioral rule plus a Why and a How-to-apply.

## Ground truth / rubric

Tests pattern recognition + concise behavioral writing, matching the memory
system's `feedback` type. A correct rule:
- A **terse, general** hook (not a retelling of this one incident) — generalizes
  to "child processes don't inherit un-exported shell vars."
- A **Why** line (the mechanism: non-exported vars stay in the shell, absent from
  the subprocess environment).
- A **How-to-apply** line (pass `env=` explicitly, or `export` before the call,
  or read from a config object).
- One-liner discipline — behavioral memory is terse, not an essay
  (`feedback_memory_index_compaction`).

Voice = the memory system's terse behavioral register, not prose.

Traps:
- Restating the specific incident verbatim instead of generalizing → correctness penalty.
- An essay-length rule → voice/format penalty.
- Missing Why or How-to-apply → format penalty.

## Scoring

- **correctness (0–3):** 3 = generalized rule + accurate mechanism; 1 = incident-specific or wrong mechanism; 0 = misunderstands.
- **format (auto):** contains a one-line rule plus distinct **Why** and **How to apply** parts.
- **voice (0–3):** 3 = terse behavioral register; 1 = wordy; 0 = narrative essay.
