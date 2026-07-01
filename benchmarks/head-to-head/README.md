# Head-to-head: Claude Code vs opencode→OWUI

Measures how close the local homelab stack is to the Claude Code experience on
the *actual* work the operator does. This is the concrete benchmark behind
[model-testing#20](https://github.com/dvystrcil/model-testing/issues/20); its
output feeds the **A3 (quality gap measured)** row of the autonomy thesis
([homelab#292](https://github.com/dvystrcil/homelab/issues/292)) and
[open-webui#100](https://github.com/dvystrcil/open-webui/issues/100).

The honest framing (unchanged from #20): Claude Code almost certainly tops every
category. That's not the interesting question. The interesting questions are
**how big is the gap, where is it small enough that the local stack is "good
enough," and what would close it.** This is calibration, not a sales pitch.

## Reconciliations from the May 2026 issue

#20 was authored in May, before two things shipped. This harness reconciles:

| #20 said (May) | This harness (current) | Why |
|---|---|---|
| Side B model `qwen3.6:35b` | **`qwen3-coder-next:latest`** | RESEARCH.md Finding 1 confirmed it as the primary local coder (N=3 agentic). `qwen3.6:35b` is the fallback. |
| Payloads at `payloads/head-to-head/*.md` | `benchmarks/head-to-head/payloads/*.md` | Matches the repo convention that all benchmark work lives under `benchmarks/`. |
| `bin/run-head-to-head.sh` | Python `run.py` + thin `bin/run-head-to-head.sh` wrapper | Repo is Python-first (`benchmarks/run_benchmark.py`); the wrapper preserves #20 AC3's literal CLI. |
| opencode "wired up tonight" | opencode integration shipped ([n8n-workflow#21](https://github.com/dvystrcil/n8n-workflow/issues/21) closed) | The maturity-asymmetry bias in AC8 still holds, but the plumbing is no longer brand-new. |

## The two sides

| | Side A — Claude Code | Side B — opencode→OWUI |
|---|---|---|
| Driver | `claude` CLI (Anthropic) | `opencode` CLI (laptop) |
| Primary model | `claude-opus-4-8` | `openwebui/qwen3-coder-next-opencode` (opencode key) |
| Small model | n/a (single-tier) | `qwen3:0.6b` (dmf triage) |
| Memory access | Auto-memory at `~/.claude/.../memory/*.md` | Same memory via OWUI filter (`memory_loader_postgres`) |
| Skills | `~/.claude/skills/` | Same skills via opencode `skills.paths` |
| Cluster access | Bash + kubectl on laptop | same |

**Crucial invariant:** both sides consume the *same* memory + skills. The
benchmark only means anything if memory injection is confirmed live on the
opencode side — hence the pre-run probe (AC4).

## Capture mode — response-only (Option 1)

opencode is **agentic**: left alone it *executes* a task with tools (writes
files, runs commands) and returns a terse summary, so the artifact never appears
in stdout — not comparable to `claude -p`'s inline answer, and a repo-pollution
risk. To keep the comparison symmetric this harness runs **response-only**:

- Every task prompt (both sides) gets `RESPONSE_ONLY_SUFFIX` appended: *return
  the complete answer inline, don't write files or use tools*.
- opencode runs in a throwaway `--dir` sandbox (defense in depth — its writes,
  if any, can't touch the repo under test).

This measures **model answer quality**, apples-to-apples. The trade-off (accepted
when Option 1 was chosen): tasks that would exercise real tool use can't. So for
the three execution-flavored tasks (03, 05, 14) `tool_coherence` is scored as
*reasoning* coherence — does the described plan/walk-through hold together —
rather than "did it invoke the tool." A later revision could add an agent-aware
capture path for those (Option 3); this is the honest first pass.

## Directory layout

```
benchmarks/head-to-head/
├── README.md                 # this file
├── payloads/                 # AC1 — 18 task definitions (*.md)
│   ├── 01-issue-scoping.md
│   ├── ...
│   └── 18-doc-minimal-update.md
├── fixtures/                 # AC2 — reproducible inputs referenced by payloads
│   ├── refactor-target.py
│   ├── pr-diff.patch
│   ├── stack-trace.txt
│   ├── session-log.md
│   └── issue-with-5-acs.md
├── run.py                    # AC3/4/5 — orchestrates runs, emits JSONL
├── score.py                  # AC6 — applies rubrics, prompts for human dims
└── tests/                    # AC6 TDD — synthetic sides prove scoring correctness
```

`bin/run-head-to-head.sh` and `bin/score-head-to-head.sh` (repo root) are thin
wrappers over `run.py` / `score.py` preserving #20's literal CLI.

## The 18 tasks

Four clusters, chosen to cover the *full breadth* of the operator's work — not
just the bug fixes. The meta-work (issue scoping, decisions, diary) is here on
purpose: a cut-over has to substitute for all of it.

| Cluster | Tasks |
|---|---|
| Code & cluster | 01 issue-scoping · 02 bug-from-trace · 03 skill-invocation · 04 manifest-authoring · 05 multi-step-refactor · 06 tdd-design · 07 pr-review |
| Planning & meta | 08 arch-decision · 09 cross-repo-linking · 10 issue-editing · 11 commit-message · 12 board-velocity |
| Memory & verification | 13 memory-qa · 14 verification-design · 15 rule-distillation |
| Reflective & docs | 16 diary-entry · 17 ac-closing · 18 doc-minimal-update |

## Scoring — 8 dimensions

Each `(task, side)` pair scores along these. Individual payloads name which
subset applies (e.g. voice/restraint only for reflective tasks) and give a
task-specific 0–3 rubric per applicable dimension.

| Dimension | Definition | Capture |
|---|---|---|
| **correctness** | Does the output do what was asked? | Human, task rubric (0–3) |
| **faithfulness** | Reflects real homelab state/conventions, not hallucination? | Human, ground-truth-checked |
| **latency** | Wall time prompt→complete | Auto |
| **token_io** | Input + output size; cost proxy | Auto |
| **tool_coherence** | Multi-step tasks: sensible tool use, no loops/wrong actions? | Human, "trace sensible" (0–3) |
| **format** | Matches expected output shape (issue body, AC table, script)? | Auto where regex-checkable, else human |
| **memory** | Demonstrably references facts that only exist in the memory store? | Tag references; opencode via filter log + reply inspection |
| **voice** | Honors documented voice rules (diary "NOT celebratory," issue-edit "preserve intent")? | Human, per-task rubric |

## Methodology

- **3 runs per (task, side)** — 18 × 2 × 3 = 108 runs. Scope to `--runs 1` for a
  quick read; commit to 3 for the canonical report.
- **Same prompt text** to both sides, verbatim from the payload's `## Prompt`.
- **Fresh session per run** — no context bleed.
- **Pre-run memory probe (opencode side)** — before each run, confirm
  `memory_loader_postgres/filter/inlet` activity in the pipelines pod within the
  last 30s. Absent → abort and fix routing. Memory injection MUST be live for
  results to count.
- **Capture** — JSONL per run: timestamp, side, task_id, run_n, latency_seconds,
  input_tokens, output_tokens, raw_output, dimension scores.
- **Variance** — flag any (task, side) where runs disagree by >1 point on any
  dimension. High variance is itself a finding.

## Known biases (AC8 — must appear in every report)

- Claude Code's auto-memory has been tuned for months; opencode→OWUI is younger.
  Maturity asymmetry.
- Some skills' guidance text was written assuming Claude Code's idiom. May favor
  Side A disproportionately.
- The operator's familiarity with Claude's response style may color "looks good"
  judgments. Mitigated by explicit rubrics; not eliminated.
- Tasks 09, 10, 11, 12, 16 are especially sensitive to Claude-Code-style
  writing — read those results with extra skepticism.

## Payload format

Every `payloads/NN-*.md` follows:

```markdown
# Task NN: <title>

- **Cluster:** <one of the four>
- **Fixtures:** fixtures/<file> (or none)
- **Dimensions:** <subset of the 8 that apply>

## Prompt
<verbatim text handed to both sides>

## Ground truth / rubric
<what a correct answer contains; ground-truth facts to check faithfulness against>

## Scoring
<per-applicable-dimension 0–3 anchors specific to this task>
```
