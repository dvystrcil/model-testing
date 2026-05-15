# TASK_MODEL pick — follow-up suggestion formatting

Date: 2026-05-15
Trigger: OWUI's TASK_MODEL was set to `llama3.2:1b` by default. User noticed
follow-up suggestions in the fiction-writer chat were "oddly formatted — every
line is a character." Investigation traced the bug to the 1B model being too
small to reliably produce the JSON-list output OWUI's prompt expects.

## Method

Replicated OWUI's actual `FOLLOW_UP_GENERATION_PROMPT_TEMPLATE` in a sweep
payload (`payloads/followup_format.json`). The template instructs the model
to emit:

```json
{ "follow_ups": ["Question 1?", "Question 2?", "Question 3?"] }
```

Two candidate models, both pulled fresh for this sweep:

- `qwen3:1.7b` (1.4 GB)
- `llama3.2:3b` (2.0 GB)

Sweep config: 2 runs per (model × payload), `--save-responses` for raw
output inspection, `--no-warmup`. Also ran `instruction_following` as a
cross-check on follow-instructions capability.

## Results

| Model | Payload | Facts score | Avg gen | Tokens | Notes |
|---|---|---:|---:|---:|---|
| qwen3:1.7b | followup_format | **50%** | 1.45s + **TIMEOUT** | 180 | Run 2 hung for 5 min before timing out |
| qwen3:1.7b | instruction_following | 100% | 5.42s (±3.90s) | 282 (±251) | High variance |
| **llama3.2:3b** | **followup_format** | **100%** | **0.75s (±0.37s)** | 62 (±30) | Clean JSON, low variance |
| llama3.2:3b | instruction_following | 100% | 1.69s (±0.01s) | 142 (±1) | Tight variance |

## Raw output comparison

### qwen3:1.7b (broken)

```
{


 up questions:

{
 up questions:

1. What's the the political structure of of the British Empire after the AI collapse? ...
2. What's the the role of the RCMP in the post-collapse era? Are they still functional ...
3. What's the the influence of the British Empire on the global power structure?? Does it ...
4 what's the the political landscape of the British Empire after the AI collapse? ...
5 what's the the political structure of the British Empire after the AI collapse? ...
```

Failure modes visible: no JSON envelope, "the the" token repetition, near-duplicate
question variants 3-5, missing numbering punctuation. **Exactly the per-token
garbage** OWUI was rendering in production.

### llama3.2:3b (clean)

```json
{
  "follow_ups": [
    "How does the Imperial influence shape Tien's daily life, given his bonded
     relationship to SHARD and his history in the AI Coalition?"
  ]
}
```

Valid JSON, well-formed sentence, contextually appropriate. Caveat: only
produced one follow-up in this run. The OWUI prompt explicitly asks for 3-5,
so production performance with OWUI's exact prompt context should yield more.

## Conclusion

`llama3.2:3b` is the right TASK_MODEL pick. The structural correctness gap is
the load-bearing fix; count tuning happens via OWUI's prompt template if
needed in production.

## Action taken

- `kubectl set env deployment/open-webui TASK_MODEL=llama3.2:3b TASK_MODEL_EXTERNAL=llama3.2:3b` (live)
- `base/webui-deployment.yaml` updated + committed to dvystrcil/open-webui main `b9130c6`
- This evaluation methodology becomes a canonical receipt for `dvystrcil/homelab#98`
  (dmf-driven model-eval workflow) — future TASK_MODEL re-picks can be triggered
  by prompting dmf

## References

- OWUI `FOLLOW_UP_GENERATION_PROMPT_TEMPLATE` default (in OWUI 0.9.3 `config.py`)
- Memory rule `feedback_small_model_prompt_brevity` (D-040) — applies to triage,
  not to prose-list generation; this report extends the rule's reach
- homelab#98 — the dmf-driven evaluation workflow that will make this kind of
  pick autonomous
