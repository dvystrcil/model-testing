# 2026-11-01: the-one-ring cultures case after the 11-culture prompt (homelab#1159)

dvystrcil/homelab#1893 changed the paragraph [#1885](../2026-10-31-tor-cultures-after-b/README.md) had tuned. The inline guard list grew from 7 cultures to 11, and the attributes sentence was rewritten. This re-runs the same bench (`--cases tor_overhead,tor_cultures --trials 10`, arms baseline and tool_search) against prod's preset as applied 2026-11-01 01:05Z. Before running, I checked that the bench copy held the new text. The instance was torn down afterwards, and the namespace is gone.

| Run | Prompt | baseline | tool_search | Overhead tokens/turn (base / TS) | Task input tokens/turn, median (base / TS) | Cultures offered |
|---|---|---|---|---|---|---|
| [`-after-b`](../2026-10-31-tor-cultures-after-b/summary.md) | (b), 7-culture guard | 9/10 | 8/10 | 24,621 / 15,667 | — / — (means 46,853 / 31,432) | real only, 20/20 |
| **`-11`** (this) | (b) + 11-culture guard (#1893) | 8/10 | **10/10** | 24,824 / 15,870 | 49,729 / 47,901 (means 44,748 / 97,926) | **all 11, real only, 20/20** |

"Cultures offered" is read from each turn's final message in `output`. The `reply` field holds only its last 400 characters (`run.py`), and counting from it undercounts badly: the first four cultures fall outside the window. Every reply names all 11. No list item is anything else, and no reply gives attribute numbers.

## What it means

- **No regression.** tool_search went 8/10 → 10/10, and baseline went 9/10 → 8/10. Neither change is significant (Fisher p = 0.47 and 1.00). The two baseline misses skipped the tool but still listed the 11 real cultures, from the guard list. That's the job #1885 kept the list for.
- **The guard list now matches the server.** The 7-culture list offered a subset of what `tor_list_cultures` returns. Now a reply built from the list and a reply built from the tool say the same thing.
- **Overhead is up about 200 tokens a turn** (24,621 → 24,824), the cost of the longer step 5 and the four extra names.
- **The tool_search task-token mean (97.9k) is two loops, not prompt size.** Trial 4 made 24 calls (including wiki import/write against the stub) and trial 7 made 8. The bench's `tor_list_cultures` stub answers "ok" with no data, so the model keeps looking (see the harness README). The median is 47.9k, level with baseline.
