# Log Analysis triage replay: gemma4:26b-a4b-it-qat vs qwen3.6:35b

Date: 2026-11-19 (calls made 10:20–10:47 PST).
Payload: [`log_triage_replay`](../../../payloads/log_triage_replay.json), **on demand only**.
Decision: recorded as D-050 in homelab ([dvystrcil/homelab#2419](https://github.com/dvystrcil/homelab/pull/2419)).
That repo is private, so this report quotes nothing from it.

**Result.** On the run-level measure that matters (a real problem the triage
call would have dropped as NOISE), gemma is not better than qwen: 0 vs 0
under the AC labels, 1 vs 0 under the adjudicated labels. qwen produced one
answer that wasn't valid JSON (1 of 62); gemma produced none. Neither model
filters noise: both filed 30–31 of 36 NOISE-labelled calls.

## What was replayed

| | |
|---|---|
| Cases | 31 real Log Analysis Agent triage runs from 2026-10-05 to 2026-11-19, one per run that filed a digest. Each case is the run's error groups as the triage node saw them. |
| Prompt | The node `Build Triage Prompt`, vendored verbatim in the payload's `prompt_source` (dvystrcil/n8n-workflow `workflows/log_analysis_agent.json` at `6879fadcec94622b81f2bd3aa2f7d0ff27d1e0de`). `run_benchmark.py` renders it from that template. |
| Baseline request | The node `Ollama Triage` body with only `model` swapped: OpenAI-compatible `/v1/chat/completions`, `temperature 0.1`, `max_tokens 2048`, `reasoning_effort "none"`, `stream false`, 300 s timeout. 2 reps × 31 cases per model, sequential, all gemma calls then all qwen calls; 124 calls, all HTTP 200 with `finish=stop`. |
| This runner's request | Native `/api/chat` with `think: false`, `options.temperature 0.1`, `options.num_predict 2048`. **This is an approximation of the node's request, not the same call.** A sweep here is comparable with another sweep here; it is not byte-for-byte the baseline below. |
| Scoring | `triage_overall` ports the node `Format Issue`: strip `<think>` and lowercase code fences, `JSON.parse`, read `overall_severity`; anything else is `UNKNOWN`. "Filed" is `Decide Action`'s rule: every run is filed unless overall is `NOISE`, so `UNKNOWN` is filed. |

The baseline answers are in [`raw/`](raw/), one line per call. Reproduce the
numbers below with `python3 -m pytest tests/test_log_triage_replay.py -q`.
The scoring tests recompute them from those files and compare them with this
table.

### Bias in the case set

- A case holds only the groups the live model flagged at the time. Groups it
  triaged as NOISE were never written anywhere recoverable (135 such rows
  across the 31 runs). Every case is therefore a set the live model (gemma,
  since 2026-09-04) already judged worth filing.
- For a group whose resource was identified, the original stream's
  pod/container wasn't recorded, so its prompt line reads `unknown`.
- Some lines, such as CronJob status-write conflicts and a buildx GPU probe,
  are now dropped before triage. They were kept as NOISE probes.

### Labels

Each case has two per-run labels (`NOISE` / `WARNING` / `CRITICAL`, or null
when excluded):

- **`ac`** applies the replay request's rule literally. Closed as not planned
  → NOISE. Completed → real, graded by its resolution (WARNING for all nine).
- **`adjudicated`** re-reads how each run was resolved. Several "not planned"
  closes were duplicates of a fixed problem, or signals that were later fixed.
  They count as real here. Runs whose outcome was never established are
  excluded.

Runs still open on 2026-11-19 are excluded from both sets.

| set | runs | real | NOISE |
|---|---|---|---|
| ac | 27 | 9 | 18 |
| adjudicated | 23 | 14 | 9 |

## Results

Counts are calls: runs × 2 reps. *Invalid JSON* = answers scored `UNKNOWN`.

### AC labels

| model | calls | exact severity | filed/not-filed agrees | NOISE runs filed | real missed | invalid JSON |
|---|---|---|---|---|---|---|
| gemma4:26b-a4b-it-qat | 54 | 19/54 | 23/54 | 31/36 | **0/18** | 0 |
| qwen3.6:35b | 54 | 20/54 | 24/54 | 30/36 | **0/18** | 1 |

### Adjudicated labels

| model | calls | exact severity | filed/not-filed agrees | NOISE runs filed | real missed | invalid JSON |
|---|---|---|---|---|---|---|
| gemma4:26b-a4b-it-qat | 46 | 27/46 | 31/46 | 14/18 | **1/28** | 0 |
| qwen3.6:35b | 46 | 28/46 | 32/46 | 14/18 | **0/28** | 1 |

### Wall time

All 62 calls per model. Each model was already loaded after its first call.
Median is the lower median, p90 is nearest-rank.

| model | median | p90 | max | total |
|---|---|---|---|---|
| gemma4:26b-a4b-it-qat | 10.9 s | 27.0 s | 37.5 s | 866 s |
| qwen3.6:35b | 10.9 s | 23.7 s | 28.3 s | 788 s |

### Invented details (read by hand, not scored here)

All 124 executive summaries were read. Each per-group
`component`/`summary`/`severityRationale` was also read when it contained a
digit token or an event word (crash, loop, stuck, restart, OOM, …) that isn't
in the prompt. A detail counts as invented when it is stated as fact and the
input neither contains it nor supports it.

| model | invented |
|---|---|
| gemma4:26b-a4b-it-qat | 2. A PV "stuck in Terminating" (the input only says deletion failed while attached); "CrashLoopBackOff" (the input has a `panic`, but no pod status) |
| qwen3.6:35b | 5. An invented 403 alongside `toomanyrequests`; "split-brain"; "stuck in a deletion/update loop" from one status-update conflict; "instance manager pod crashes" from "deleted or not running"; "persistent failures" from single occurrences |

### Per case (overall per rep)

| case (window end, UTC) | groups | ac | adjudicated | gemma4 | qwen3.6 |
|---|---|---|---|---|---|
| 2026-10-05T16:49 | 11 | WARNING | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-05T22:00 | 15 | WARNING | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-06T10:00 | 14 | NOISE | WARNING | **NOISE** / WARNING | WARNING / WARNING |
| 2026-10-06T16:00 | 12 | WARNING | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-08T10:00 | 1 | WARNING | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-09T04:00 | 11 | WARNING | WARNING | CRITICAL / CRITICAL | CRITICAL / CRITICAL |
| 2026-10-09T10:00 | 7 | NOISE | NOISE | WARNING / WARNING | WARNING / WARNING |
| 2026-10-09T16:00 | 5 | NOISE | NOISE | WARNING / WARNING | WARNING / WARNING |
| 2026-10-09T22:00 | 2 | NOISE | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-10T10:00 | 6 | NOISE | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-11T04:00 | 2 | NOISE | NOISE | NOISE / NOISE | NOISE / NOISE |
| 2026-10-15T04:00 | 1 | NOISE | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-18T04:00 | 3 | WARNING | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-19T16:00 | 12 | WARNING | WARNING | CRITICAL / CRITICAL | CRITICAL / CRITICAL |
| 2026-10-21T04:00 | 1 | WARNING | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-21T10:00 | 2 | WARNING | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-10-23T04:00 | 1 | NOISE | NOISE | NOISE / NOISE | NOISE / NOISE |
| 2026-10-24T04:00 | 1 | NOISE | NOISE | WARNING / WARNING | WARNING / WARNING |
| 2026-10-24T16:00 | 11 | NOISE | excluded | WARNING / WARNING | WARNING / WARNING |
| 2026-10-25T04:00 | 1 | NOISE | excluded | WARNING / WARNING | NOISE / NOISE |
| 2026-10-26T16:00 | 1 | NOISE | NOISE | WARNING / WARNING | WARNING / WARNING |
| 2026-10-27T16:00 | 4 | excluded | excluded | CRITICAL / CRITICAL | WARNING / CRITICAL |
| 2026-11-03T05:00 | 4 | NOISE | WARNING | WARNING / WARNING | WARNING / WARNING |
| 2026-11-06T23:00 | 13 | NOISE | NOISE | WARNING / WARNING | WARNING / WARNING |
| 2026-11-11T02:00 | 15 | NOISE | NOISE | WARNING / WARNING | **UNKNOWN** / CRITICAL |
| 2026-11-13T02:00 | 13 | NOISE | NOISE | WARNING / WARNING | WARNING / WARNING |
| 2026-11-13T14:00 | 8 | NOISE | excluded | WARNING / WARNING | WARNING / WARNING |
| 2026-11-14T02:00 | 11 | NOISE | excluded | WARNING / WARNING | WARNING / WARNING |
| 2026-11-19T02:00 | 3 | excluded | excluded | WARNING / WARNING | WARNING / WARNING |
| 2026-11-19T08:00 | 11 | excluded | excluded | WARNING / WARNING | WARNING / WARNING |
| 2026-11-19T14:00 | 11 | excluded | excluded | WARNING / WARNING | WARNING / WARNING |

## What the run-level numbers hide

- **gemma's one miss contradicted itself.** In case 2026-10-06T10:00, rep 1,
  it marked a group WARNING and summarised "one warning". It then set
  `overall_severity: NOISE`, so the run would have been dropped, including a
  missing-snapshot-CRD (csi-snapshotter) signal that was later fixed.
- **qwen's invalid answer** closed a JSON object with `]` (case
  2026-11-11T02:00, rep 1). The node would file it as UNKNOWN, "Triage parse
  failed".
- **At group level, qwen called the real problem noise more often.** In the
  four csi-snapshotter cases (2026-10-05 to 2026-10-06), qwen marked 52 of 84
  snapshotter group entries NOISE, and gemma marked 26 of 84. The runs were
  filed either way, so the run-level score doesn't count it.
- **Triage isn't where noise gets removed today.** On these cases both models
  file almost every NOISE run. The case bias above makes this look worse than
  live does.

## Redaction

The cases and answers come from a private cluster, and this repo is public.
Before anything was committed:

- Pod IPs and node IPs became documentation addresses (`192.0.2.x`,
  `198.51.100.x`).
- Volume/PVC/snapshot UUIDs, including ones the 200-character sample cut
  short, became `pvc-0001`-style ids.
- 32- and 64-hex instance-manager and exec ids became `h01`-style ids.
- Node names became `node-a`…`node-e`, and the in-cluster service DNS suffix became
  `svc.cluster.lan`.
- The names of privately built services, CronJobs and claims became
  `app-a`, `cronjob-c`, `ns-f`, `claim-g` and so on.

Each replacement maps one original value to one placeholder everywhere, the
same in the cases and in the answers that echo them. Error text, upstream
component names (longhorn, cert-manager, kube-apiserver, csi-snapshotter, …),
counts and severity cues are unchanged. Every placeholder is no longer than
the value it replaced, so the node's 200-character sample cut falls in the
same place.

The original replay's answers were redacted with the same map. That makes
their wording slightly different from what the models produced, but no
severity changed: the scoring tests reproduce the published tables exactly.
The issue references and the closing-comment text behind the labels were not
carried over. Only the bare labels were.

`tests/test_log_triage_replay.py` fails if the payload or anything under this
directory contains an RFC1918 address, a UUID, the in-cluster DNS suffix, a private
domain, a node-name pattern, a 32/64-hex id or a private issue link. Run
against the unredacted fixtures, it flagged all of them.
