# Surgical whole-file edits: coder candidates × the three dmf CVE payloads

Date: 2026-10-30
Trigger: [model-testing#139](https://github.com/dvystrcil/model-testing/issues/139) AC4. This is the controlled sweep that homelab D-029 (`architecture/ways-of-working.md`) said it lacked. D-029 moved the dmf code-draft lane to `coder-tool-free` (qwen3-coder-next) on the strength of an n=1 A/B over three prompts.

## Bottom line

**D-029 holds.** qwen3-coder-next is clean on 8/9 runs. Its one failure was a format slip: a preamble plus a second code block, which the one-block parser refuses. It never edited a line it was not asked to edit.

qwen3.6:35b, once it is allowed to finish, is clean on 6/9 runs. **All three failures are the dangerous class**, and all are on the same payload. Each open-terminal run rewrote `/etc/apt/sources.list.d/` to `/etc/sources.list.d/`, and two of the runs also touched a `pip install` line (one doubled the space before a `\`, one appended a `\` to a line that ended without one). That is the 2026-10-29 failure, which now reproduces 3/3 instead of 1/1.

| model | homelab pip removal | kroger pip removal | open-terminal `sigstore>=4.1.1` | total | failure class |
|---|---|---|---|---|---|
| **qwen3-coder-next** (`coder-tool-free`) | 3/3 | 2/3 | 3/3 | **8/9** | format slip (refused, retryable) |
| qwen3.6:35b (`automation-tool-free`), uncapped | 3/3 | 3/3 | **0/3** | 6/9 | edits outside the request (only the guard stops it) |
| qwen3.8:27B | 3/3 | 3/3 | 3/3 | 9/9 | none |

A run is clean when `schema_violations == []` under the `surgical_edit` scorer from #140.

**qwen3.8:27B was clean 9/9**, never hit the token cap, and took 36–110 s, against 20–61 s for coder-next. D-029 did not consider it. That is a candidate for a follow-up, not something this report changes. This is n=3 per cell, and coder-next is 1.8–2.4x faster on every payload (26.5 vs 62.4 s, 19.9 vs 36.7 s, 61.5 vs 110.2 s).

## Method

Two runs of `model-sweep.yaml`, both on Ollama 0.40.13, using the `payload=surgical_edit` subset with `runs=3`.

| run | ref | models | `num_predict` | purpose |
|---|---|---|---|---|
| [40985588529](https://github.com/dvystrcil/model-testing/actions/runs/40985588529) | `main` @ `fbedecf` | all 8 in `models.yaml` | 4096 (harness default) | the AC4 sweep |
| [41076227331](https://github.com/dvystrcil/model-testing/actions/runs/41076227331) | `measure/139-qwen36-uncapped` @ `3b00039` | qwen3.6:35b | 12288 (per-payload `options`) | re-measure the model that the cap truncated |

- **Warm loads.** `run_benchmark.py` warms each model before its first timed run (`warmup done cold_load=…` in `raw/*/per_run_log_lines.txt`). Every timed run below is warm.
- **Why the second run.** In the first sweep, 8 of qwen3.6:35b's 9 runs stopped at `done_reason=length`. A thinking model spends most of 4096 tokens before it writes the file, so those runs scored `code_blocks=0` or `changes beyond…`, which in this case means the file was cut off. That measures the cap, not the model. The production dmf call goes through OWUI with no 4096 cap; on 10-29 qwen3.6 took 88–123 s there, which at ~53 tok/s is more than 4096 tokens. With the cap at 12288, every qwen3.6 run ended at `stop`, using 4031–5649 tokens on average.
- **The branch is measurement-only and is not merged.** On the slowest models (~9–12 tok/s), 12288 tokens would outlast `HTTP_TIMEOUT=600`. Raising the cap for all-model sweeps needs the timeout to scale with it first.
- **Per-run outcomes come from the job logs.** The JSONL row stores the *union* of violations and `done_reasons` across a cell's 3 runs, so it can't say how many runs were clean. The `run N/3 done … SCHEMA=[…]` log lines can. They are extracted to `raw/*/per_run_log_lines.txt` and parsed into `raw/run-40985588529/per_run.json`.
- **Per-run truncation** is not logged either. It is estimated as `gen_seconds × model's peak tok/s ≥ 3950`. The estimate agrees with `done_reasons` in every cell that is all-`stop` or all-`length`. It cannot split the two gemma4:31b open-terminal cells, whose mean is 4063±57 tokens: at least one run in each was truncated, and the split isn't recorded.

### Caveats

- The sweep calls Ollama directly, not through the OWUI preset, so it has no preset system prompt and no memory-loader injection. The 10-29 captures in homelab `prompts/dmf-tasks/cve/` did go through OWUI. On both paths, qwen3.6 makes the same sources.list.d rewrite and coder-next makes the same kroger preamble slip, which suggests the path difference doesn't change the failure classes.
- n=3 per cell, three payloads, one family of task (one-line Dockerfile edits in files of 60–200 lines).

## Results

### qwen3-coder-next vs qwen3.6:35b

| payload | model | clean | violations by run | total_duration mean | eval_duration mean ± SD | eval_count mean ± SD | done_reasons |
|---|---|---|---|---|---|---|---|
| homelab | qwen3-coder-next | 3/3 | — | 26.5 s | 26.0 ± 0.0 s | 1192 ± 0 | stop |
| homelab | qwen3.6:35b uncapped | 3/3 | — | 89.5 s | 89.1 ± 5.0 s | 4644 ± 241 | stop |
| kroger | qwen3-coder-next | 2/3 | run 2: `code_blocks=2`, `text_outside_block` | 19.9 s | 19.5 ± 1.6 s | 897 ± 77 | stop |
| kroger | qwen3.6:35b uncapped | 3/3 | — | 76.2 s | 75.8 ± 14.1 s | 4031 ± 723 | stop |
| open-terminal | qwen3-coder-next | 3/3 | — | 61.5 s | 60.4 ± 0.2 s | 2596 ± 1 | stop |
| open-terminal | qwen3.6:35b uncapped | **0/3** | all 3: `changes beyond…` (`/etc/apt/sources.list.d/` → `/etc/sources.list.d/`; run 1 also doubled the space before a `\`, run 2 appended a `\` to `csvkit`) | 112.3 s | 111.6 ± 16.6 s | 5649 ± 793 | stop |

The sweep only records `total_duration` as a mean. The SD column is `eval_duration_sd`, and prompt eval is under 2 s of each total.

On the token cost: qwen3.6 spends 4–5.6k tokens on an edit whose answer is 0.9–2.6k tokens, which makes it 3.4x / 3.8x / 1.8x slower than coder-next per payload. D-029's note that the 10-29 latencies were not evidence of speed still applies to that A/B. These numbers are warm-loaded and n=3.

### Full field at the 4096 default cap (run 40985588529)

| model | clean | truncated (est.) | failed, not truncated | homelab | kroger | open-terminal | gen tok/s |
|---|---|---|---|---|---|---|---|
| qwen3.8:27B | **9/9** | 0 | 0 | 3/3 | 3/3 | 3/3 | 32–34 |
| qwen3-coder-next | **8/9** | 0 | 1 (format) | 3/3 | 2/3 | 3/3 | 43–46 |
| gemma4:31b | 7/9 | ≥2 | 0 | 3/3 | 3/3 | 1/3 | 8.4–9.0 |
| gemma4:31b-it-qat | 7/9 | ≥2 | 0 | 3/3 | 3/3 | 1/3 | 8.7–9.5 |
| qwen3.5:27B | 3/9 | 6 | 0 | 2/3 | 0/3 | 1/3 | 11.4–11.8 |
| qwen3.6:27B | 2/9 | 7 | 0 | 1/3 | 1/3 | 0/3 | 11.4–11.8 |
| gemma4:26b-a4b-it-qat | 1/9 | 8 | 0 | 0/3 | 1/3 | 0/3 | 41–43 |
| qwen3.6:35b | 1/9 | 8 | 0 | 0/3 | 1/3 | 0/3 | 50–53 |

Every failure below coder-next in this table was a truncated run. Apart from qwen3.6:35b, this sweep can't say how those models do on the task, only that 4096 tokens isn't enough for them. Per-cell detail, with violations, durations, eval counts and done_reasons for each run, is in `raw/run-40985588529/per_run.json`.

## Verdict on D-029

D-029 asked whether `coder-tool-free` (qwen3-coder-next) beats `automation-tool-free` (qwen3.6:35b) for whole-file edits. **Yes: 8/9 clean vs 6/9 clean**, and the failure classes split the same way D-029 argued from n=1. coder-next's one miss is refused deterministically and costs a retry. All three of qwen3.6's misses are wrong edits to lines it was told not to touch, and on open-terminal it makes that edit every time. `DMF_MODEL = "coder-tool-free"` in homelab `bin/cve-constraint-pr.py` stays.

## Follow-ups

- The JSONL loses per-run outcomes (it stores a union of violations and a set of done_reasons). This report had to rebuild them from job logs.
- `HTTP_TIMEOUT` is fixed while `num_predict` can be raised per payload. Lifting the cap on the surgical payloads for every model needs the timeout to scale with it.
- qwen3.8:27B (9/9) is worth a head-to-head with coder-next on this lane.
