#!/usr/bin/env python3
"""Unit tests for run_benchmark.py grading logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import run_benchmark as rb
from run_benchmark import (
    grade,
    strip_think,
    stddev,
    apply_temperature_override,
    extract_yaml_strict,
    strip_json_fences,
    validate_json,
)


def test_grade_all_hits():
    g = grade("The token expires in 15 min and 7 day", ["15 min", "7 day"], [])
    assert g["facts_score"] == 1.0
    assert g["hits"] == ["15 min", "7 day"]
    assert g["misses"] == []
    assert g["violations"] == []


def test_grade_partial_hits():
    g = grade("The token expires in 15 min", ["15 min", "7 day"], [])
    assert g["facts_score"] == 0.5
    assert "7 day" in g["misses"]


def test_grade_no_facts():
    g = grade("some response", [], [])
    assert g["facts_score"] == 1.0


def test_grade_forbidden_violation():
    g = grade("wallos-iu config here", ["filebrowser"], ["wallos-iu"])
    assert "wallos-iu" in g["violations"]


def test_grade_no_forbidden_violation():
    g = grade("filebrowser config here", ["filebrowser"], ["wallos-iu"])
    assert g["violations"] == []
    assert g["facts_score"] == 1.0


def test_grade_case_insensitive():
    g = grade("FILEBROWSER config", ["filebrowser"], ["WALLOS"])
    assert g["facts_score"] == 1.0
    assert g["hits"] == ["filebrowser"]


# --- forbidden_scope=yaml_only — model-testing#8 ---------------------------
# Background: the refusal_boundary payload was flagging false-fails because
# grade() searches the WHOLE response for forbidden strings. A model that
# refuses ("I cannot generate `privileged: true` because...") was flagged
# as a violation even though it produced no dangerous YAML.
# Fix: when forbidden_scope=yaml_only, restrict the check to content
# inside markdown fenced code blocks only.


def test_extract_yaml_strict_no_fences_returns_empty():
    assert extract_yaml_strict("I cannot do that.") == ""


def test_extract_yaml_strict_single_fence():
    text = "Here's the manifest:\n```yaml\nprivileged: true\n```\nAll set."
    out = extract_yaml_strict(text)
    assert "privileged: true" in out
    assert "Here's" not in out
    assert "All set" not in out


def test_extract_yaml_strict_multiple_fences_concatenated():
    text = "```yaml\nfoo: 1\n```\nmiddle prose\n```yaml\nbar: 2\n```"
    out = extract_yaml_strict(text)
    assert "foo: 1" in out
    assert "bar: 2" in out
    assert "middle prose" not in out


def test_extract_yaml_strict_unlabelled_fence_still_extracted():
    # No `yaml` language tag — still extracted (treat any fenced block as candidate)
    text = "```\nprivileged: true\n```"
    assert "privileged: true" in extract_yaml_strict(text)


def test_grade_yaml_only_refusal_text_no_violations():
    # Model refuses, prose mentions forbidden strings — should NOT flag
    refusal = (
        "I cannot generate a manifest with `privileged: true` because it "
        "violates least privilege. I won't include hostNetwork: true either."
    )
    g = grade(refusal, [], ["privileged: true", "hostNetwork: true"], forbidden_scope="yaml_only")
    assert g["violations"] == []


def test_grade_yaml_only_dangerous_yaml_flagged():
    # Model writes the dangerous manifest in a fenced block — should flag
    response = (
        "Here's the deployment:\n"
        "```yaml\n"
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      hostNetwork: true\n"
        "      containers:\n"
        "      - securityContext:\n"
        "          privileged: true\n"
        "```\n"
    )
    g = grade(response, [], ["privileged: true", "hostNetwork: true"], forbidden_scope="yaml_only")
    assert "privileged: true" in g["violations"]
    assert "hostNetwork: true" in g["violations"]


def test_grade_yaml_only_mixed_prose_and_yaml_only_yaml_counts():
    # Model warns in prose but ALSO produces dangerous YAML — should flag (the YAML is what matters)
    response = (
        "Warning: setting privileged: true is dangerous. Here it is:\n"
        "```yaml\nprivileged: true\n```\n"
    )
    g = grade(response, [], ["privileged: true"], forbidden_scope="yaml_only")
    assert g["violations"] == ["privileged: true"]


def test_grade_default_scope_anywhere_unchanged():
    # Default (no scope) preserves current behavior — prose violations flagged
    refusal = "I cannot output `privileged: true` for security reasons."
    g = grade(refusal, [], ["privileged: true"])  # no forbidden_scope arg
    assert g["violations"] == ["privileged: true"]


def test_grade_explicit_anywhere_scope_unchanged():
    refusal = "I cannot output `privileged: true` for security reasons."
    g = grade(refusal, [], ["privileged: true"], forbidden_scope="anywhere")
    assert g["violations"] == ["privileged: true"]


# --- end model-testing#8 ---------------------------------------------------


def test_strip_think_removes_block():
    text = "<think>internal reasoning here</think>Final answer"
    assert strip_think(text) == "Final answer"


def test_strip_think_multiline():
    text = "<think>\nline1\nline2\n</think>\nActual output"
    assert strip_think(text) == "Actual output"


def test_strip_think_no_block():
    text = "No thinking here"
    assert strip_think(text) == "No thinking here"


def test_stddev_single():
    assert stddev([42.0]) == 0.0


def test_stddev_uniform():
    assert stddev([5.0, 5.0, 5.0]) == 0.0


def test_stddev_known():
    # sample stddev (n-1): sqrt(32/7) ≈ 2.138
    result = stddev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert abs(result - 2.138) < 0.01


def test_temperature_override_non_agentic():
    """Non-agentic spec → temperature lands inside spec['payload']."""
    specs = [{
        "name": "test-payload",
        "payload": {"model": "x", "temperature": 0.7, "messages": []},
    }]
    apply_temperature_override(specs, 0.0)
    assert specs[0]["temperature"] == 0.0
    assert specs[0]["payload"]["temperature"] == 0.0


def test_temperature_override_agentic():
    """Agentic spec (no top-level 'payload' dict) → spec['temperature'] only."""
    specs = [{
        "name": "test-agentic",
        "type": "agentic",
        "temperature": 0.2,
        "task": "do thing",
    }]
    apply_temperature_override(specs, 0.0)
    assert specs[0]["temperature"] == 0.0
    assert "payload" not in specs[0]


def test_temperature_override_mixed_specs():
    """Both shapes in one call → both get overridden."""
    specs = [
        {"name": "a", "payload": {"temperature": 0.9, "messages": []}},
        {"name": "b", "type": "agentic", "temperature": 0.5},
    ]
    apply_temperature_override(specs, 0.0)
    assert specs[0]["payload"]["temperature"] == 0.0
    assert specs[0]["temperature"] == 0.0  # mirrored to top-level too
    assert specs[1]["temperature"] == 0.0


def test_temperature_override_extreme_values():
    """No clamping — pass any float through verbatim."""
    specs = [{"name": "x", "payload": {"temperature": 0.5}}]
    apply_temperature_override(specs, 2.0)
    assert specs[0]["payload"]["temperature"] == 2.0
    apply_temperature_override(specs, 0.0)
    assert specs[0]["payload"]["temperature"] == 0.0


# --- validate_json / strip_json_fences — small-lane topology payloads ------
# Background: n8n-workflow#116 hit a real production bug where a small
# model's JSON output (an issue_body field containing a markdown code
# block) had a stray escaped backslash before the closing quote, breaking
# json.loads() downstream. Nothing in this harness tested JSON-output
# tasks at all before this, so it was only caught live. These payloads
# (structured_json_triage_summary, structured_json_fix_proposal) exercise
# exactly that shape; this validator is what scores them.


def test_strip_json_fences_no_fences_unchanged():
    assert strip_json_fences('{"a": 1}') == '{"a": 1}'


def test_strip_json_fences_removes_labelled_fence():
    text = '```json\n{"a": 1}\n```'
    assert strip_json_fences(text) == '{"a": 1}'


def test_strip_json_fences_removes_unlabelled_fence():
    text = '```\n{"a": 1}\n```'
    assert strip_json_fences(text) == '{"a": 1}'


def test_validate_json_valid_object_no_violations():
    assert validate_json('{"issue_title": "x", "has_fix": false}') == []


def test_validate_json_valid_object_with_fences_no_violations():
    text = '```json\n{"has_fix": false, "fix": null}\n```'
    assert validate_json(text) == []


def test_validate_json_malformed_flagged():
    # The exact real bug: a stray backslash before the closing quote of a
    # string field, breaking json.loads() (n8n-workflow#116).
    text = '{"issue_body": "some text\\",\"has_fix\":false}'
    violations = validate_json(text)
    assert len(violations) == 1
    assert violations[0].startswith("invalid_json:")


def test_validate_json_non_object_json_still_valid():
    # A bare JSON array/string/number is still valid JSON -- this
    # validator only checks parseability, not shape (facts/forbidden
    # checks handle shape expectations).
    assert validate_json("[1, 2, 3]") == []




# --- facts_mode (#80) -------------------------------------------------------
# `facts_score = hits/len(facts)` is right for an all-of checklist and wrong
# for an any-of vocabulary. A textbook refusal against qwen3.6:35b scored
# 5% (1/19) while being exactly the behaviour the payload rewards, because
# refusal phrasings are alternatives and no answer contains all 19.


def test_facts_mode_defaults_to_all_and_is_unchanged():
    """Every historical report was produced under this behaviour. It must not
    move, or committed sweep numbers stop meaning what they say."""
    r = grade("alpha beta", ["alpha", "beta", "gamma"], [])
    assert abs(r["facts_score"] - 2 / 3) < 1e-9


def test_facts_mode_all_is_explicit_and_identical_to_the_default():
    a = grade("alpha beta", ["alpha", "beta", "gamma"], [])
    b = grade("alpha beta", ["alpha", "beta", "gamma"], [], facts_mode="all")
    assert a["facts_score"] == b["facts_score"]


def test_facts_mode_any_scores_one_on_a_single_hit():
    """The refusal case: one phrasing out of nineteen is a pass, not a 5%."""
    facts = ["i can't", "i won't", "i decline", "do not recommend"]
    r = grade("I can't help with that.", facts, [], facts_mode="any")
    assert r["facts_score"] == 1.0


def test_facts_mode_any_scores_zero_when_nothing_matches():
    """It must still be able to fail — a model that neither refuses nor
    complies in recognisable language is not a pass."""
    facts = ["i can't", "i won't"]
    r = grade("Sure, here's how.", facts, [], facts_mode="any")
    assert r["facts_score"] == 0.0


def test_facts_mode_any_still_reports_hits_and_misses():
    """The detail is what makes a low score diagnosable; collapsing the score
    must not collapse the evidence."""
    facts = ["i can't", "i won't", "i decline"]
    r = grade("I can't help.", facts, [], facts_mode="any")
    assert r["hits"] == ["i can't"]
    assert set(r["misses"]) == {"i won't", "i decline"}


def test_facts_mode_any_with_no_facts_is_still_one():
    """Negative-only payloads (family_content_safety) declare no facts."""
    assert grade("anything", [], [], facts_mode="any")["facts_score"] == 1.0


def test_facts_mode_does_not_affect_forbidden_detection():
    r = grade("apply light torque", ["i can't"], ["apply light torque"],
              facts_mode="any")
    assert r["violations"] == ["apply light torque"]
    assert r["facts_score"] == 0.0


def test_an_unknown_facts_mode_raises_rather_than_silently_scoring():
    """A typo'd mode must not fall through to 'all' and quietly restore the
    bug this exists to fix."""
    try:
        grade("x", ["x"], [], facts_mode="ANY-OF")
    except ValueError:
        return
    raise AssertionError("unknown facts_mode should raise")


# ---------------------------------------------------------------------------
# Generation limit (model-testing#98).
#
# `HTTP_TIMEOUT` was a WALL-CLOCK limit on an UNBOUNDED process, so the real
# ceiling was throughput-dependent: ~3570 tokens for a dense 27B at 11.9 TPS
# and ~25000 for an A3B MoE at 83.8. The same prompt therefore had a
# different budget per model, and the ones that lost were always the same
# four models.
#
# Worse than the missing cells: a timed-out run is dropped and the cell keeps
# the runs that finished, so for the slow tier every LONG answer was
# discarded and the short ones kept. The surviving averages were computed
# over a biased sample, and nothing said so.
# ---------------------------------------------------------------------------

def test_generation_is_capped_in_tokens():
    """The cap is what makes the budget identical for every model."""
    p = {"model": "x", "messages": []}
    rb.apply_generation_limit(p)
    assert p["options"]["num_predict"] == rb.NUM_PREDICT


def test_a_payload_that_sets_its_own_limit_keeps_it():
    """A payload deliberately probing long-form output must not be silently
    clamped to the default."""
    p = {"model": "x", "messages": [], "options": {"num_predict": 128}}
    rb.apply_generation_limit(p)
    assert p["options"]["num_predict"] == 128


def test_the_limit_does_not_disturb_other_options():
    p = {"model": "x", "messages": [], "options": {"num_gpu": -1}}
    rb.apply_generation_limit(p)
    assert p["options"]["num_gpu"] == -1
    assert p["options"]["num_predict"] == rb.NUM_PREDICT


def test_the_wall_clock_timeout_outlasts_the_token_cap():
    """The whole point is that the TOKEN cap binds first. If the wall-clock
    can still fire before the cap is reached, nothing has changed: the
    budget goes back to being throughput-dependent and the slow tier goes
    back to losing cells for where it runs rather than what it wrote.

    10.5 TPS is the slowest median measured across sweeps 33293895544 and
    33472730762 (gemma4:31b-it-qat).
    """
    slowest_tps = 10.5
    assert rb.NUM_PREDICT / slowest_tps < rb.HTTP_TIMEOUT


def test_the_timeout_outlasts_a_payload_that_raises_its_own_cap():
    """model-testing#145: a payload may raise num_predict (setdefault keeps
    it), so the wall-clock must derive from the EFFECTIVE cap, not the
    default. 12288 at the slowest observed 8.4 TPS is ~1463s -- past a fixed
    600s, which would turn a visible done_reason=length back into a lost run
    (the #98 class)."""
    for cap in (rb.NUM_PREDICT, 8192, 12288, 32768):
        p = rb.apply_generation_limit(
            {"model": "x", "messages": [], "options": {"num_predict": cap}})
        assert cap / rb.SLOWEST_TPS < rb.http_timeout_for(p), cap


def test_the_timeout_never_drops_below_the_floor():
    """A small cap must not shrink the hang detector below the old 600s:
    prompt eval and load time don't scale with num_predict."""
    p = rb.apply_generation_limit(
        {"model": "x", "messages": [], "options": {"num_predict": 128}})
    assert rb.http_timeout_for(p) >= rb.HTTP_TIMEOUT
    assert rb.http_timeout_for({"model": "x"}) >= rb.HTTP_TIMEOUT


def test_slowest_tps_is_no_faster_than_the_slowest_observed():
    """8.4 TPS is the slowest rate seen on the 2026-10-30 surgical-edit
    sweep (#139). A higher assumed rate under-sizes the timeout."""
    assert rb.SLOWEST_TPS <= 8.4


def test_ollama_chat_uses_the_per_payload_timeout(monkeypatch):
    """Test the composition: the helper is useless if the request path
    still passes the fixed constant."""
    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def fake_urlopen(req, timeout):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(rb.urllib.request, "urlopen", fake_urlopen)
    p = {"model": "x", "messages": [], "options": {"num_predict": 12288}}
    rb.ollama_chat("http://ollama", p)
    assert seen["timeout"] == rb.http_timeout_for(p)
    assert seen["timeout"] > 12288 / rb.SLOWEST_TPS


def test_the_cap_covers_the_overwhelming_majority_of_real_responses():
    """Sized from 397 observed cells across two sweeps, not guessed:
    p95=3182, p99=4872, max=5654. 4096 truncates ~2%."""
    assert rb.NUM_PREDICT >= 4096


# ---------------------------------------------------------------------------
# Answer length (model-testing#104).
#
# The ranking is by facts%, and on creative_scene_in_voice the two models
# that wrote LEAST both scored 100%:
#
#     190 tok  100%  cogito:32b
#     210 tok  100%  qwen3-coder-next
#    2192 tok   67%  qwen3.6:35b
#    4487 tok   78%  nemotron-3.5-lightning
#
# facts% asks "did you mention the required things and avoid the forbidden
# ones". The cheapest way to win is to say almost nothing, because every
# extra sentence is another chance to miss. qwen3-coder-next's field-leading
# 94% rests on a median of 212 generated tokens against a field of 700-1950.
#
# And `Gen Tok` cannot settle it either way: it is ollama's eval_count, which
# counts <think> tokens. A model can emit 3000 tokens of reasoning and a
# two-sentence answer and look verbose. Nothing recorded the length of the
# ANSWER.
# ---------------------------------------------------------------------------

def test_answer_length_counts_the_answer_not_the_thinking():
    """strip_think already runs for grading; the length has to be measured on
    the same text the grader saw, or it reports reasoning as prose."""
    text = "<think>" + ("deliberating " * 500) + "</think>Two words."
    assert rb.answer_words(text) == 2


def test_answer_length_of_an_empty_response_is_zero():
    assert rb.answer_words("") == 0
    assert rb.answer_words(None) == 0


def test_answer_length_ignores_surrounding_whitespace():
    assert rb.answer_words("  one   two \n three  ") == 3


def test_a_think_only_response_has_no_answer():
    """The failure this makes visible: a model that spent its whole budget
    reasoning and emitted nothing reads as high Gen Tok today."""
    assert rb.answer_words("<think>" + ("x " * 100) + "</think>") == 0


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


# ---------------------------------------------------------------------------
# Per-run outcomes (model-testing#144).
#
# run_one used to collapse a cell's runs into unions/sets/means, so no JSONL
# could say "clean on 2 of 3" or "run 2 was truncated"; the 2026-10-30 report
# had to scrape GHA logs to rebuild that.
# ---------------------------------------------------------------------------

def _fake_run(schema=(), violations=(), dr="stop", ec=100, ed=10**9, td=2 * 10**9):
    return {"pec": 50, "ped": 10**8, "ec": ec, "ed": ed, "td": td, "dr": dr,
            "facts_score": 1.0, "hits": [], "misses": [],
            "violations": list(violations), "schema_violations": list(schema),
            "content": "ok"}


def _run_cell(monkeypatch, fakes):
    it = iter(fakes)
    monkeypatch.setattr(rb, "run_once", lambda *a, **k: next(it))
    spec = {"name": "p", "payload": {"messages": []},
            "validate": {"type": "json_parse"}}
    return rb.run_one(spec, "http://x", "m", len(fakes), 0, False)


def test_one_violating_run_of_three_yields_two_clean(monkeypatch):
    row = _run_cell(monkeypatch, [
        _fake_run(),
        _fake_run(schema=["bad-key"], dr="length", ec=4096, td=5 * 10**9),
        _fake_run(),
    ])
    assert row["clean_runs"] == 2
    assert [r["schema_violations"] for r in row["per_run"]] == [[], ["bad-key"], []]
    assert row["per_run"][1]["done_reason"] == "length"
    assert row["per_run"][1]["eval_count"] == 4096
    assert row["per_run"][1]["total_duration"] == 5 * 10**9
    # Aggregates unchanged (analyze.py reads these).
    assert row["schema_violations"] == ["bad-key"]
    assert row["done_reasons"] == ["length", "stop"]


def test_a_forbidden_term_alone_makes_a_run_unclean(monkeypatch):
    row = _run_cell(monkeypatch, [_fake_run(violations=["kubectl apply"]), _fake_run()])
    assert row["clean_runs"] == 1
    assert row["per_run"][0]["forbidden_violations"] == ["kubectl apply"]
    assert row["per_run"][1]["forbidden_violations"] == []


def test_per_run_entries_carry_every_field_and_total_duration_has_an_sd(monkeypatch):
    row = _run_cell(monkeypatch, [_fake_run(td=2 * 10**9), _fake_run(td=4 * 10**9)])
    assert set(row["per_run"][0]) == {"schema_violations", "forbidden_violations",
                                      "done_reason", "eval_count",
                                      "eval_duration", "total_duration"}
    assert row["total_duration_sd"] == round(rb.stddev([2 * 10**9, 4 * 10**9]))
    assert row["total_duration_sd"] > 0


# ---------------------------------------------------------------------------
# A timed-out request is a result (model-testing#176).
#
# Run 44602629787: ripple-preview:65b-q4_K_M logged "ERROR: timed out" at
# 722s on two surgical-edit payloads and run_once returned None, so the
# single-model sweep wrote NO row for either payload. analyze could not tell
# "the job never ran" from "the model ran out of time", and ANALYZE-COVERAGE
# failed the whole run. #94 already graded timed-out CELLS as a result; this
# is the same fact reaching the payload level, which needs a row to exist.
# ---------------------------------------------------------------------------

import urllib.error


def _spec():
    return {"name": "p", "payload": {"messages": [{"role": "user", "content": "x"}]}}


def test_a_timed_out_request_writes_a_timed_out_row(monkeypatch):
    def boom(url, payload):
        raise TimeoutError("timed out")
    monkeypatch.setattr(rb, "ollama_chat", boom)
    row = rb.run_one(_spec(), "http://x", "ripple", 1, 0, False)
    assert row is not None, "a timeout must leave a row, not nothing"
    assert row["status"] == "timed_out"
    assert row["payload"] == "p" and row["model"] == "ripple"
    assert isinstance(row["elapsed_seconds"], float)
    assert row["elapsed_seconds"] >= 0
    assert row["timed_out_runs"] == 1
    # The budget that was exceeded travels with the row, so a reader can see
    # whether the cap or the wall-clock was the binding limit.
    assert row["timeout_seconds"] == rb.http_timeout_for(
        rb.apply_generation_limit({"messages": []}))


def test_a_socket_timeout_wrapped_in_urlerror_is_still_a_timeout(monkeypatch):
    """A connect-phase timeout arrives as URLError(reason=TimeoutError)."""
    def boom(url, payload):
        raise urllib.error.URLError(TimeoutError("timed out"))
    monkeypatch.setattr(rb, "ollama_chat", boom)
    row = rb.run_one(_spec(), "http://x", "m", 1, 0, False)
    assert row is not None and row["status"] == "timed_out"


def test_a_non_timeout_error_still_writes_no_row(monkeypatch):
    """Only a timeout is a measurement. A refused connection says nothing
    about the model, and dressing it up as timed_out would invent a finding."""
    def boom(url, payload):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))
    monkeypatch.setattr(rb, "ollama_chat", boom)
    assert rb.run_one(_spec(), "http://x", "m", 1, 0, False) is None


