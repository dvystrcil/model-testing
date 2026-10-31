# 2026-10-30: Tool Search and lazy skill loading on OWUI v0.12.5

For dvystrcil/homelab#1871 AC2.

> **Correction, 2026-10-31.** The first version scored `tor_skill_check` against the wrong expectation. The-one-ring's dice skill says *"The player rolls their own physical dice for their hero's actions … Never roll the player's dice for them."* So for the hero's own Athletics test, asking the player to roll is right, and calling `roll_tor_check` is a rule break. Re-scored with `none_ok` (no re-run), Tool Search on the-one-ring went from 6/12 to 9/12; its only loss is the cultures case. A 10-trial follow-up on that case is in [`../2026-10-31-tor-cultures/`](../2026-10-31-tor-cultures/README.md). The decision below is the original one; see D-032 in homelab `architecture/ways-of-working.md` for the current one.

**Original decision: neither feature is turned on.** Both stay `false`, which is already prod's state. Tool Search helps the biggest tool surface a lot but breaks a small one, and it can't be scoped per preset. Lazy loading saves little and costs one case.

How it ran: see [`../../../owui-tool-search/README.md`](../../../owui-tool-search/README.md). A throwaway OWUI v0.12.5 with prod's config and middleware patch, stub MCP servers serving the real schemas, the UI socket path, and a non-admin user. 3 trials per case. 183 turns, 3 timed out (counted as errors, excluded from accuracy).

## Scoreboard

| Arm | Preset | Tool choice | Fixed cost/turn (tokens) | Total input per task turn |
|---|---|---|---|---|
| baseline | meal-time | 19/22 | 33,867 | 680k |
| tool_search | meal-time | **23/24** | **12,495** (−63%) | 128k |
| lazy | meal-time | 19/23 | 32,224 (−5%) | 423k |
| both | meal-time | **23/24** | **10,852** (−68%) | 117k |
| baseline | the-one-ring | 12/12 | 24,556 | 70k |
| tool_search | the-one-ring | **9/12** | 15,602 (−36%) | 41k |
| lazy | the-one-ring | 11/12 | 20,518 (−16%) | 71k |
| both | the-one-ring | 8/12 | 11,564 (−53%) | 43k |
| tool_search_pinned | the-one-ring | 9/12 | 18,640 (−24%) | 67k |

- **Fixed cost** is the "Reply with exactly OK" turn: one LLM call, so it's the prompt every turn pays. It was identical in all 3 trials of every cell.
- **Total input** is summed over every call in a task turn. The canned stub replies make models keep looking, so this mostly measures loop length.
- **Presets:** `meal-time` has builtins plus 107 MCP tools (mealie 70, kroger 32, tinysearch 5). `the-one-ring` has builtins plus 21 MCP tools and a 12k-char skill.
- **`tool_search_pinned`:** Tool Search on, with `chat.tool_search.always_loaded = ["rpg-dice-mcp_*", "tor-character-mcp_*"]`.

## Per case (1 = right first domain tool, 0 = wrong or none, E = timeout)

`tor_skill_check` is scored `none_ok` (see the correction above): asking the player to roll, or `tor_adjudicate` first, is right; `roll_tor_check` is a rule break.

| Case | baseline | tool_search | lazy | both | pinned |
|---|---|---|---|---|---|
| mt_mealie_search | 000 | 111 | 011 | 011 | |
| mt_todays_plan | 111 | 111 | 111 | 111 | |
| mt_import_url | 111 | 111 | E11 | 111 | |
| mt_shopping_list | 111 | 101 | 111 | 111 | |
| mt_kroger_price | 11E | 111 | **000** | 111 | |
| mt_kroger_cart | 111 | 111 | 111 | 111 | |
| mt_web_recipe | 11E | 111 | 111 | 111 | |
| mt_no_tool_units | 111 | 111 | 111 | 111 | |
| tor_roll | 111 | 111 | 111 | 111 | 111 |
| tor_skill_check | 111 | 111 | **101** | **100** | 111 |
| tor_wiki | 111 | 111 | 111 | 111 | 111 |
| tor_cultures | 111 | **000** | 111 | 010 | **000** |

## What it means

- **Tool Search fixes meal-time's worst failure.** In baseline, nemotron called made-up Mealie tools (`mealie-mcp_search_recipes`, `mealie-mcp_search`) on every Mealie search, because 70 Mealie schemas are in the prompt. With Tool Search it went 3/3, and the fixed prompt cost fell from 33.9k to 12.5k tokens.
- **Tool Search's only the-one-ring loss is cultures (0/3).** The model listed the cultures without calling `tor_list_cultures`. It wasn't failing to find the tool: at 286 chars it is under the 400-char threshold, so Tool Search never deferred it, and pinning made no difference. The preset's system prompt already lists every Heroic Culture inline, and the model answered from that. Its hero-creation steps still say to call the tool, so this is a real miss, but the content was right.
- **Lazy loading breaks a skill rule.** Without the dice skill injected up front, qwen3-coder-next rolled the player's own dice in 1 of 3 (lazy) and 2 of 3 (both) skill checks, which the skill forbids.
- **Both settings are global.** OWUI has no per-model override, so a trade-off between presets can't be scoped away.
- **Lazy loading saves 1.6k tokens per turn on meal-time and 4k on the-one-ring.** But without the `kroger-mcp` skill injected, nemotron opened Kroger product images and details before searching, so the Kroger price case went 0/3. That doesn't clear "turn on only what the numbers support".
- **3 trials per case** is enough to see the 0/3-versus-3/3 flips above. It isn't enough to rank arms that differ by one turn.

## Follow-ups this points at

- meal-time's real problem is 70 Mealie schemas in every prompt. Per-connection `function_name_filter_list` (a field on each MCP connection) can trim them for every preset that uses mealie, without a global switch. Re-run this bench with a filtered mealie connection to measure it.
- Re-run when OWUI adds per-model Tool Search, or when a new base model lands on either preset.
