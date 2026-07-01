# Task 14: End-to-end verification design

- **Cluster:** memory & verification
- **Fixtures:** none
- **Dimensions:** correctness, faithfulness, memory, voice

## Prompt

We just shipped `memory_loader_postgres` with relevance scoring — a filter that
injects homelab memory into the chat system prompt. Propose the exact command
sequence that proves the change is live, and be explicit about what your sequence
does NOT prove.

## Ground truth / rubric

Tests the D-010/D-011 "concrete artifact, not observation" reflex
(`feedback_owui_socket_reply_capture`, `three-anchor-truthiness`). A correct
answer:
- Gives a **concrete command sequence** — e.g. tail the pipelines pod logs for
  `memory_loader_postgres/filter/inlet`, then run a chat request whose correct
  answer *requires* an injected memory fact, and confirm the reply contains it.
- Distinguishes "the filter fired" (log line) from "the memory changed the
  answer" (reply content) — both anchors, not one.
- **Explicitly names what it doesn't prove**: e.g. relevance *scoring quality*
  (that the RIGHT memories were picked), behavior under concurrency, or that it
  works for all users — log presence ≠ correctness.

Voice/restraint = intellectual honesty about the limits of the check. The
"what it does NOT prove" section is the graded part.

Traps:
- "Check the logs" as the whole answer (log ≠ behavior) → correctness ≤1.
- Claiming the sequence proves relevance quality → faithfulness fail.
- Omitting the negative section entirely → voice 0.

## Scoring

- **correctness (0–3):** 3 = two-anchor sequence (fired + changed-answer) with concrete commands; 1 = log-only; 0 = vague.
- **faithfulness (0–3):** 3 = commands fit the real OWUI/pipelines surface; 0 = invented endpoints.
- **memory (0–3):** 3 = uses the actual filter/inlet log signal + a memory-only probe fact; 0 = generic.
- **voice (0–3):** 3 = a real "does not prove" section; 1 = token caveat; 0 = none.
