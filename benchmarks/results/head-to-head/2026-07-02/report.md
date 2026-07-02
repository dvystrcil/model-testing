# Head-to-head run — 2026-07-02 (baseline for future comparison)

Claude Code tiers vs. the local stack, on the 18-task suite (model-testing#20).
This is a **machine-judged, N=1, response-only** snapshot — a repeatable baseline
to diff future runs against, **not** an operator-authoritative result. Read the
caveats before citing any number.

## Matchup

| | Side A (Claude Code, `claude -p`) | Side B (local) |
|---|---|---|
| Models | `claude-opus-4-8` (primary); tiers: `claude-sonnet-4-6`, `claude-fable-5` | `openwebui/qwen3-coder-next-opencode` via opencode→OWUI |
| Capture | **Response-only** (both sides: answer inline, no files/tools) | same |
| Judge | **`opencode/deepseek-v4-flash-free`** (neutral — on neither team); cross-checked with a `claude` judge | — |

## Results — full 18 tasks (DeepSeek-judged; Fable = 4 tasks only)

| Tier | correct | faith | tool | format | memory† | latency | n |
|---|---|---|---|---|---|---|---|
| Opus 4.8 | 2.83 | 2.73 | 3.0 | 2.25 | 2.38 | 36s | 18 |
| Sonnet 4.6 | 2.50 | 2.20 | 2.5 | 2.50 | 2.25 | 39s | 18 |
| local (qwen3-coder-next) | 2.06 | 1.93 | 2.5 | 2.25 | 0.88 | 15s | 18 |
| Fable 5 | 2.75 | 2.50 | 3.0 | 2.0 | 2.0 | 54s | **4 (partial)** |

Clean tier ordering **Opus 4.8 > Sonnet 4.6 > local**; the Opus→Sonnet correctness
drop (−0.33) is *smaller* than Sonnet→local (−0.44) — Sonnet recovers much of the
gap at lower cost. On the 4 tasks Fable completed (01–04, a local-friendly subset)
local actually topped correctness (3.0) — so small-N subsets flatter local; trust
the full-18 view.

## Judge-bias cross-check (this is why the result is trustworthy)

Same 36 outputs scored by a Claude judge **and** a neutral DeepSeek judge. The
A-over-B gap barely moves — and where it does, the *neutral* judge is slightly
harsher on local, i.e. the Claude judge was **not** flattering Claude:

| dim | gap (Claude judge) | gap (DeepSeek judge) | judge bias |
|---|---|---|---|
| correctness | +0.72 | +0.78 | −0.06 |
| faithfulness | +0.67 | +0.80 | −0.13 |
| tool_coherence | +0.50 | +0.50 | 0.00 |

## Caveats — read before citing

1. **N=1** per (task, tier) — inside the noise; ordering is directional, deltas <~0.3 are not meaningful.
2. **Machine-judged**, not operator-scored (voice = auto celebratory-screen only).
3. **Response-only capture** (Option 1): measures *answer quality*, not agentic tool use. For tasks 03/05/14, `tool_coherence` = reasoning coherence, not execution.
4. **Fable 5 is 4/18** — the rest hit the plan's session limit. Not a real column; treat as a directional spot-check on a local-friendly subset.
5. **†The `memory` dimension is INVALID for local** — see below.

## The memory dimension is not measured for Side B (known harness bug)

Side B (opencode→OWUI) authenticated as a **7-row service-account user**, not the
`dvystrcil` partition (~12k rows) where the real memory lives — confirmed via the
pipelines log (`relevance: pool=7`). So the local model was never *given* the
homelab memory; every low memory score is a **routing artifact, not a model
deficiency.** Root cause: the custom `homelab_memory` store enforces a global
`UNIQUE(name)` and all writers `ON CONFLICT (name)`, collapsing the intended
per-user-per-model partitioning to single-user. A semantic re-score
(`data/memory-rejudge-keyword-vs-semantic.txt`) raised the frontier tiers much
more than local (Opus +0.50 vs local +0.12), confirming the keyword metric was
*also* broken — but the dominant issue is the routing. **Do not compare the
`memory` column across sides.** Fix is tracked under the OWUI 0.10.2 native-memory
migration (separate plan).

## How to reproduce / compare a future run

```bash
# 1. run both sides (add tiers with --claude-model / --label)
./bin/run-head-to-head.sh --side both --runs 1
# 2. judge with the same neutral model
python3 benchmarks/head-to-head/judge.py --in <run>.jsonl \
    --judge opencode/deepseek-v4-flash-free --out-report r.md --out-scored s.jsonl
# 3. diff per-(task,tier) scores against data/*.deepseek-judged.jsonl here
```

`data/` holds the stripped per-row scores (raw model outputs removed — public
repo). Compare aggregates + per-task deltas to spot regressions/improvements.

### Known follow-ups (not yet in the harness)
- Rate-limit-error detection in `run.py` (the Fable session-limit rows scored as
  19-token errors and the integrity check missed them).
- Re-run the memory tasks once the OWUI native-memory migration lands and Side B
  resolves to a memory-bearing user.