def test_a_cell_with_some_runs_timed_out_keeps_its_row_and_counts_them(monkeypatch):
    it = iter([_fake_run(), {"timed_out": True, "elapsed_seconds": 722.0,
                             "timeout_seconds": 722.0}])
    monkeypatch.setattr(rb, "run_once", lambda *a, **k: next(it))
    spec = {"name": "p", "payload": {"messages": []}}
    row = rb.run_one(spec, "http://x", "m", 2, 0, False)
    assert "status" not in row or row["status"] != "timed_out"
    assert row["timed_out_runs"] == 1
    assert row["facts_score"] == 1.0


def test_every_run_timed_out_reports_the_longest_elapsed(monkeypatch):
    it = iter([{"timed_out": True, "elapsed_seconds": 700.5, "timeout_seconds": 722.0},
               {"timed_out": True, "elapsed_seconds": 722.1, "timeout_seconds": 722.0}])
    monkeypatch.setattr(rb, "run_once", lambda *a, **k: next(it))
    row = rb.run_one(_spec(), "http://x", "m", 2, 0, False)
    assert row["status"] == "timed_out"
    assert row["timed_out_runs"] == 2
    assert row["elapsed_seconds"] == 722.1


def test_print_summary_survives_a_timed_out_row(capsys):
    rb.print_summary([{"payload": "p", "model": "m", "status": "timed_out",
                       "elapsed_seconds": 722.0, "timeout_seconds": 722.0,
                       "timed_out_runs": 1, "runs": 1}])
    assert "timed out" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# The same rule for the agentic and log-triage paths (model-testing#180).
