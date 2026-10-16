# 2026-10-16 — Inline Visualizer 2.2.3 vs OpenUI 0.5.0

**Question.** For homelab#981: the `inline_visualizer_v2` tool had been deleted while five presets still bound it. Should we restore it, remove it, or let OpenUI cover the need? And which of our four most-used models can drive each tool?

**Answer.** Neither tool replaces the other.
- **OpenUI** is the default for charts, tables and status views.
- **The visualizer** is kept only for diagrams and polished one-offs, on **qwen3.8:27B**, the only model that was clean with it in both runs (8/8).

## What was run

| Run | Harness | Records | Notes |
|---|---|---|---|
| original | scratch scripts (identical prompts and tool sources) | `original-run/results.jsonl`, `original-run/shots/` | Human-reviewed. Published as the comparison page in `meta.yaml`. |
| rerun | `bin/owui-visual-tools-eval.py` at this commit | `raw/results.jsonl`, `shots/`, `summary.md` | Proves the committed harness reproduces the result (model-testing#126 AC8). Rescored once, after `WAITS_FOR_LOAD` was added. |
| real UI | `e2e/chat.js` | `e2e/openui-qwen3.8-real-owui.*` | OpenUI paints in the real OWUI 0.12.2 chat: one iframe, chart and follow-ups inside. |

The models were qwen3.6:35b (64 chats in the last 60 days), nemotron-3.5-lightning (41), qwen3.8:27B (27) and qwen3-coder-next (22). Usage counts include the presets that run on each model.

## Results: both runs, human-reviewed

Each cell reads *original / rerun*. ✅ clean · ⚠️ rendered, but flawed · ❌ the user sees no correct picture.

### Inline Visualizer

| Model | Diagram | Chart | Dashboard | Non-visual |
|---|---|---|---|---|
| qwen3.6:35b | ❌ `NO_CALL` / ✅ | ✅ / ❌ `WAITS_FOR_LOAD` (blank chart) | ❌ `NO_CALL` / ❌ `NO_CALL` | ✅ / ✅ |
| nemotron-3.5-lightning | ✅ minimal / ⚠️ pods chained, wrong topology | ❌ `LEAKED_CALL_SYNTAX` / ✅ | ⚠️ no colour / ✅ | ✅ / ✅ |
| qwen3.8:27B | ✅ / ✅ | ✅ / ✅ | ✅ best of run / ✅ | ✅ / ✅ |
| qwen3-coder-next | ⚠️ overlapping labels / ⚠️ | ❌ `SKIPPED_SKILL`, bars out of scale / ⚠️ `SKIPPED_SKILL`, values right | ❌ `LOOP_NO_CONTENT` / ⚠️ `SKIPPED_SKILL` | ✅ / ✅ |

### OpenUI 0.5.0

| Model | Diagram | Chart | Dashboard | Non-visual |
|---|---|---|---|---|
| qwen3.6:35b | ⚠️ step list / ⚠️ step list | ✅ / ❌ blank (`MISSING_VALUES`) | ✅ / ⚠️ DOWN amber | ✅ / ✅ |
| nemotron-3.5-lightning | ❌ wrong ASCII art / ⚠️ raw Mermaid in a code block | ✅ / ✅ | ⚠️ DOWN amber / ⚠️ DOWN amber | ✅ / ❌ `CALLED_ON_NEGATIVE` |
| qwen3.8:27B | ⚠️ callouts + arrows / ⚠️ steps + table | ✅ / ✅ | ⚠️ DOWN amber, invented details / ✅ table | ✅ / ✅ |
| qwen3-coder-next | ⚠️ step list / ⚠️ callout text | ✅ / ✅ | ❌ blank (`MISSING_VALUES`) / ❌ blank | ✅ / ❌ `CALLED_ON_NEGATIVE` |

## What held across both runs

These are the findings to act on. Single cells moved, but these did not.

1. **qwen3.8:27B + visualizer: 8/8.** It is the only model–tool pairing with no failure in either run, and it produced the best output of the whole test. It is also slow: 72–619 s per visual.
2. **qwen3.6:35b does not reliably call `visualize()`.** Across the two runs it failed 4 of its 6 visual prompts, three of them with `NO_CALL`. In OWUI, a `NO_CALL` shows the user raw HTML.
3. **qwen3-coder-next skips the visualizer's skill** (4 of 6). Once that cost it the correct data, once a loop with no content at all.
4. **OpenUI cannot draw diagrams (0/8).** It substitutes step lists, callouts, or code: once wrong ASCII art, once raw Mermaid.
5. **OpenUI fails silently.** When a model writes invalid OpenUI Lang, it renders a blank frame or just a title, with no error (3 of 24 positive cases).
6. **OpenUI 0.5.0 draws `error` callouts in amber.** Every callout dashboard showed DOWN looking the same as degraded.
7. **OpenUI sometimes renders when nobody asked** (2 of 8 non-visual prompts, both in the rerun). Its docstring says to use it "whenever a visual response would be helpful". The visualizer's says "only on explicit request", and it never over-triggered (0/8).
8. **Speed: OpenUI ≈ 5–110 s, visualizer ≈ 17–619 s.**

## Single-run variance

7 of the 12 positive visualizer cells changed verdict between runs, and 4 of the 12 OpenUI cells did; 2 of the 4 OpenUI non-visual cells changed too. **One run per cell is not enough to judge a single model–tool pairing.** Repeat before acting on a single cell. The findings above are the ones that held.

## Mechanical score vs human review

The rerun's mechanical score was 24/32, after rescoring with `WAITS_FOR_LOAD`. The human review is harsher:
- OpenUI's "pass" on diagrams is a step list, not a diagram.
- Some passing visualizer diagrams have wrong topology.

Two failure modes were found **only by looking at screenshots**. Both are now checks in the harness:
- `MISSING_VALUES`, case-insensitive. It replaced a false failure on "Harbor".
- `WAITS_FOR_LOAD`, a blank Chart.js.

Out-of-scale bars and wrong topology still need a human.

## Real-UI tier (e2e)

OpenUI 0.4.0 (the installed version) rendered in the real OWUI 0.12.2 chat as the test user, using qwen3.8:27B. There was one iframe, containing the chart, its axes and two follow-up suggestions (`e2e/`).

Two things it showed:
- **Without being told which tool to use, qwen3.8 chose OWUI's built-in code interpreter over `render_openui`.** Built-in tools compete with rendering tools.
- **The `?tools=` URL parameter silently drops the tool**, because it is filtered against a tool list that hasn't loaded yet. Select the tool through the UI instead.

## Action taken

homelab#981:
- Restore Inline Visualizer 2.2.3 on the `chat-visualizer` preset, running qwen3.8:27B.
- Remove the visualizer binding from the other four presets.
- Update OpenUI to 0.5.0 with its bundle pinned (`@openuidev/browser-bundle@0.2.1`, not `@latest`).

## Re-running with a new model

```bash
bin/owui-visual-tools-eval.py --models <new-model>,qwen3.8:27B \
  --report benchmarks/reports/owui-visual-tools/<date>-<model>
```

Include qwen3.8:27B as the reference, since it's the current visualizer pick. Review `shots/`, then record the human verdicts in the same table shape as above.
