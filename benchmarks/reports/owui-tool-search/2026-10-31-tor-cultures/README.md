# 2026-10-31: the-one-ring cultures case, 10 trials (Tool Search off vs on)

A follow-up to [`../2026-10-30-v0.12.5/`](../2026-10-30-v0.12.5/README.md) for dvystrcil/homelab#1871. After the `tor_skill_check` correction, the cultures case was Tool Search's only loss on the-one-ring (0/3 against 3/3). Three trials couldn't say whether that was real, so this reruns just that case with 10 trials per arm. Same bench, rebuilt from scratch, and torn down afterwards.

| Arm | Called `tor_list_cultures` first | Fixed cost/turn (tokens) |
|---|---|---|
| baseline | 7/10 | 24,556 |
| tool_search | 4/10 | 15,602 |
| pooled with the 2026-10-30 run | baseline 10/13, tool_search 4/13 | |

Fisher's exact test: p = 0.37 for this run alone, and p = 0.047 pooled. So Tool Search plausibly lowers the call rate on this case, but the evidence is thin.

## What it means

- **The case is flaky without Tool Search too.** With it off, qwen3-coder-next still skipped the tool 3 times in 10. Every skipped turn still listed the cultures, from the system prompt, which names each Heroic Culture inline ("Bardings, Dwarves of Durin's Folk, Elves of Lindon, …"). The prompt's hero-creation steps also say "Call tor_list_cultures". The two instructions compete, and Tool Search tips the balance toward the inline list.
- **It isn't a findability problem.** `tor_list_cultures` (286 chars) is under the 400-char defer threshold, so it was in the provider tool array in every trial.
- **The fix belongs in the preset, not in OWUI:** drop the inline culture list, or say the tool is the only source. That has its own risk (the list is also the model's guard against inventing cultures), so measure it with this case before shipping.