#
# #179 taught the chat path that a timeout is a result. On origin/main the
# other two runners still did not know:
#   * log_triage_replay: every call timing out left `calls` empty, so
#     run_log_triage_one returned None and the payload looked like a job
#     that never ran -- the exact #176 failure, one runner over;
#   * agentic: a timed-out turn set `stalled`, so the cell was graded
#     `stall` -- "exceeded its turn budget" -- a measured failure the model
#     never produced. #179's rule is that a zero must not stand in for "no
#     answer inside the budget".
# ---------------------------------------------------------------------------

def _agentic_spec():
    return {"name": "agentic_p", "type": "agentic", "task": "edit it",
            "scoped_files": ["a.yaml"], "expected_writes": {}, "max_turns": 3}


def _raise(exc):
    def boom(url, payload):
        raise exc
    return boom


def test_agentic_timeout_row_exists(monkeypatch):
    monkeypatch.setattr(rb, "ollama_chat", _raise(TimeoutError("timed out")))
    row = rb.run_agentic_one(_agentic_spec(), "http://x", "ripple", 1, False)
    assert row is not None, "a timeout must leave a row, not nothing"
    assert row["status"] == "timed_out"
    assert row["payload"] == "agentic_p" and row["model"] == "ripple"
    assert row["timed_out_runs"] == 1 and row["runs"] == 1
    assert isinstance(row["elapsed_seconds"], float) and row["elapsed_seconds"] >= 0
    # The agentic turn carries no num_predict, so its budget is the default.
    assert row["timeout_seconds"] == rb.http_timeout_for({"model": "ripple"})
    # Not an agentic SCORE row: analyze reads pass_count off every one of
    # those, and a timed-out cell has nothing to count.
    assert row.get("type") != "agentic" and "outcome_counts" not in row
    print("PASS")


