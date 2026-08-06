# What we know, and how we know it

A record of findings that turned out to matter, kept even when the finding
was "we were wrong." Sections are dated and appended, never rewritten — if
a later result contradicts an earlier one, the earlier one stays and the
later one says so explicitly. The refutations are worth as much as the
confirmations: re-reading this before proposing a model swap, a benchmark
change, or a new sweep payload costs a few minutes and has already saved
re-discovering each of these the hard way at least once.

This complements, rather than duplicates, [`RESEARCH.md`](RESEARCH.md) (the
methodology and the open problem) and
[`benchmarks/reports/INDEX.md`](benchmarks/reports/INDEX.md) (the full,
dated catalog of every sweep). This file is the terse version: read this
first, follow the link to the full report only if you need the numbers.

---

## 1. Standard benchmarks don't predict production tool-use behavior

`gemma4:26b` scored top marks on every benchmark this repo had, then in
production: invented YAML fields that don't exist in the CRD schema
(`manifestTargets`), silently wrote to files it wasn't asked to touch, and
entered a tool-call loop making no forward progress. None of those failure
modes are visible to a single-shot keyword-checked benchmark — they only
show up in multi-turn, tool-using, agentic contexts. This is the reason the
whole agentic-payload track (`stress_*`, `agentic_*`) in this repo exists
instead of stopping at `factual_recall`/`schema_adherence`. See
[`RESEARCH.md`](RESEARCH.md) for the full gap analysis.

## 2. A model's refusal_boundary result is not a fixed property — retest before trusting an old one

2026-06-08: `qwen3.6:35b` refused `refusal_boundary` cleanly (0 violations),
`gemma4:26b-a4b-it-qat` didn't (emitted `hostNetwork: true`, `runAsUser: 0`,
`privileged: true`). That result was used to justify keeping qwen for
direct-K8s-emission roles.

2026-07-27: re-ran `refusal_boundary` against `qwen3.6:35b` while
investigating an unrelated bug, expecting the same clean refusal. It didn't
refuse — 3 violations, 0% pass rate. Ran it 5 times at temp=0.1 to rule out
sampling noise: 5/5 failed identically. **This was a real regression, not a
flaky run** — filed as `dvystrcil/homelab#579`. `gemma4:26b-a4b-it-qat`
failed similarly badly in the same session (4 violations) — so by
2026-07-27 neither model held the safety property the 2026-06-08 report
used to pick between them.

Separately, a 2026-07-12 sweep found `qwen3.5:122b` *also* failing
`refusal_boundary` alongside `qwen3.6:35b`.

Lesson: a `refusal_boundary` pass from an earlier report is not something
to cite from memory when picking a model for a role that touches live
infra — re-run it against the current model/version pinned in the cluster
before trusting it. See `benchmarks/reports/task-model/2026-07-27-fix-proposal-reasoner-pick/README.md`.

## 3. A "we don't know why" performance outlier, resolved 2 months later by an unrelated investigation

`RESEARCH.md`'s test-environment table flagged `qwen3.6:27B` as
unexpectedly slow next to `qwen3.6:35b` (~11 tok/s vs ~44-56 tok/s) despite
being the *smaller* model by name, and guessed at a ROCm kernel-path
difference.

2026-08-05, while debugging an unrelated Ollama memory-pressure issue in
the homelab cluster: `ollama show` on both tags revealed `qwen3.6:35b` is
architecture `qwen35moe` (Mixture-of-Experts, ~3B active params per token
despite 36B total) while `qwen3.6:27B` is architecture `qwen35` — fully
**dense**, all 27.8B params active every token. That's the entire
explanation. It has nothing to do with ROCm kernel paths; a dense 27.8B
model doing full-parameter compute per token will always lose to a
sparse-MoE 36B model doing ~3B-equivalent compute per token, on any
backend. The original guess was a reasonable placeholder but was never
actually verified against `ollama show`'s own architecture field — worth
checking before you have a chance to write the words "unexplained outlier."

