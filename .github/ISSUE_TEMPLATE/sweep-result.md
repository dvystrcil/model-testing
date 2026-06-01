---
name: Sweep result
about: Filed automatically by the n8n sweep-runner when a model-sweep workflow completes. Not for manual use.
title: 'sweep: <model> requested by <requester>'
labels: ['sweep', 'automated']
assignees: ''
---

<!--
This template is populated by the n8n `model_sweep_runner` workflow. The
fields below are placeholders; the runner replaces them at issue-create
time. Do NOT file this template manually — use the n8n webhook
(via the sweep-mcp tool in OWUI, or via `curl` to the webhook) instead.

Tracking convention:
  - Title format: `sweep: <model> requested by <requester>`
  - Labels: `sweep`, `automated`, optionally `regression` if analyze
    flagged one
  - Body: filled with sweep metadata + GHA run link
  - Comments: progress updates, raw aggregated table, analyze report,
    (Phase 2) dual-summarize Claude + local LLM analysis
-->

## Sweep metadata

| Field | Value |
|---|---|
| Model | `<model>` |
| Requester | `<requester>` |
| Sweep ID | `<github_run_id>` |
| Triggered at | `<utc_timestamp>` |
| GHA run | https://github.com/dvystrcil/model-testing/actions/runs/`<run_id>` |
| Payload | `<payload_or_all>` |
| Runs per pair | `<n>` |

## Status

Sweep is running. Progress comments will be posted as each payload
completes. Results table + analyze report follow on completion.

## Acceptance / dispositions

After the report lands:
- [ ] Triage: any regressions vs baseline?
- [ ] If regression: file a `regression-<model>` issue with details
- [ ] If new model is a keeper: update `models.yaml` to include it in default sweeps
- [ ] Close this issue when triage is complete (or auto-close after 7 days if no action needed)

## Related

- Parent thesis: dvystrcil/homelab#292 — Reduce Anthropic dependency
- Automation: dvystrcil/model-testing#36 — sweep automation epic