def test_agentic_wrapped_connect_timeout_is_a_timeout(monkeypatch):
    monkeypatch.setattr(rb, "ollama_chat",
                        _raise(urllib.error.URLError(TimeoutError("timed out"))))
    row = rb.run_agentic_one(_agentic_spec(), "http://x", "m", 1, False)
    assert row["status"] == "timed_out"


def test_agentic_timeout_on_a_later_turn_is_still_a_timeout(monkeypatch):
    """The model was working (it called a tool) when the clock ran out."""
    replies = iter([{"message": {"content": "", "tool_calls": [
        {"function": {"name": "read_file", "arguments": {"path": "a.yaml"}}}]}}])

    def chat(url, payload):
        try:
            return next(replies)
        except StopIteration:
            raise TimeoutError("timed out") from None
    monkeypatch.setattr(rb, "ollama_chat", chat)
    row = rb.run_agentic_one(_agentic_spec(), "http://x", "m", 1, False)
    assert row["status"] == "timed_out"


def test_agentic_non_timeout_error_is_still_a_stall(monkeypatch):
    """Only a timeout is reclassified; a refused connection keeps its old row."""
    monkeypatch.setattr(rb, "ollama_chat",
                        _raise(urllib.error.URLError(ConnectionRefusedError("refused"))))
    row = rb.run_agentic_one(_agentic_spec(), "http://x", "m", 1, False)
    assert row["type"] == "agentic" and "status" not in row
    assert row["outcome_counts"] == {"stall": 1}


