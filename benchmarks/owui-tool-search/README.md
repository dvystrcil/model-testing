# owui-tool-search

Measures two OWUI v0.12 features against our real tool surfaces, so we turn on only what the numbers support (dvystrcil/homelab#1871 AC2):

| Feature | Setting | What it changes |
|---|---|---|
| **Tool Search** (v0.12.0, experimental) | `chat.tool_search.enable` | Tool schemas over `defer_threshold` chars (400) leave the provider `tools` array. They're listed by name in an `<available_tools>` block, and the model reaches them via `search_tools` → `call_tool`. |
| **Lazy skill loading** (v0.12.4) | `skills.lazy_loading.enable` | The skills the UI sends as `skill_ids` (seeded from the preset's `meta.skillIds`) become `<available_skills>` manifest entries, loaded with `view_skill`, instead of being injected in full. |

Both are **global** settings with no per-model override, so the bench runs on a throwaway OWUI, never on prod.

## What it runs on

- **Namespace `owui-bench`:** `ghcr.io/open-webui/open-webui:v0.12.5`, plus prod's `patch-middleware.py` (tool-name repair, homelab#1152). SQLite on an emptyDir. It uses prod's Ollama.
- **Config:** prod's `config` rows, minus credentials, auth and connections (`keep_config_key`). So the builtin tool set, permissions and sub-agent settings match prod.
- **MCP servers:** stubs (`stub_mcp.py`) serving the **real** schemas captured in `tools.lock.json`, with canned replies. A wrong pick can't touch Mealie or the Kroger cart.
- **Presets:** copies of prod's `meal-time` (builtins + mealie 70 + kroger 32 + tinysearch 5 MCP tools, the biggest surface in prod) and `the-one-ring` (builtins + 21 MCP tools, plus a 12k-char skill). Their skills are copied too. Base models are registered so a non-admin can reach them (v0.12.5 treats an unregistered base as admin-only; homelab#1883).
- **Turns:** each goes through the UI's socket path as a **non-admin** user: `homelab/bin/owui-socket-chat.py --skill-ids model`, which needs homelab#1882.

## Run it

```bash
cd benchmarks/owui-tool-search
/usr/bin/python3 run.py up
/usr/bin/python3 run.py setup
/usr/bin/python3 run.py bench --report ../reports/owui-tool-search/$(date +%F)-<slug> --trials 3
/usr/bin/python3 run.py down          # deletes the namespace, so nothing is left behind
```

`bench` resumes: records already in `raw/results.jsonl` are skipped. `--arms` picks from `baseline,tool_search,lazy,both`, and `--cases` runs a subset. `rescore` re-applies the scoring to an existing report. The last line is `OWUI-TOOL-SEARCH-COMPLETE records=N errors=E`.

## Scoring

- **Tool choice:** the first *domain* tool called (meta tools such as `search_tools` and `view_skill` are skipped, and `call_tool` is unwrapped to its target) must be in the case's `expect` list. `expect: []` means no domain tool should be called.
- **Overhead tokens/turn:** `input_tokens` of an `overhead` case ("Reply with exactly OK"). That's one LLM call, so it is the fixed prompt cost every turn pays.
- **Task input tokens/turn:** `usage.input_tokens` summed over every LLM call in a task turn, as OWUI records it. The canned stub replies make models keep looking, so this measures loop length as much as prompt size. Read it alongside the overhead column, not alone.

Tests: `python3 -m pytest tests/test_owui_tool_search.py`.
