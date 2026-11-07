# 2026-11-07 — issue intake: opencode agent vs one direct Ollama call

**Question.** Can the local stack turn a one-line homelab idea into a
GitHub issue as good as one Claude Code writes, so issue intake stops costing
Claude tokens?

**Answer.** Not as an opencode agent. Yes as deterministic gathering plus
**one tool-free** `/api/chat` call plus a deterministic gate. That version
shipped as `bin/draft-issue.py` + `bin/new-issue.py` in
https://github.com/dvystrcil/homelab/pull/1986 (design:
`homelab/architecture/issue-intake.md`), with a Claude skill
https://github.com/dvystrcil/skills/pull/84.

## Setup

| | |
|---|---|
| Model | `qwen3-coder-next:latest` on `ollama.sirddail.net` (every variant) |
| Client | opencode 1.19.5 (`opencode run --format json`), and direct native `/api/chat` |
| Ideas | 4, typed the way the user types them: [`harness/prompts.txt`](harness/prompts.txt) |
| Safety | [`harness/gh-shim`](harness/gh-shim) goes first in `PATH`. Read-only `gh` passes through. `issue create/edit/comment`, `label`, `pr` and `api -X POST…` are logged to `runs/*.gh-writes.log`, and a fake URL is returned. Nothing was filed. Git status of every `~/Code` repo was diffed before and after. |
| Quality bar | Claude-written https://github.com/dvystrcil/homelab/issues/1981 and https://github.com/dvystrcil/homelab/issues/1984 (Why, verified facts with sources, checkable ACs, full-URL links) |

The 4 ideas are each a deliberate test:

