# feat: model-sweep completion notifications

## Acceptance Criteria

- [ ] AC1: The sweep Action POSTs a completion summary to the n8n webhook on finish.
- [ ] AC2: The summary payload is valid JSON (no `={...}` lexer corruption).
- [ ] AC3: The OWUI API key is sourced from Infisical, not a raw GitHub repo secret.
- [ ] AC4: On webhook failure the Action warns but does not fail the run (non-fatal).
- [ ] AC5: A completion marker line (`# sweep summary`) is asserted present in the run log so a crashed harness can't pass green.

---

## Evidence

### Evidence A — workflow run 27144695096 log tail
```
[INFO 14:31:02] sweep complete: 9 models x 15 payloads
[INFO 14:31:02] POST https://n8n.n8n-workflow.svc.cluster.local:80/webhook/model-sweep-complete -> 200
```

### Evidence B — n8n execution 5561 body
```json
{"models": 9, "payloads": 15, "run_id": "27144695096", "status": "ok"}
```
(parsed successfully; no NodeOperationError)

### Evidence C — workflow step "notify n8n" (excerpt)
```yaml
- name: notify n8n
  env:
    OWUI_API_KEY: ${{ steps.infisical.outputs.OWUI_API_KEY }}
  run: ./bin/notify-n8n.sh
```

### Evidence D — workflow step "post summary" continue-on-error
```yaml
- name: post summary
  continue-on-error: true
  run: ./bin/notify-n8n.sh || echo "::warning::notify failed, continuing"
```
