#!/usr/bin/env python3
"""Cell coverage and confabulation checks (sweep 31915264873).

Three defects in one run, all the same shape — something absent reading as
something fine:

  * `qwen3.8:27b` is in models.yaml but not in ollama, so it produced no
    rows and no error
  * ANALYZE-COVERAGE counted PAYLOADS, so 23-of-24 passed while an entire
    model was missing from all of them
  * the generated analysis invented `qwen3.10:30b` with fabricated metrics,
    sitting under a correct computed table

The operator caught all three by reading the report. That is the bar these
tests exist to replace.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "benchmarks"
_spec = importlib.util.spec_from_file_location("analyze", BIN / "analyze.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["analyze"] = mod
_spec.loader.exec_module(mod)


class InventedModelTest(unittest.TestCase):

    REAL = ["qwen3.6:35b", "qwen3.6:27B"]

    def test_the_actual_confabulation_is_caught(self):
        text = ("- **`qwen3.10:30b`**: Strong constraint adherence with "
                "near-zero quality misses, ~15% higher token consumption.")
        self.assertEqual(mod.invented_models(text, self.REAL), ["qwen3.10:30b"])

    def test_real_models_are_not_flagged(self):
        text = "`qwen3.6:35b` excels (avg 7 turns). `qwen3.6:27B` trails."
        self.assertEqual(mod.invented_models(text, self.REAL), [])

    def test_case_differences_are_not_confabulation(self):
        """models.yaml says 27B, prose may say 27b. Flagging that would be
        noise, and noise is what gets a check switched off."""
        self.assertEqual(mod.invented_models("`qwen3.6:27b` did fine",
                                             self.REAL), [])

    def test_a_requested_but_absent_model_is_still_invented(self):
        """qwen3.8:27b IS in models.yaml but produced no rows. Prose about
        its results is about nothing, so being requested must not excuse
        it — that is precisely the misleading case."""
        self.assertEqual(
            mod.invented_models("`qwen3.8:27b` completely fails agentic",
                                self.REAL),
            ["qwen3.8:27b"])


class CellCoverageTest(unittest.TestCase):

    def test_a_wholly_absent_model_is_caught(self):
        """The bug: 23 of 24 payloads present, one model missing from all
        of them, and the payload-level check reported near-complete."""
        payloads = ["a", "b", "c"]
        models = ["m1", "m2"]
        seen = {("m1", "a"), ("m1", "b"), ("m1", "c")}
        c = mod.cell_coverage(payloads, models, seen)
        self.assertFalse(c["complete"])
        self.assertEqual(c["expected"], 6)
        self.assertEqual(c["analyzed"], 3)
        self.assertEqual(mod.missing_by_model(c["missing"]), {"m2": 3})

    def test_one_missing_cell_is_caught(self):
        """family_citation_discipline ran for 35b only."""
        c = mod.cell_coverage(["a", "b"], ["m1", "m2"],
                              {("m1", "a"), ("m1", "b"), ("m2", "a")})
        self.assertEqual(c["missing"], [("m2", "b")])

    def test_complete_is_complete(self):
        c = mod.cell_coverage(["a"], ["m1"], {("m1", "a")})
        self.assertTrue(c["complete"])

    def test_unknown_expectation_is_not_complete(self):
        self.assertFalse(mod.cell_coverage(None, ["m"], set())["complete"])
        self.assertFalse(mod.cell_coverage(["p"], None, set())["complete"])


class WhollyAbsentTest(unittest.TestCase):
    """model-testing#94: partial gaps must not grade like a lost family.

    Run 33293895544 lost 4 cells of 216 to per-call timeouts on the two
    slowest models and was FAILED for it, which discarded a complete report
    and skipped the Claude analysis entirely. Sweep 31915264873 lost an
    entire model and must keep failing. These are the two sides.
    """

    PAYLOADS = ["a", "b", "c", "d"]
    MODELS = ["m1", "m2"]

    def _cells(self, seen):
        return mod.cell_coverage(self.PAYLOADS, self.MODELS, seen)

    def test_a_model_absent_from_every_payload_is_wholly_absent(self):
        seen = {("m1", p) for p in self.PAYLOADS}
        c = self._cells(seen)
        self.assertEqual(mod.wholly_absent_models(c, self.PAYLOADS), ["m2"])

    def test_a_model_missing_only_some_payloads_is_NOT_wholly_absent(self):
        # The 33293895544 shape: present for most, timed out on a few.
        seen = {("m1", p) for p in self.PAYLOADS} | {("m2", "a"), ("m2", "b")}
        c = self._cells(seen)
        self.assertEqual(mod.missing_by_model(c["missing"]), {"m2": 2})
        self.assertEqual(mod.wholly_absent_models(c, self.PAYLOADS), [])

    def test_missing_exactly_one_cell_is_not_wholly_absent(self):
        seen = ({("m1", p) for p in self.PAYLOADS}
                | {("m2", p) for p in self.PAYLOADS if p != "d"})
        c = self._cells(seen)
        self.assertEqual(mod.wholly_absent_models(c, self.PAYLOADS), [])

    def test_complete_coverage_has_none_absent(self):
        seen = {(m, p) for m in self.MODELS for p in self.PAYLOADS}
        c = self._cells(seen)
        self.assertEqual(mod.wholly_absent_models(c, self.PAYLOADS), [])

    def test_unknown_expectation_reports_none_rather_than_guessing(self):
        c = mod.cell_coverage(None, self.MODELS, set())
        self.assertEqual(mod.wholly_absent_models(c, None), [])

    def test_marker_carries_wholly_absent_so_ci_can_grade(self):
        seen = {("m1", p) for p in self.PAYLOADS} | {("m2", "a")}
        c = self._cells(seen)
        c["wholly_absent"] = mod.wholly_absent_models(c, self.PAYLOADS)
        marker = mod.cell_marker(c)
        self.assertIn("wholly_absent=none", marker)

        seen2 = {("m1", p) for p in self.PAYLOADS}
        c2 = self._cells(seen2)
        c2["wholly_absent"] = mod.wholly_absent_models(c2, self.PAYLOADS)
        self.assertIn("wholly_absent=m2", mod.cell_marker(c2))


class RenderCellCoverageTest(unittest.TestCase):
    """The gap has to be IN THE REPORT. render_coverage's own docstring says
    the report is what gets read; cell coverage previously lived only in a CI
    marker, so failing the job was the only way to surface it."""

    PAYLOADS = ["a", "b", "c", "d"]
    MODELS = ["m1", "m2"]

    def test_partial_names_the_model_and_the_count(self):
        seen = {("m1", p) for p in self.PAYLOADS} | {("m2", "a"), ("m2", "b")}
        c = mod.cell_coverage(self.PAYLOADS, self.MODELS, seen)
        out = mod.render_cell_coverage(c, self.PAYLOADS)
        self.assertIn("PARTIAL", out)
        self.assertIn("m2", out)
        self.assertIn("fewer samples", out)

    def test_wholly_absent_is_worded_differently_from_a_partial_gap(self):
        seen = {("m1", p) for p in self.PAYLOADS}
        c = mod.cell_coverage(self.PAYLOADS, self.MODELS, seen)
        out = mod.render_cell_coverage(c, self.PAYLOADS)
        self.assertIn("absent from ALL", out)

    def test_complete_says_so(self):
        seen = {(m, p) for m in self.MODELS for p in self.PAYLOADS}
        c = mod.cell_coverage(self.PAYLOADS, self.MODELS, seen)
        self.assertIn("Complete", mod.render_cell_coverage(c, self.PAYLOADS))

    def test_unknown_expectation_renders_nothing(self):
        c = mod.cell_coverage(None, self.MODELS, set())
        self.assertEqual(mod.render_cell_coverage(c, None), "")



if __name__ == "__main__":
    unittest.main()


BENCH = Path(__file__).resolve().parent.parent / "benchmarks"
_rspec = importlib.util.spec_from_file_location(
    "run_benchmark", BENCH / "run_benchmark.py")
rb = importlib.util.module_from_spec(_rspec)
sys.modules["run_benchmark"] = rb
_rspec.loader.exec_module(rb)


class PreflightTest(unittest.TestCase):
    """`qwen3.8:27b` sat in models.yaml while ollama had never pulled it.
    Every call failed, no rows were written, and the sweep reported success
    with the model simply absent from the comparison it was dispatched to
    make."""

    def test_a_missing_model_is_detected(self):
        self.assertEqual(
            rb.missing_models(["a:1", "b:2"], {"a:1"}), ["b:2"])

    def test_case_differences_are_not_missing_models(self):
        """models.yaml says 27B, ollama may report 27b. A spelling
        difference must not trigger a pull of something already present."""
        self.assertEqual(rb.missing_models(["qwen3.6:27B"],
                                           {"qwen3.6:27b"}), [])

    def test_unreachable_ollama_is_none_not_empty(self):
        """An empty set would mean 'no models installed' and try to pull
        everything; None means 'could not ask' and refuses to start."""
        self.assertIsNone(rb.available_models("http://127.0.0.1:1"))


class PullOutcomeTest(unittest.TestCase):
    """Ollama reports pull failures in an `error` field with HTTP 200, so
    the status code proves nothing and the stream is what decides."""

    def test_success_is_success(self):
        ok, _ = rb.pull_outcome(['{"status":"pulling"}', '{"status":"success"}'])
        self.assertTrue(ok)

    def test_an_error_field_is_a_failure_despite_http_200(self):
        ok, detail = rb.pull_outcome(
            ['{"status":"pulling"}', '{"error":"model not found"}'])
        self.assertFalse(ok)
        self.assertIn("not found", detail)

    def test_a_truncated_stream_is_not_success(self):
        """A pull cut off mid-download must not read as complete — that
        would leave the sweep running against a half-present model."""
        ok, detail = rb.pull_outcome(['{"status":"pulling 40%"}'])
        self.assertFalse(ok)
        self.assertIn("without success", detail)

    def test_garbage_lines_do_not_crash_it(self):
        ok, _ = rb.pull_outcome(["not json", '{"status":"success"}'])
        self.assertTrue(ok)


class WarmupTest(unittest.TestCase):
    """A failed warmup used to be swallowed. With no warmup the FIRST
    measured run absorbs the load-into-VRAM cost and it is recorded as
    inference latency — the row reads as a slow model rather than a missing
    warmup, and comparing that number is the entire point of a sweep."""

    def test_it_returns_false_rather_than_pretending(self):
        self.assertFalse(rb.warmup_model("http://127.0.0.1:1", "m", attempts=1))

    def test_it_retries_before_giving_up(self):
        """A model that has just been pulled may need a moment before it
        answers, and pulls are now the common path."""
        calls = []
        orig = rb.ollama_chat
        try:
            def flaky(url, payload):
                calls.append(1)
                if len(calls) < 2:
                    raise RuntimeError("model is loading")
                return {"total_duration": 1_000_000}
            rb.ollama_chat = flaky
            self.assertTrue(rb.warmup_model("http://x", "m", attempts=3))
            self.assertEqual(len(calls), 2)
        finally:
            rb.ollama_chat = orig


class EvictionTest(unittest.TestCase):
    """Without emptying VRAM first, a model's 'load time' depends on whether
    it happened to be resident — so the first model in a sweep pays a cold
    load and the rest may not, and whichever model ran last previously gets
    a flattering number. That is not comparable, which is the one thing a
    comparison sweep must be."""

    def test_resident_models_are_listed(self):
        payload = {"models": [{"name": "a:1"}, {"name": "b:2"}]}
        self.assertEqual(rb.parse_resident(payload), ["a:1", "b:2"])

    def test_nothing_resident_is_an_empty_list(self):
        self.assertEqual(rb.parse_resident({"models": []}), [])

    def test_unreadable_is_none_not_empty(self):
        """'nothing is resident' means proceed; 'could not ask' means the
        eviction never happened and the next number is not a cold load."""
        self.assertIsNone(rb.parse_resident(None))

    def test_entries_without_a_name_are_skipped(self):
        self.assertEqual(rb.parse_resident({"models": [{"size": 1}]}), [])


class DryRunTest(unittest.TestCase):
    """--dry-run exists to check payload wiring with no ollama at all.
    Gating the preflight on it was a regression that made the one mode
    designed to work offline the one mode that could not."""

    def test_dry_run_reaches_payload_loading_without_ollama(self):
        import subprocess, sys as _s
        from pathlib import Path as _P
        root = _P(__file__).resolve().parent.parent
        r = subprocess.run(
            [_s.executable, str(root / "benchmarks" / "run_benchmark.py"),
             "--dry-run", "--payload", "factual_recall",
             "--ollama", "http://127.0.0.1:1"],
            capture_output=True, text=True, timeout=120, cwd=root)
        self.assertNotIn("could not reach", r.stderr,
                         "dry run must not require a reachable ollama")


class RunsColumnTest(unittest.TestCase):
    """A binary payload at runs=5 renders "20%", which reads like a quality
    percentage when it means 1 of 5. refusal_boundary showed 0% vs 100% at
    runs=1 and 20% vs 40% at runs=5 — the sample size is what separates a
    generational win from noise (homelab#1046)."""

    ROW = {"model": "m:1", "payload": "p", "facts_score": 0.2, "runs": 5,
           "eval_count": 10, "eval_duration": 1e9, "gen_tps": 5.0,
           "forbidden_violations": []}

    def test_runs_appears_in_the_table(self):
        t = mod.build_summary_table([self.ROW])
        self.assertIn("Runs", t)
        self.assertIn("| 5 |", t)

    def test_a_row_without_runs_shows_unknown_not_one(self):
        """Absent must not render as 1 — that would silently assert a sample
        size nobody recorded."""
        r = dict(self.ROW); r.pop("runs")
        self.assertIn("| ? |", mod.build_summary_table([r]))


class AbbreviationIsNotInventionTest(unittest.TestCase):
    """Sweep 33472730762 failed on an analysis that was entirely correct.

    The summariser wrote:

        `qwen3.6:35b` processes `~2207` tokens with zero hallucinations,
        while `gemma4:26b` requires `~2401` tokens to reach the same
        fidelity.

    Both numbers are exact matches for the computed table. `2207` is
    `qwen3.6:35b`'s real value and `2401` is `gemma4:26b-a4b-it-qat`'s. The
    model did not invent anything -- it dropped the `-a4b-it-qat` suffix.

    The guard failed the run, stamped "its analysis section is not
    trustworthy" on a true statement, and -- because the Claude summary step
    runs after this one -- discarded the paired Claude analysis of a 25-
    payload, 9-model sweep. That is the same cost model-testing#94 already
    fixed once for partial cells: a gate that fails a correct 9-hour run is
    a gate somebody deletes.

    So invention and abbreviation are graded apart. The distinction is not
    "does it look close enough" -- it is whether the short name identifies
    exactly ONE real model, and whether it stops at a component boundary.
    Anything else stays fatal.
    """

    REAL = ["qwen3.6:35b", "gemma4:26b-a4b-it-qat", "gemma4:31b-it-qat"]

    # --------------------------------------------- the run that failed
    def test_the_real_sentence_from_33472730762_is_not_invention(self):
        text = ("`qwen3.6:35b` processes `~2207` tokens with zero "
                "hallucinations, while `gemma4:26b` requires `~2401` tokens "
                "to reach the same fidelity.")
        invented, ambiguous, abbrev = mod.classify_named_models(text, self.REAL)
        self.assertEqual(invented, [])
        self.assertEqual(ambiguous, [])
        self.assertEqual(abbrev, {"gemma4:26b": "gemma4:26b-a4b-it-qat"})

    def test_invented_models_no_longer_flags_it(self):
        """The old entry point keeps working and keeps its meaning."""
        self.assertEqual(
            mod.invented_models("`gemma4:26b` was fastest", self.REAL), [])

    # ------------------------------------- what must STILL be fatal
    def test_the_original_confabulation_is_still_invention(self):
        """qwen3.10:30b prefixes nothing. This is the case the guard exists
        for and it must not be softened by any of the above."""
        invented, _, _ = mod.classify_named_models(
            "`qwen3.10:30b`: ~97% factual score", ["qwen3.6:35b"])
        self.assertEqual(invented, ["qwen3.10:30b"])

    def test_an_ambiguous_short_name_is_NOT_an_abbreviation(self):
        """With two 26b variants, `gemma4:26b` cannot be resolved. Silently
        attributing the claim to one of them is worse than failing: it reads
        as a verified statement about a model nobody chose."""
        known = self.REAL + ["gemma4:26b-instruct"]
        invented, ambiguous, abbrev = mod.classify_named_models(
            "`gemma4:26b` led on latency", known)
        self.assertEqual(abbrev, {})
        self.assertEqual(ambiguous, ["gemma4:26b"])
        self.assertEqual(invented, [])

    def test_a_boundary_prefix_matching_several_variants_is_ambiguous(self):
        """Two variants of the same base tag. The short form stops at a real
        component boundary, so it is a plausible abbreviation of either --
        which is exactly why it cannot be resolved."""
        known = ["qwen3.6:27B-instruct", "qwen3.6:27B-chat"]
        invented, ambiguous, abbrev = mod.classify_named_models(
            "`qwen3.6:27B` trails", known)
        self.assertEqual(abbrev, {})
        self.assertEqual(invented, [])
        self.assertEqual(ambiguous, ["qwen3.6:27B"])

    def test_a_mid_token_truncation_is_invention_not_abbreviation(self):
        """`qwen3.6:3` is a prefix of `qwen3.6:35b`, but it reads as a
        different size. An abbreviation drops whole suffix COMPONENTS
        (`-a4b-it-qat`); it never cuts a token in half."""
        invented, _, abbrev = mod.classify_named_models(
            "`qwen3.6:3` was slow", ["qwen3.6:35b"])
        self.assertEqual(abbrev, {})
        self.assertEqual(invented, ["qwen3.6:3"])

    def test_a_requested_but_absent_model_is_still_invention(self):
        """Unchanged from the original guard: being in models.yaml must not
        excuse prose about results that do not exist."""
        invented, _, _ = mod.classify_named_models(
            "`qwen3.8:27b` completely fails agentic", self.REAL)
        self.assertEqual(invented, ["qwen3.8:27b"])

    def test_case_still_does_not_matter(self):
        _, _, abbrev = mod.classify_named_models("`GEMMA4:26B` won",
                                                 self.REAL)
        self.assertEqual(abbrev, {"GEMMA4:26B": "gemma4:26b-a4b-it-qat"})

    # ------------------------------------------------- the marker
    def test_the_marker_reports_all_three_buckets(self):
        m = mod.confabulation_marker([], [], {})
        self.assertIn("invented=none", m)
        self.assertIn("ambiguous=none", m)
        self.assertIn("abbreviated=none", m)

    def test_the_marker_names_the_expansion_it_resolved(self):
        """A reader who sees a warning must be able to tell WHICH model the
        claim was about without opening the report."""
        m = mod.confabulation_marker([], [],
                                     {"gemma4:26b": "gemma4:26b-a4b-it-qat"})
        self.assertIn("gemma4:26b->gemma4:26b-a4b-it-qat", m)

    def test_the_marker_still_says_invented_when_it_is(self):
        m = mod.confabulation_marker(["qwen3.10:30b"], [], {})
        self.assertIn("invented=qwen3.10:30b", m)
        self.assertNotIn("invented=none", m)


class TruncationMustBeVisibleTest(unittest.TestCase):
    """A capped generation is still a partial answer — it just fails safely.

    Capping tokens (model-testing#98) fixes WHICH runs are lost, but it does
    not make a long answer complete. If the cap is silent, the same bias the
    timeout produced simply moves: an average over truncated responses reads
    exactly like an average over finished ones.

    Ollama already returns `done_reason`. `length` vs `stop` turns truncation
    into a VALUE in the data instead of an absence, which is the whole
    difference from the timeout it replaces — a timed-out run left no trace
    at all.
    """

    ROWS = [
        {"model": "qwen3.5:27B", "payload": "stress_constraint_hard",
         "done_reasons": ["length"]},
        {"model": "qwen3.5:27B", "payload": "creative_scene_in_voice",
         "done_reasons": ["length", "stop"]},
        {"model": "qwen3.5:27B", "payload": "factual_recall",
         "done_reasons": ["stop"]},
        {"model": "qwen3.6:35b", "payload": "factual_recall",
         "done_reasons": ["stop"]},
    ]

    def test_a_cell_that_hit_the_cap_is_counted(self):
        self.assertEqual(mod.truncation_summary(self.ROWS),
                         {"qwen3.5:27B": 2})

    def test_a_cell_where_only_some_runs_hit_the_cap_still_counts(self):
        """Two of three runs finishing does not make the average clean — it
        makes it a blend. That is the bias, not an exception to it."""
        self.assertIn("qwen3.5:27B", mod.truncation_summary(
            [{"model": "qwen3.5:27B", "payload": "p",
              "done_reasons": ["stop", "length", "stop"]}]))

    def test_a_clean_sweep_summarises_to_nothing(self):
        self.assertEqual(mod.truncation_summary(self.ROWS[2:]), {})

    def test_rows_from_before_this_change_do_not_read_as_truncated(self):
        """Every result file written before #98 has no done_reason at all.
        Absent must mean unknown, not 'fine' — but it must also not
        manufacture a finding out of old data."""
        self.assertEqual(
            mod.truncation_summary([{"model": "m", "payload": "p"}]), {})

    def test_the_marker_names_the_models_and_counts(self):
        m = mod.truncation_marker(mod.truncation_summary(self.ROWS))
        self.assertIn("ANALYZE-TRUNCATION", m)
        self.assertIn("qwen3.5:27B:2", m)

    def test_the_marker_says_none_when_there_is_none(self):
        self.assertIn("truncated=none", mod.truncation_marker({}))

    def test_the_report_section_names_the_affected_models(self):
        section = mod.render_truncation(mod.truncation_summary(self.ROWS))
        self.assertIn("qwen3.5:27B", section)
        self.assertIn("2", section)

    def test_a_clean_sweep_renders_no_section(self):
        """A section that always appears is a section nobody reads."""
        self.assertEqual(mod.render_truncation({}), "")


class TersenessIsRelativeToThePayloadTest(unittest.TestCase):
    """How much a model said, judged against what the task actually needed.

    An absolute word threshold cannot work here. `triage_one_line_
    classification` wants one line -- flagging every model on it would be
    noise, and noise is what gets a check switched off. `creative_scene_in_
    voice` wants a scene, and a 210-word reply to it is not a scene.

    So the reference is the PAYLOAD'S OWN median across models. That is
    self-scoping: on a task where everyone is terse the median is terse and
    nobody is flagged, and on a task where the field writes 1500 words a
    model that writes 200 stands out. No hand-picked bands to invent, and no
    per-payload config to keep in sync with the payloads.
    """

    @staticmethod
    def rows(pairs, payload="creative_scene_in_voice"):
        return [{"model": m, "payload": payload, "answer_words": w}
                for m, w in pairs]

    def test_a_model_far_below_its_peers_is_flagged(self):
        """The creative_scene_in_voice shape: 100% facts on a scene that was
        never written."""
        out = mod.terse_cells(self.rows([
            ("qwen3-coder-next:latest", 180), ("qwen3.6:35b", 1500),
            ("gemma4:26b-a4b-it-qat", 1400), ("nemotron", 1600)]))
        self.assertEqual(out, {"qwen3-coder-next:latest":
                               ["creative_scene_in_voice"]})

    def test_a_payload_where_everyone_is_terse_flags_nobody(self):
        """triage_one_line_classification. Terse is the CORRECT answer, and a
        check that scolds correct behaviour gets muted."""
        out = mod.terse_cells(self.rows(
            [("a", 8), ("b", 6), ("c", 9), ("d", 7)],
            payload="triage_one_line_classification"))
        self.assertEqual(out, {})

    def test_writing_MORE_than_the_field_is_never_flagged(self):
        """This measures whether the task was done, not whether the model was
        efficient. Penalising length would just invert the same bias."""
        out = mod.terse_cells(self.rows([
            ("verbose", 5000), ("a", 1000), ("b", 1100), ("c", 900)]))
        self.assertEqual(out, {})

    def test_too_few_models_means_no_verdict(self):
        """A median over two models is the other model. A single-model sweep
        would flag whichever one happened to be shorter."""
        out = mod.terse_cells(self.rows([("a", 100), ("b", 1000)]))
        self.assertEqual(out, {})

    def test_rows_from_before_this_change_are_exempt(self):
        """Every result written before #104 has no answer_words. Absent must
        not read as zero -- that would flag every model on every payload of
        every historical sweep."""
        out = mod.terse_cells([
            {"model": "a", "payload": "p"}, {"model": "b", "payload": "p"},
            {"model": "c", "payload": "p"}])
        self.assertEqual(out, {})

    def test_a_genuinely_empty_answer_IS_flagged(self):
        """Zero words is a real measurement, not a missing one -- the
        think-only response. It must not be confused with the exempt case."""
        out = mod.terse_cells(self.rows([
            ("silent", 0), ("a", 1000), ("b", 1100), ("c", 900)]))
        self.assertIn("silent", out)

    def test_the_marker_names_the_models_and_counts(self):
        m = mod.terseness_marker({"qwen3-coder-next:latest": ["a", "b"]})
        self.assertIn("ANALYZE-TERSENESS", m)
        self.assertIn("qwen3-coder-next:latest:2", m)

    def test_the_marker_says_none_when_there_is_none(self):
        self.assertIn("terse=none", mod.terseness_marker({}))

    def test_a_clean_sweep_renders_no_section(self):
        self.assertEqual(mod.render_terseness({}), "")

    def test_the_section_explains_what_it_measured_against(self):
        """'terse' without a reference is an accusation, not a finding."""
        s = mod.render_terseness({"m": ["creative_scene_in_voice"]})
        self.assertIn("creative_scene_in_voice", s)
        self.assertIn("median", s.lower())


class NothingIsDefinedAfterTheEntrypointTest(unittest.TestCase):
    """Sweep 33831285722 died with `NameError: name 'terse_cells' is not
    defined`, after 25 payload jobs and ~9 hours of GPU time had already
    succeeded.

    The terseness functions (model-testing#104) were appended to the END of
    analyze.py — below `if __name__ == \"__main__\": main()`. Python binds
    names at call time, so main() ran before they existed.

    THE UNIT TESTS COULD NOT SEE IT. They import the module and call the
    functions directly; importing never fires the `__main__` guard, so every
    one of them passed against a file that could not run. Same shape as the
    n8n credential and the ANALYZE-CELLS gate earlier in this session: green
    tests, live path unrun.

    A static invariant is the cheap catch. Executing the script for real
    would need ollama stubbed at subprocess level; asserting that nothing is
    DEFINED after the entrypoint costs nothing and is impossible to get
    wrong by accident.
    """

    @staticmethod
    def _tree_and_guard():
        import ast
        src = (BIN / "analyze.py").read_text()
        tree = ast.parse(src)
        guard = None
        for node in tree.body:
            if isinstance(node, ast.If) and ast.dump(node.test).count("__name__"):
                guard = node
        return tree, guard

    def test_the_entrypoint_guard_exists(self):
        """Guard the guard: without it the assertion below passes vacuously
        and stops testing anything."""
        _, guard = self._tree_and_guard()
        self.assertIsNotNone(guard, "no `if __name__ == '__main__'` block")

    def test_no_function_or_class_is_defined_after_main_runs(self):
        import ast
        tree, guard = self._tree_and_guard()
        late = [n.name for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                and n.lineno > guard.lineno]
        self.assertEqual(
            late, [],
            f"defined after the entrypoint, so main() cannot see them: {late}")

    def test_every_name_main_calls_is_defined_before_the_guard(self):
        """The sharper form. A helper defined late is only fatal if main()
        actually calls it — but that is exactly the case that ships looking
        fine and dies on the first real run."""
        import ast
        tree, guard = self._tree_and_guard()
        defs = {n.name: n.lineno for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        main = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        called = {n.func.id for n in ast.walk(main)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        late = sorted(c for c in called
                      if c in defs and defs[c] > guard.lineno)
        self.assertEqual(late, [], f"main() calls these, defined too late: {late}")