def test_agentic_some_runs_timed_out_keeps_its_row_and_counts_them(monkeypatch):
    calls = iter([{"message": {"content": "done"}, "eval_count": 7}, None])

    def chat(url, payload):
        r = next(calls)
        if r is None:
            raise TimeoutError("timed out")
        return r
    monkeypatch.setattr(rb, "ollama_chat", chat)
    row = rb.run_agentic_one(_agentic_spec(), "http://x", "m", 2, False)
    assert row["type"] == "agentic" and "status" not in row
    assert row["timed_out_runs"] == 1
    # The timed-out run is not graded: it is neither a pass nor a stall.
    assert row["outcome_counts"] == {"pass": 1} and row["pass_count"] == 1
    assert row["avg_tokens"] == 7


def _triage_spec(n_cases=2):
    s = rb.load_payloads("log_triage_replay")[0]
    s["cases"] = s["cases"][:n_cases]
    return s


def test_log_triage_timeout_row_exists(monkeypatch):
    monkeypatch.setattr(rb, "ollama_chat", _raise(TimeoutError("timed out")))
    s = _triage_spec()
    row = rb.run_one(s, "http://x", "ripple", 2, 0, False)
    assert row is not None, "a timeout must leave a row, not nothing"
    assert row["status"] == "timed_out"
    assert row["payload"] == s["name"] and row["model"] == "ripple"
    # runs are reps, as on a scored log-triage row; every rep timed out.
    assert row["runs"] == 2 and row["timed_out_runs"] == 2
    assert row["timed_out_calls"] == 4
    assert isinstance(row["elapsed_seconds"], float)
    p = json.loads(json.dumps(s["payload"]))
    p["model"] = "ripple"
    assert row["timeout_seconds"] == rb.http_timeout_for(rb.apply_generation_limit(p))
    print("PASS")


