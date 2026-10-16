# owui-visual-tools

Benchmarks Open WebUI's in-chat **rendering tools** against local models. The question it answers: *given a visual request, does this model drive this tool well enough that the user sees the right picture?* (model-testing#126, built for dvystrcil/homelab#981.)

| Tool | Upstream | Good at | Can't do |
|---|---|---|---|
| **Inline Visualizer** (`visualize`) | Classic298/open-webui-plugins | anything drawable: diagrams, custom dashboards, Chart.js | nothing structural, but every result depends on the model writing good HTML/SVG |
| **OpenUI – Generative UI** (`render_openui`) | thesysdev/openwebui-plugin | charts, tables, cards, callouts built from data | diagrams (no component for them) |

The answer depends on the model as much as the tool. **Re-run this when a new model arrives.**

## Three tiers

| Tier | What it proves | How |
|---|---|---|
| 1. protocol | the model calls the tool correctly | `bin/owui-visual-tools-eval.py` runs OWUI's native tool loop over the ollama API, with each tool's own docstring, plus the visualizer's own `SKILL.md` and result text |
| 2. render | the tool's own code turns that output into something | the same script screenshots it with chrome-headless-shell: the visualizer's real wrapper inside a same-origin mock chat message, and OpenUI's `_build_openui_html` |
| 3. real UI | OWUI's frontend actually paints it | `e2e/chat.js` drives the real OWUI chat in a browser as a test user |

Tiers 1–2 are cheap and repeatable. Tier 3 needs a test user's session token and leaves a chat behind, so run it when OWUI or a tool changes, not for every new model.

## Run it

```bash
# ollama reachable on localhost (or pass --ollama)
kubectl -n ollama port-forward svc/ollama 11434:80 &

bin/owui-visual-tools-eval.py \
  --models qwen3.6:35b,qwen3.8:27B \
  --report benchmarks/reports/owui-visual-tools/$(date +%F)-<slug>
```

- Needs chrome-headless-shell for tier 2 (`npx playwright install chromium-headless-shell`, or set `CHROME`). Without it, the visualizer is still scored on protocol, but **OpenUI is reported `unscored`, never `pass`**.
- `--tools visualizer` or `--tools openui` runs a single tool. `--cases chart,negative` runs a subset of prompts.
- Output in the report directory: `raw/results.jsonl` (one record per model × tool × case), `shots/*.png`, and `summary.md` (the scoreboard).
- The last line is `OWUI-VISUAL-TOOLS-COMPLETE records=N pass=P fail=F unscored=U`.

To re-apply improved rules to an existing report without re-running the models, use `--rescore --report <dir>`. Changed verdicts keep their old value in `rescored_from`.

Tier 3:

```bash
cd benchmarks/owui-visual-tools/e2e && npm ci
OWUI_TOKEN_FILE=~/.owui-e2e-token TOOL_NAME="OpenUI - Generative UI" \
  node chat.js qwen3.8:27B "Use the render_openui tool to show me a bar chart: Jan 120, Feb 95" out/openui
```

## Reason codes

| Code | Meaning | First seen |
|---|---|---|
| `NO_CALL` | wrote the visual without calling the tool, so in OWUI the user sees raw HTML | qwen3.6:35b, 2026-10-16 |
| `LEAKED_CALL_SYNTAX` | wrote its tool call as text (`<｜DSML｜…>`), so the parser never saw it | nemotron-3.5-lightning |
| `LOOP_NO_CONTENT` | called the tool repeatedly and never wrote content: an empty frame | qwen3-coder-next |
| `SKIPPED_SKILL` | called `visualize` without reading the skill, so the design rules went unread | qwen3-coder-next (its bars were out of scale) |
| `WAITS_FOR_LOAD` | the fragment's script waits for `DOMContentLoaded` or `load`, which has already fired when the wrapper injects it, so the chart stays blank | qwen3.6:35b rerun |
| `BAD_FRAGMENT` | not exactly one clean `@@@VIZ` block: a code fence, a full document, or unbalanced tags | — |
| `MISSING_VALUES` | the prompt's names or numbers aren't in what the user sees | qwen3-coder-next on OpenUI (silent partial render) |
| `CALLED_ON_NEGATIVE` | rendered when nobody asked for a visual | none yet |
| `RENDER_SKIPPED` | OpenUI output that couldn't be rendered, so it is `unscored` | — |

## Mechanical scores are not the verdict

The checks catch protocol failures. They don't judge whether a picture is *right*.

- In the 2026-10-16 run, qwen3-coder-next drew a bar chart with the wrong bar heights that passed every structural check.
- OpenUI "passed" diagrams by producing numbered lists instead.

**Look at `shots/` before drawing conclusions**, and record the human verdict in the report's README, as the 2026-10-16 report does.

## Keeping it comparable

- **`cases.json`:** the prompts are verbatim from 2026-10-16. Add new cases; don't edit existing ones, or old and new runs stop being comparable.
- **`tools.lock.json`:** pins each upstream file by commit and sha256, and pins the OpenUI browser bundle version. A mismatch stops the run. To test a newer upstream, bump the commit and sha256 together and say so in the report.
- **Single runs:** each cell is one run, so treat cells as observations, not rates. Use repeats before acting on one cell. See the 2026-10-16 report for how much the same cells varied between two runs.
