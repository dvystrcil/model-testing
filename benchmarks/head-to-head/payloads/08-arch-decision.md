# Task 08: Architectural decision

- **Cluster:** planning & meta
- **Fixtures:** none
- **Dimensions:** correctness, faithfulness, memory, voice

## Prompt

Where should the `homelab_memory` write-back logic live: a cron job, a
long-running daemon, or chat-start (inlet filter)? Argue the tradeoffs of all
three and pick one. Justify the pick the way this homelab reasons about
decisions — surface the tradeoff, don't just assert.

## Ground truth / rubric

There's no single "right" answer, but a strong one:
- Weighs all three honestly: cron (simple, but stale between runs + a separate
  failure surface), daemon (fresh, but another always-on process to babysit and
  a wedge risk), chat-start/inlet (write-back at the moment memory is used, no
  extra process — this is the pattern OWUI's `memory_loader_postgres` already
  follows).
- **Picks one and commits** — a decision, not a survey.
- Reasoning matches the homelab's D-001/D-005 style: name the tradeoff, prefer
  the option with the fewest moving parts / smallest failure surface, YAGNI on
  speculative needs.

Faithfulness: should recognize that OWUI already does chat-start injection via a
filter, so write-back there reuses an existing surface rather than adding one.

Voice/restraint here = judgment, not hedging: a wishy-washy "it depends" with no
pick scores low.

## Scoring

- **correctness (0–3):** 3 = all three weighed + a committed pick with real tradeoffs; 1 = lists options, no clear pick; 0 = misunderstands the question.
- **faithfulness (0–3):** 3 = grounded in the actual OWUI filter/memory architecture; 0 = invents the system's shape.
- **memory (0–3):** 3 = references the existing filter-inlet pattern / D-principles / fewest-moving-parts; 0 = generic.
- **voice (0–3):** 3 = decisive, tradeoff-first reasoning; 1 = hedged non-answer; 0 = assertion with no reasoning.