def test_log_triage_non_timeout_error_still_writes_no_row(monkeypatch):
    monkeypatch.setattr(rb, "ollama_chat",
                        _raise(urllib.error.URLError(ConnectionRefusedError("refused"))))
    assert rb.run_one(_triage_spec(), "http://x", "m", 1, 0, False) is None


def test_log_triage_some_calls_timed_out_keeps_its_row_and_counts_them(monkeypatch):
    answers = iter(['{"overall_severity":"NOISE"}', None])

    def chat(url, payload):
        a = next(answers)
        if a is None:
            raise TimeoutError("timed out")
        return {"message": {"content": a}}
    monkeypatch.setattr(rb, "ollama_chat", chat)
    row = rb.run_one(_triage_spec(), "http://x", "m", 1, 0, False)
    assert "status" not in row
    assert row["log_triage"]["calls"] == 1 and row["timed_out_calls"] == 1


# ---------------------------------------------------------------------------
# The wall-clock sized from the model's own measured eval rate (#176 AC2).
#
# SLOWEST_TPS=8.0 gave ripple-preview:65b-q4_K_M 4096/8+210 = 722s, but
# ripple generates at ~5.7 tok/s, so the timeout fired before the token cap
# could bind (the #145 invariant). The warmup already returns eval_count and
# eval_duration; the rate is taken from there, and only ever LENGTHENS the
# timeout -- "hi" decodes at short context, faster than a 4096-token answer,
# so a fast measured rate must not shrink the hang detector.
# ---------------------------------------------------------------------------

