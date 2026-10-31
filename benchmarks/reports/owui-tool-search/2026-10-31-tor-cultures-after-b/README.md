# 2026-10-31: the-one-ring cultures prompt fix, measured before and after (homelab#1885)

The cultures case lost under Tool Search because the-one-ring's system prompt lists every Heroic Culture inline while also telling the model to call `tor_list_cultures` ([`../2026-10-31-tor-cultures/`](../2026-10-31-tor-cultures/README.md)). This run measures two candidate prompt edits against that. It's the same bench, with `--cases tor_overhead,tor_cultures --trials 10` and arms baseline and tool_search. The edits were applied to the bench's copy of the preset through the OWUI model API, one at a time on the same instance, which was torn down afterwards.

| Run | Prompt | baseline | tool_search | Overhead tokens/turn (base / TS) | Task input tokens/turn (base / TS) | Cultures offered |
|---|---|---|---|---|---|---|
| [`-before`](../2026-10-31-tor-cultures-before/summary.md) | prod, unchanged | 9/10 | 4/10 | 24,556 / 15,602 | 46,729 / 21,874 | real only, 20/20 |
| [`-after-a`](../2026-10-31-tor-cultures-after-a/summary.md) | (a) inline list dropped | 9/10 | 8/10 | 24,568 / 15,614 | 49,226 / 76,489 | **invented in 19/20** |
| **`-after-b`** (this) | (b) list kept as a guard, tool required | 9/10 | **8/10** | 24,621 / 15,667 | 46,853 / 31,432 | real only, 20/20 |

"Cultures offered" comes from reading each reply's final message and checking its bolded names against the TOR 2e list (Bardings, Dwarves of Durin's Folk, Elves of Lindon, Hobbits of the Shire, Men of Bree, Rangers of the North, plus High Elves of Rivendell).

## The edits

Both edits change the same two sentences.

**(a)** replaces the inline list with: *"The player picks a Heroic Culture and that culture sets the three attribute values. The Heroic Cultures on offer come from tor_list_cultures — when the player asks which cultures they can pick, call it first and offer what it returns; never list cultures from memory."*

**(b)** keeps the list and appends: *"That list is ONLY a guard against inventing cultures, not the answer to give: when the player asks which cultures they can pick, you MUST call tor_list_cultures first and offer what it returns."*

Both also change the character-creation paragraph, so it no longer contradicts the tool routing: *"If they ask which Heroic Cultures they can pick, call tor_list_cultures first instead. If they want to roll a specific hero (e.g. "an Elf of Rivendell"), call tor_culture_template for that culture's attribute values and Standard of Living, then narrate."*

## What it means

- **(b) ships.** tool_search goes from 4/10 to 8/10, and baseline is unchanged at 9/10. Fisher's exact test gives p = 0.17 for today's 10 against 10. Pooled with the two earlier unchanged-prompt runs (0/3 on 2026-10-30, 4/10 earlier on 2026-10-31), it's 8/23 against 8/10, p = 0.026. Every reply named only real cultures, and none gave attribute numbers.
- **(a) is a regression, even though it raises the call rate just as much.** The bench's `tor_list_cultures` stub returns a canned "ok" with no data. Without the inline list, the model filled the gap from training data: "Orc", "Troll", "Ainu", "Faeries of the Wild", "Men of Dunharrow", "Dwarves of the Iron Hills". The 20th reply said plainly that the tool returned no data. That is the correct behaviour, but it was the only one. Prod's tool returns the real list, but the stub stands in for the failure case (an empty or broken reply, a skipped call), and there the list is the only guard. This confirms the risk the previous report flagged.
- **The prompt doesn't shrink.** (b) adds about 65 tokens to the fixed cost. The issue hoped dropping the list would shrink the prompt, but the list has to stay.
- **Task tokens go up under tool_search (21.9k to 31.4k)** because more turns now make the tool call. The canned reply keeps the model looking, so this measures loop length (see the harness README), not prompt size.
- **The "before" run had a skip that confabulated.** In tool_search trial 7, the model skipped the tool and gave invented attribute values ("Strength 10, Heart 12, Wits 12"; TOR 2e attributes run 2–7). (b) had no such reply.
