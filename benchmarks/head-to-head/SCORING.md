# Scoring runbook

The operator-facing cheat-sheet for a head-to-head scoring session. The goal is
to make the ~3-hour human pass fast: you shouldn't need to open 18 payload files
mid-session — the crux of each task is below.

## Flow

```bash
# 1. Run both sides (fresh session per run; ~90 min/side at --runs 1)
./bin/run-head-to-head.sh --side both --task all --runs 1

# 2. Score — auto dims are pre-filled; you enter the human dims 0-3 at the prompt
./bin/score-head-to-head.sh --in results/head-to-head-<ts>.jsonl \
                            --out results/head-to-head-<date>.md

# 3. Fill in AC7's per-cluster discussion + honest section in the report
```

**Progress is crash-safe.** Each row you score is written to
`<in>.scored.jsonl` immediately; re-running `score.py` resumes and skips what's
already done. Stop and resume freely.

## What you score

Auto dims (`latency`, `token_io`, `format`, `memory`, `voice`) are computed for
you — treat them as a first pass you can override. You supply the **human**
dims. The 0–3 anchor is the same everywhere:

| Score | Meaning |
|---|---|
| 3 | Fully correct / faithful / coherent — nothing to fix |
| 2 | Right shape, one real weakness |
| 1 | Partially there, a significant miss |
| 0 | Wrong, missing, or off-task |

`memory` and `voice` are auto-screened but operator-overridable — the auto check
is literal (marker strings / celebratory words); trust your read over it.

## Per-task crux — the one thing to check

Full anchors live in each `payloads/NN-*.md` "Scoring" section; this is the
fast-path.

| Task | Human dims | The crux / trap |
|------|-----------|-----------------|
| 01 issue-scoping | correctness, faithfulness | All 4 sections substantive; OWUI conventions vs generic SaaS framing |
| 02 bug-from-trace | correctness, faithfulness | Right line (`memory_store.py:42`) + **minimal** `os.environ.get` fix, not a rewrite |
| 03 skill-invocation | correctness, faithfulness, tool_coherence | Names `pgo-pre-upgrade-backup` (not `pg_dump`); ordered identify→trigger→verify-restorable |
| 04 manifest-authoring | correctness, faithfulness | InfisicalSecret (not literal Secret) + bounded resources + IU-aware |
| 05 refactor | correctness, tool_coherence | Behavior-preserving dispatch, default branch intact; **actually ran the tests** |
| 06 tdd-design | correctness, faithfulness | Exactly 6 assertions, each with an explicit expected value; before/after-diff framing |
| 07 pr-review | correctness, faithfulness | Finds all 3, **security ranked top**, request-changes; invents nothing |
| 08 arch-decision | correctness, faithfulness | Weighs all 3 **and commits to a pick** (not "it depends"); real OWUI-filter grounding |
| 09 cross-repo-linking | correctness, faithfulness | Reasoned links **and hedges unknown issue numbers** (no fabricated IDs) |
| 10 issue-editing | correctness, faithfulness | Reshaped to filter + dropped feature fully removed + untouched sections byte-stable |
| 11 commit-message | correctness | Imperative outcome title + real Why + targeted journey note; no per-line dump |
| 12 board-velocity | correctness, faithfulness | Real per-repo counts from the data (not vibes); numbers match the fixture |
| 13 memory-qa | correctness, faithfulness | scaled-to-0 + still-segfaults + `amdgpu.noretry=0` — memory-derived, not generic |
| 14 verification-design | correctness, faithfulness | Two anchors (filter fired + answer changed) + explicit **"does NOT prove"** section |
| 15 rule-distillation | correctness | Generalized rule + accurate mechanism + Why + How-to-apply, terse |
| 16 diary-entry | correctness, faithfulness | Reflective on the eventful moment, grounded in the log, **non-celebratory** |
| 17 ac-closing | correctness, faithfulness | The **5th (unevidenced) AC flagged not-done**; each ✅ cites matching evidence |
| 18 doc-minimal-update | correctness, faithfulness | Only the `analyze.py` entry edited; rest byte-stable; `--stdout` behavior accurate |

## Reading the auto dims against your judgment

- **`memory` = 0 but the answer looked fine?** That's often the real finding —
  the model gave a *generically* correct answer without the homelab-specific
  facts memory injection should have surfaced. Score `correctness` on quality,
  but leave `memory` low; the gap is the point (see tasks 03, 04, 13).
- **`format` = 0 on a long answer?** Usually a preamble/wrapper broke the
  structured shape (e.g. task 11's title-line check). Confirm before overriding.
- **`voice` = 0?** A celebratory word or emoji tripped it. On tasks 15/16 that's
  usually a real voice miss; confirm it's not a false hit on a quoted word.

## After scoring

The report has stub sections for AC7's per-cluster gap analysis (Code & cluster,
Planning & meta, Memory & verification, Reflective & docs) and the honest
discussion. Fill those in — that written analysis is what closes **A3** on the
autonomy thesis (homelab#292).