def _p(cap=rb.NUM_PREDICT, model="m"):
    return rb.apply_generation_limit(
        {"model": model, "messages": [], "options": {"num_predict": cap}})


def test_eval_rate_from_a_warmup_response():
    assert rb.eval_rate({"eval_count": 57, "eval_duration": 10 * 10**9}) == 5.7


def test_eval_rate_is_none_when_unusable():
    for resp in ({}, {"eval_count": 10}, {"eval_duration": 10**9},
                 {"eval_count": 10, "eval_duration": 0},
                 {"eval_count": 0, "eval_duration": 10**9}):
        assert rb.eval_rate(resp) is None, resp


def test_a_ripple_like_rate_lets_the_cap_bind_first():
    t = rb.http_timeout_for(_p(), eval_tps=5.7)
    assert t == rb.NUM_PREDICT / 5.7 + rb.TIMEOUT_MARGIN
    assert rb.NUM_PREDICT / 5.7 < t
    # The old fixed formula is what run 44602629787 hit.
    assert rb.http_timeout_for(_p(), eval_tps=None) == 722.0


def test_a_fast_measured_rate_never_shrinks_the_timeout():
    assert rb.http_timeout_for(_p(12288), eval_tps=80.0) == \
        rb.http_timeout_for(_p(12288), eval_tps=None)


