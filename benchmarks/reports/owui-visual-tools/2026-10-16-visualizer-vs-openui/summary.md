## visualizer

visualizer: 12/16 pass

| model | diagram | chart | negative | dashboard |
|---|---|---|---|---|
| qwen3.6:35b | pass 106s | fail WAITS_FOR_LOAD 67s | pass 14s | fail NO_CALL 145s |
| nemotron-3.5-lightning:30b-a3b-q4_K_M | pass 69s | pass 49s | pass 2s | pass 24s |
| qwen3.8:27B | pass 243s | pass 72s | pass 9s | pass 128s |
| qwen3-coder-next:latest | pass 72s | fail SKIPPED_SKILL 18s | pass 2s | fail SKIPPED_SKILL 22s |

## openui

openui: 12/16 pass

| model | diagram | chart | negative | dashboard |
|---|---|---|---|---|
| qwen3.6:35b | pass 59s | fail MISSING_VALUES 25s | pass 8s | pass 52s |
| nemotron-3.5-lightning:30b-a3b-q4_K_M | pass 18s | pass 5s | fail CALLED_ON_NEGATIVE 18s | pass 17s |
| qwen3.8:27B | pass 110s | pass 14s | pass 11s | pass 65s |
| qwen3-coder-next:latest | pass 7s | pass 5s | fail CALLED_ON_NEGATIVE 8s | fail MISSING_VALUES 12s |