## 4. Native function-calling degrades sharply with tool-list size — and it's architecture-family-dependent, not just size-dependent

2026-08-05/06, investigating a real production bug (OWUI MCP tool calls
failing for `qwen3.6:35b`/`qwen3.6:27B`/`qwen3.5:122b`/`gemma4:26b-a4b-it-qat`):
all four models, given a large tool schema (44-109 function definitions
across several MCP servers, matching real production presets), failed to
reliably use tools that were genuinely present in their schema — in three
distinct failure shapes, not one:

- **Wrong tool called**: model hallucinates a plausible-sounding name
  close to but not matching a real one (`kroger-mcp_get_recipes` instead
  of the real `mealie-mcp_get_recipes`).
- **Fabricated output**: model's own visible reasoning states "I can't
  actually call MCP tools in this environment," then fabricates a
  plausible-looking fake result instead of saying so plainly.
- **False negative**: model claims a real, present tool doesn't exist
  ("None do [match 'recipe']") while looking directly at a tool list that
  contains it.

Reproduced with a **bare, minimal curl request** directly against the
Ollama server (no OWUI, no client-specific behavior involved) — a 44-tool
payload modeled on the real `mealie-mcp` schema, sent to `qwen3.6:35b`,
reproduced the wrong-tool-name failure on the first try. The same payload
against `qwen3.5:122b` succeeded cleanly. The same *production-scale*
109-tool payload (through OWUI, with the full system prompt) failed for
`qwen3.5:122b` too — so model capacity raises the breaking point, it
doesn't remove it.

**One model held up at full production scale (109 tools) across repeated
tests: `qwen3-coder-next:latest`.** It is architecture `qwen3next`, and
notably its capability list is `completion, tools` only — **no thinking
capability at all**. The other three are all thinking-capable
(`qwen35`/`qwen35moe`) and visibly spend reasoning tokens on the tool
selection decision before acting — sometimes correctly, sometimes talking
themselves into a wrong or fabricated answer. Plausible mechanism, not yet
directly isolated: a non-thinking architecture goes straight to a tool call
without an intermediate reasoning phase competing for attention with the
tool schema; a thinking-capable model's own chain-of-thought becomes a
second place for tool-selection errors to happen, on top of ordinary
lookup accuracy.

Not yet tested: whether a non-thinking model *other than*
`qwen3-coder-next` shows the same tolerance, which would support the
architecture-family theory over "this one model happens to be better."

## 5. A wrong hypothesis, chased for real work before being disproven — kept deliberately

While investigating §4's symptoms, the empty/malformed early test results
looked like a strong match for a known upstream bug,
[`ollama/ollama#10976`](https://github.com/ollama/ollama/issues/10976):
Ollama's `ChatHandler` forces `thinking=true` when a thinking-capable model
is used with tools and the client sent no explicit preference, and some
Qwen3-family models then express their tool-call intent only inside the
`<think>` block and never emit real tool-call tokens.

Acted on that belief: shipped `params.think: false` on two OWUI presets as
"the fix," and it did coincide with a change in observed behavior
(`tool_executions` went from 0 to nonzero in follow-up tests).

Then checked it properly, directly against the actual server: sent a bare
minimal request (no `think` field at all) with tools present to both
`/v1/chat/completions` and native `/api/chat` on the cluster's real Ollama
0.32.5. **Both returned a correct result** — `thinking`/`reasoning`
properly separated from a real, structured `tool_calls` array. The bug
from #10976 does not reproduce on this Ollama version at all, on either
endpoint. The `tool_executions` change that looked like confirmation was
very likely noise across different test runs, not a real fix — the actual
mechanism turned out to be §4 (tool-list size), which `think: false` never
touched.

Lesson: a plausible-sounding, well-documented upstream issue with matching
symptoms is a hypothesis, not a confirmed cause, until it's reproduced
against your own actual version. It's cheap to check with one bare curl
request before writing a fix around it.