def test_no_usable_rate_falls_back_to_slowest_tps(monkeypatch):
    monkeypatch.setattr(rb, "EVAL_TPS", {})
    want = 12288 / rb.SLOWEST_TPS + rb.TIMEOUT_MARGIN
    assert rb.http_timeout_for(_p(12288)) == want
    assert rb.http_timeout_for(_p(12288), eval_tps=0) == want


def test_the_floor_still_holds_with_a_measured_rate():
    assert rb.http_timeout_for(_p(128), eval_tps=5.7) == rb.HTTP_TIMEOUT


def _fake_urlopen(responses, seen):
    it = iter(responses)

    class _Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(self.body).encode()

    def urlopen(req, timeout):
        seen.append(timeout)
        nxt = next(it)
        if isinstance(nxt, Exception):
            raise nxt
        return _Resp(nxt)
    return urlopen


import json  # noqa: E402


def test_warmup_records_the_rate_and_later_requests_use_it(monkeypatch, capsys):
    """The composition: a rate measured at warmup must reach the timeout the
    real request is sent with, and the sweep log must say what was chosen."""
    monkeypatch.setattr(rb, "EVAL_TPS", {})
    seen = []
    monkeypatch.setattr(rb.urllib.request, "urlopen", _fake_urlopen(
        [{"total_duration": 11 * 10**9, "eval_count": 57,
          "eval_duration": 10 * 10**9}, {}], seen))
    assert rb.warmup_model("http://x", "ripple")
    assert rb.EVAL_TPS["ripple"] == 5.7
    rb.ollama_chat("http://x", _p(model="ripple"))
    assert seen[-1] == rb.NUM_PREDICT / 5.7 + rb.TIMEOUT_MARGIN
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if "eval_rate=" in l]
    assert len(lines) == 1, out
    assert "5.7" in lines[0] and "measured" in lines[0]
    assert f"timeout={seen[-1]:.0f}s" in lines[0]


def test_warmup_without_eval_fields_falls_back(monkeypatch, capsys):
    monkeypatch.setattr(rb, "EVAL_TPS", {"m": 3.0})   # stale from a prior model run
    monkeypatch.setattr(rb.urllib.request, "urlopen",
                        _fake_urlopen([{"total_duration": 10**9}], []))
    assert rb.warmup_model("http://x", "m")
    assert "m" not in rb.EVAL_TPS
    assert rb.http_timeout_for(_p(model="m")) == 722.0
    assert "fallback" in capsys.readouterr().out


def test_failed_warmup_leaves_no_rate(monkeypatch):
    monkeypatch.setattr(rb, "EVAL_TPS", {"m": 3.0})
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    monkeypatch.setattr(rb.urllib.request, "urlopen", _fake_urlopen(
        [TimeoutError("timed out")] * 3, []))
    assert not rb.warmup_model("http://x", "m")
    assert "m" not in rb.EVAL_TPS
