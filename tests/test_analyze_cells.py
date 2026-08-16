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
