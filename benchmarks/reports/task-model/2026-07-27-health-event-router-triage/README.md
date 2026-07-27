# Health Event Router triage pick — keep llama3.2:1b, don't swap to lfm2.5-thinking:1.2b

Date: 2026-07-27
Trigger: a small-LLM sweep (GHA run 30289109165) for a CUDA-lane replacement
for `llama3.2:1b` ranked `lfm2.5-thinking:1.2b` the clear winner on general
K8s-manifest-generation payloads — higher accuracy, higher raw TPS. But none
of those payloads matched `Health Event Router`'s actual role: a bare
one-line data string, no system prompt, asking for exactly one of three
enum tokens, running every 5 minutes as part of `dvystrcil/n8n-workflow`
(homelab#571 / D-027). Built a payload that replicates that exact prompt
shape before trusting the general-purpose recommendation for this
specific, latency-sensitive use.

## Method

`payloads/triage_one_line_classification.json` reproduces
`Build Triage Prompt`'s exact wording:

```
severity=CRITICAL findings=1 primary=ollama-inference OOMKilled -> reply with one of: informational, needs_action, needs_escalation
```

`quality_facts: ["needs_escalation"]` (the only correct answer for a
CRITICAL event — this scenario deliberately mirrors `Parse Triage`'s own
severity-ceiling logic: `severity === 'CRITICAL' && classification ===
'informational'` is forced up to `needs_escalation` in production).
`quality_forbidden` includes the other two enum tokens plus hedge language
(`i think`, `possibly`, `maybe`, ...).

Sweep run: GHA 30292609330, `lfm2.5-thinking:1.2b` vs `llama3.2:1b`,
ollama 0.32.1.

## Results

| Model | Answer | Correct? | Gen time | Gen tokens |
|---|---|:---:|---:|---:|
| `llama3.2:1b` | "I would respond with: informational." | ❌ | **0.06s** | 8 |
| `lfm2.5-thinking:1.2b` | "needs_action \n\nThe error \"OOMKilled\" indicates a resource issue requiring direct intervention. Since severity is CRITICAL and the primary involves technical adjustments, taking action to resolve it aligns best with this response." | ❌ | **4.28s** (70x slower) | 859 |

**Both models answered wrong.** That's not the interesting part — the
interesting part is *which* wrong answer each gave, because production has
an existing safety net (`Parse Triage`'s severity floor/ceiling) that only
covers one of the two failure modes:

```js
if (event.severity === 'OK') classification = 'informational';
else if (event.severity === 'CRITICAL' && classification === 'informational') classification = 'needs_escalation';
```

- `llama3.2:1b` said `informational` → **the ceiling rule catches this
  exactly** and silently corrects it to `needs_escalation` in production.
  Its wrong answer never actually ships wrong.
- `lfm2.5-thinking:1.2b` said `needs_action` → **the ceiling rule does
  NOT cover this case** (it only upgrades `informational`, not
  `needs_action`). This wrong answer would ship uncorrected, and it took
  70x longer to arrive at it.

So `lfm2.5-thinking:1.2b` is worse on *both* axes for this specific role:
slower, and its particular failure mode falls outside the existing
safety net while llama's doesn't.

## Bonus finding (not the primary question, but caught in the same sweep)

`structured_json_triage_summary` (mirrors `Log Analysis Agent`'s
multi-item JSON summarization role) caught `llama3.2:1b` emitting
genuinely invalid JSON — it echoed the prompt's enum placeholder
literally instead of picking a value, then appended trailing prose after
the JSON object:

```
{"triaged":[{"service":"harbor-registry","signal":"","severity":"info|warn|error|critical"}],"overall_severity":null,"executive_summary":"Error messag...
```
→ `invalid_json: Extra data: line 1 column 196 (char 195)`

`lfm2.5-thinking:1.2b` produced clean, valid JSON for the same prompt.
Not this repo's concern directly (that role is served by
`gemma4:26b-a4b-it-qat`, not either of these two), but it's the first real
evidence the new `validate:json_parse` harness feature (added alongside
these payloads) actually catches something.

## Conclusion

**Keep `llama3.2:1b` for `Health Event Router`'s `LLM Triage` node.** The
general-sweep recommendation to standardize on `lfm2.5-thinking:1.2b`
does not transfer to this role — it's a real regression here, not just a
latency trade-off. `lfm2.5-thinking:1.2b` may still be the right pick for
other roles (K8s manifest generation, general coding assistant) where its
higher accuracy under constraint stress is the dominant factor.

## Action taken

- No change to `Health Event Router` — `llama3.2:1b` stays.
- Posted as a status note on `dvystrcil/homelab#571`.

## Caveat

This test isolates the *classification* prompt shape only, via
`run_benchmark.py` talking directly to Ollama. It does not, and cannot,
exercise `dvystrcil/homelab#577` (OWUI's builtin-tools layer offering a
memory-search tool to raw model tags) — that bug only exists on the
OWUI-mediated path (`Health Event Fix Proposal`'s `OWUI Fix Proposal`
node), which this harness bypasses entirely. Any fix for #577 needs
live re-verification, not a benchmark-suite regression test.

## References

- `dvystrcil/homelab#571` — Health Event Router fix-proposal build (this
  report answers the "should we swap the triage model" side-question
  raised during that work)
- `dvystrcil/homelab#577` — the separate OWUI builtin-tools bug this
  report's caveat refers to
- `payloads/triage_one_line_classification.json`,
  `payloads/structured_json_triage_summary.json`,
  `payloads/structured_json_fix_proposal.json` — added same day
  (`dvystrcil/model-testing#59`)
- `n8n-workflow/workflows/health_event_router.json` — `Build Triage
  Prompt` (the prompt this payload replicates) and `Parse Triage` (the
  severity ceiling this report's conclusion depends on)
- GHA runs: 30289109165 (general sweep, motivated this report),
  30292609330 (this report's data)