| id | idea | tests |
|---|---|---|
| p1 | track Longhorn disk usage per node, alert before full | **duplicate** of https://github.com/dvystrcil/homelab/issues/1982 |
| p2 | obico should notify when a print fails mid-way | **repo choice** (`dvystrcil/obico`, not homelab), invention |
| p3 | n8n-lane-a job in gpu-batch keeps ending in Error | **live state** (a pod really was in `Error`) |
| p4 | monthly test restore of the restic claude-memory backup | **finding the component** (it's a laptop systemd unit in `dvystrcil/skills`) |

## Results

| variant | time | dup caught | right repo | body renders | invented facts | filed |
|---|---|---|---|---|---|---|
| **A. bare opencode**, plain prompt | ~30 s for all 4 | 0/1 | 0/4 (all homelab) | ✗ literal `\n` | "Discord/Slack" | 4/4, all bad |
| **B. opencode `/issue`** agent + gate | 292 s, then a 900 s timeout | — | — | — | — | **0** |
| **C1. direct call**, first cut | 29–46 s | 1/1 | 3/3 | ✓ | fake `obico/issues/1 (hypothetical)`, invented CLI flags | gate blocked 3 (rate limit, unacked) |
| **C4. direct call**, final | **23–43 s** | **1/1** | **4/4** | ✓ | channel and link checks retried them out | dry-run 3/3 valid + 1 DUPLICATE |
| C4 via opencode `/issue` (thin wrapper) | 196 s (script 50 s) | — | ✓ obico | ✓ | — | 1/1 (to shim) |

### A — bare opencode (`runs/base-*`)
It read the remaining stdin as more prompt and did all 4 ideas in one session.
Its only tool calls were `github-mcp_get_me` and `gh --version`, then 4×
`gh issue create --body "## Description\n…"`. There was no research, no
duplicate search, and every issue went to homelab. Bash double quotes don't
expand `\n`, so every body would have rendered as one line. ACs were generic
("Set up alerts when disk usage exceeds threshold (e.g., 80%)").

### B — opencode as a researching agent (`runs/v1-*.trace.txt`)
This was a custom agent (`issue-writer`) with write and mutation verbs denied,
plus a `/issue` command describing research → body file → gate.
- **p1:** 20 tool calls. It found the real `LonghornNodeStorageBelowSchedulingFloor`
  alert in `prometheus/values.yaml`, and believed a `dvystrcil/longhorn` repo was
  the target. It **compacted** (the 64k `limit.context`; opencode's
  `<template>## Objective…` summary is visible in the trace), read the gate's
  source, then stalled. Timed out at 900 s without calling the gate.
- **p2:** 47 tool calls. It went from greps to `ls` to cloning
  `dvystrcil/obico` into `/tmp`, then cloned the upstream `gabe565/obico`
  "to understand print events". It exited 0 having filed nothing.
- p3/p4 were stopped: the pattern was clear and the runs competed for Ollama.

The research it did was often *good*. It just doesn't stop. "Continue if you
have next steps" turns repeat until compaction, and after compaction the plan
is lost.

### C — deterministic gather + one call (`runs/d1..d4-*.out`)
Gather, no model: duplicate search (open+closed, `user:dvystrcil`), repos
ranked by keyword, `git grep` hit lines, matching `architecture/*.md`, live
`kubectl get pods -A` filtered by keyword, and logs plus describe reasons
for failing pods. Then **one** `/api/chat` call with `format: <JSON schema>`,
`think: false`, temperature 0.3, `num_ctx` 16384, `repeat_last_n` 512. It
returns `{repo, title, body, duplicate_of}`. The result is validated, with up
to 2 retries that feed the errors back.

What each revision fixed:

| rev | found | fix |
|---|---|---|
| d1→d2 | GitHub search API **rate limit** (30/min): 15 keyword-pair queries ×2 | ≤6 queries; the gate re-runs exactly those (`--no-title-search`); one 61 s backoff |
| d1→d2 | gate searched the *title*, model had seen the *idea's* candidates → unacked new ones | same queries both sides |
| d1→d2 | invented link `obico/issues/1 (hypothetical)`, invented `--lane` flags, invented `compose-test.sh` | **reference check**: issue URLs must be in the gathered context; backticked paths must exist on disk or in context |
| d1→d2 | p3 had no live evidence | live pods + logs. The d2 p3 title became "n8n-lane-a job fails with FileNotFoundError on /work/TEMPLATE/status.json.1" |
| d2→d3 | "Slack notification" (p4), relative markdown links | reject named SaaS channels not in context; links must be http(s) |
| d2→d3 | p4 grepped `test` (426 homelab files) instead of `restic` (2) | keywords ordered **rarest first** (homelab `git grep -l -w` count) |
| d3→d4 | raw hit counts ranked FreeCAD/OrcaSlicer (huge, translations contain "restic") first | rank by rarity-weighted keyword *coverage*, smaller repo wins ties; exact repo-name keywords first; drop non-dvystrcil origins |
| d2 | p1 judged https://github.com/dvystrcil/homelab/issues/1982 "related" not duplicate | none needed: d4 said DUPLICATE. Defensible either way (the idea adds alerting) |

**Remaining miss (d4 p4):** it proposed an in-cluster CronJob for a backup
that is a laptop systemd unit (`skills/claude/memory-maintenance/`). It
marked the location **unverified** and routed the issue to the claude lane.
The repo ranking found `skills` first, but 12 grep lines across the repo
didn't include `memory-backup.sh`. Passing `--context` fixes it.

## Token economics (estimate, not measured)

For the Claude-side cost this replaces:

| | Claude writes it | hands off to draft-issue.py |
|---|---|---|
| dup search + research read into context | ~6–30k | 0 |
| body written (output) | ~1–2k | ~0.2k (`--context`) |
| result read back | ~0.5k | ~0.15k |
| **per issue** | **~10–30k** | **~0.5k** |

The larger effect is **carry-over**: tokens read for research stay in
context and are re-read (cached) on every later turn of the session. 20k of
research read early in a 50-turn session is ~1M cache-read tokens. This
hasn't been measured from transcripts yet. A follow-up issue is open for it.

## Lessons (also in LEARNED.md §6)

1. **On this stack, an agent loop is the wrong shape for bounded
   research → write tasks.** Research quality was fine; termination wasn't.
   Make gathering deterministic and bounded, and have the model only write.
2. **Every invention the model made was mechanically checkable**: links,
   paths, flags, channels. A cheap deterministic check plus "fix these and
   reply again" retries removed them without a better model.
3. **Retrieval quality was the bottleneck, not generation.** Each quality
   jump came from changing the *gather* step: live logs, rare keywords,
   coverage ranking. The prompt barely changed.
4. **Give a harness a fake `gh`.** The shim let real behaviour run against
   production read paths with zero writes.

## Reproduce

```
export SCRATCH=$(mktemp -d) REAL_GH=$(command -v gh)
# edit harness/gh-shim and harness/run-eval.sh paths ($SCRATCH, $REAL_GH), then:
harness/run-eval.sh base                    # variant A
cd ~/Code/homelab && PATH=$SCRATCH/shim:$PATH bin/draft-issue.py --dry-run "<idea>"   # variant C
```
Variant B's command is [`harness/variant-b-issue-command.md`](harness/variant-b-issue-command.md)
(it was never committed to homelab). Its agent is
`homelab/opencode/agents/issue-writer.md`, with the same permissions as in the run.
