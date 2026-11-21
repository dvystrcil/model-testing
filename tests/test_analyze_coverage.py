#!/usr/bin/env python3
"""Coverage accounting for a sweep report (model-testing, run 31905169831).

Why this exists
---------------
On 2026-08-15 the `family_citation_discipline` matrix job died in `Install
dependencies`, so its sweep never ran and it produced no JSONL. Every other
family was fine. The pipeline's response to that was:

  * "Copy results to PVC" is `if: always()` and ends in `|| echo "No result
    file found"`, so it exited 0 having copied nothing;
  * `analyze` fails only when it finds ZERO files, so 23-of-24 sailed
    through;
  * the report lists the payloads it analyzed and never mentions the one it
    did not.

The result is a report that looks complete, is missing a family, and says
nothing. Someone comparing two sweeps would read the gap as "that family
wasn't in this run" rather than "that family failed" — and a family that
silently vanishes on failure is worse than one that loudly fails, because
a *regression* in one family could hide behind the same silence.

So the rule under test: a report must always state what it was supposed to
cover, and the run must go red when it did not.

The fourth case is the one that matters most. When nobody passes an
expected set, coverage is UNKNOWN — and unknown must never render as
complete. That is the same collapse ("we could not look" == "there was
nothing to see") that produced this bug in the first place.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "benchmarks"
sys.path.insert(0, str(BIN))
_spec = importlib.util.spec_from_file_location("analyze", BIN / "analyze.py")
analyze = importlib.util.module_from_spec(_spec)
sys.modules["analyze"] = analyze
_spec.loader.exec_module(analyze)


class ParseExpectedTest(unittest.TestCase):
    """The expected set arrives from the GHA matrix, whose exact shape has
    bitten this repo before. Accept both a comma list and a JSON array so a
    quoting slip degrades to a parse error, never to a silent empty set —
    an empty expected set would report perfect coverage of nothing."""

    def test_comma_separated(self):
        self.assertEqual(analyze.parse_expected("a,b,c"), ["a", "b", "c"])

    def test_json_array(self):
        self.assertEqual(analyze.parse_expected('["a", "b"]'), ["a", "b"])

    def test_whitespace_and_empties_are_dropped(self):
        self.assertEqual(analyze.parse_expected(" a , ,b, "), ["a", "b"])

    def test_none_is_unknown_not_empty(self):
        self.assertIsNone(analyze.parse_expected(None))

    def test_a_blank_string_is_unknown_not_empty(self):
        """`--expect ""` is what an unset GHA expression expands to. It must
        mean 'nobody told us', not 'expected nothing'."""
        self.assertIsNone(analyze.parse_expected(""))


class CoverageTest(unittest.TestCase):

    def test_complete_run_has_no_missing(self):
        c = analyze.coverage(["a", "b"], ["b", "a"])
        self.assertEqual(c["missing"], [])
        self.assertEqual(c["unexpected"], [])
        self.assertTrue(c["complete"])
        self.assertTrue(c["known"])

    def test_a_failed_family_is_named(self):
        c = analyze.coverage(["a", "b", "c"], ["a", "c"])
        self.assertEqual(c["missing"], ["b"])
        self.assertFalse(c["complete"])

    def test_a_payload_nobody_asked_for_is_surfaced(self):
        """Results for a payload outside the matrix mean stale data leaked
        into the run directory. The numbers would be real and from another
        sweep — a silent apples-to-oranges comparison."""
        c = analyze.coverage(["a"], ["a", "ghost"])
        self.assertEqual(c["unexpected"], ["ghost"])
        self.assertFalse(c["complete"])

    def test_unknown_expectation_is_not_complete(self):
        c = analyze.coverage(None, ["a", "b"])
        self.assertFalse(c["known"])
        self.assertFalse(c["complete"],
                         "an unchecked run must never claim completeness")


class RenderTest(unittest.TestCase):
    """The section is the whole point: the report is what a human reads, and
    the failure has to be visible there, not only in a job log nobody
    opens."""

    def test_missing_family_appears_in_the_report_section(self):
        s = analyze.render_coverage(analyze.coverage(["a", "b"], ["a"]))
        self.assertIn("b", s)
        self.assertIn("INCOMPLETE", s.upper())

    def test_complete_run_says_so_with_counts(self):
        s = analyze.render_coverage(analyze.coverage(["a", "b"], ["a", "b"]))
        self.assertIn("2", s)
        self.assertNotIn("INCOMPLETE", s.upper())

    def test_unknown_says_unknown_rather_than_complete(self):
        s = analyze.render_coverage(analyze.coverage(None, ["a"]))
        self.assertIn("UNKNOWN", s.upper())


class MarkerTest(unittest.TestCase):
    """A machine-readable line so the workflow can turn the run red without
    re-deriving anything, and so 'the step ran' is distinguishable from 'the
    step was skipped' (feedback_ci_assert_completion_marker)."""

    def test_marker_reports_counts_and_names(self):
        m = analyze.coverage_marker(analyze.coverage(["a", "b", "c"], ["a"]))
        self.assertIn("ANALYZE-COVERAGE", m)
        self.assertIn("expected=3", m)
        self.assertIn("analyzed=1", m)
        self.assertIn("missing=b,c", m)

    def test_marker_says_none_when_complete(self):
        m = analyze.coverage_marker(analyze.coverage(["a"], ["a"]))
        self.assertIn("missing=none", m)

    def test_marker_distinguishes_unknown_from_complete(self):
        """`missing=none` on an unchecked run would be a lie the CI gate
        then propagates as a green tick."""
        m = analyze.coverage_marker(analyze.coverage(None, ["a"]))
        self.assertIn("expected=unknown", m)
        self.assertNotIn("missing=none", m)



class TimedOutPayloadTest(unittest.TestCase):
    """Run 44602629787 (model-testing#176): a single-model sweep of
    ripple-preview:65b-q4_K_M timed out at 722s on two surgical-edit
    payloads. Those jobs wrote no result row, so ANALYZE-COVERAGE read
    `expected=35 analyzed=33 missing=surgical_edit_kroger_pip_removal,
    surgical_edit_open_terminal_constraint` and failed the run -- the same
    "a timeout is a result" call #94 made for cells, lost one level up.

    Reproduced end to end through main(), with the summariser stubbed: the
    marker CI greps is the thing under test, not a re-derivation of it.
    """

    MODEL = "ripple-preview:65b-q4_K_M"
    DONE = "factual_recall"
    TIMED_OUT = ["surgical_edit_kroger_pip_removal",
                 "surgical_edit_open_terminal_constraint"]

    def _row(self, payload):
        return {"ts": "2026-11-19T21:54:30Z", "payload": payload,
                "model": self.MODEL, "runs": 1, "facts_score": 1.0,
                "eval_count": 100, "eval_duration": 10**10, "gen_tps": 5.7,
                "prompt_eval_duration": 10**8, "forbidden_violations": [],
                "schema_violations": None, "done_reasons": ["stop"],
                "answer_words": 80}

    def _timed_out(self, payload):
        return {"ts": "2026-11-19T21:54:30Z", "payload": payload,
                "model": self.MODEL, "runs": 1, "status": "timed_out",
                "timed_out_runs": 1, "elapsed_seconds": 722.1,
                "timeout_seconds": 722.0}

    def _run_main(self, rows_by_file):
        import contextlib, io, json, tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            files = []
            for i, rows in enumerate(rows_by_file):
                f = Path(d) / f"sweep_{i}.jsonl"
                meta = {"type": "metadata", "ts": "x", "ollama_version": "v",
                        "hostname": "h", "git_commit": "c"}
                f.write_text("\n".join(json.dumps(r) for r in [meta, *rows]) + "\n")
                files.append(str(f))
            out = Path(d) / "report.md"
            argv = ["analyze.py", *files, "--out", str(out),
                    "--expect", ",".join([self.DONE, *self.TIMED_OUT]),
                    "--expect-models", self.MODEL]
            err = io.StringIO()
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(analyze, "ollama_chat",
                                   return_value="No recommendation."), \
                 contextlib.redirect_stderr(err):
                analyze.main()
            return err.getvalue(), out.read_text()

    def _the_44602629787_shape(self):
        return self._run_main([[self._row(self.DONE)],
                               [self._timed_out(self.TIMED_OUT[0])],
                               [self._timed_out(self.TIMED_OUT[1])]])

    def test_a_timed_out_payload_does_not_fail_coverage(self):
        err, _ = self._the_44602629787_shape()
        marker = next(l for l in err.splitlines()
                      if l.startswith("ANALYZE-COVERAGE"))
        self.assertIn("analyzed=3", marker)
        self.assertIn("missing=none", marker)
        self.assertIn("unexpected=none", marker)

    def test_the_timeout_is_stated_not_swallowed(self):
        """Passing coverage must not hide that two cells have no numbers."""
        err, report = self._the_44602629787_shape()
        marker = next(l for l in err.splitlines()
                      if l.startswith("ANALYZE-TIMEOUTS"))
        self.assertIn(f"{self.MODEL}:2", marker)
        self.assertIn("## Timed Out", report)
        for p in self.TIMED_OUT:
            self.assertIn(p, report)
        self.assertIn("722", report)

    def test_a_timed_out_cell_is_not_counted_as_measured(self):
        """Payload coverage asks "did the job run"; cell coverage asks "did
        this model produce numbers". A timeout answers yes to the first and
        no to the second, so the CELLS warning still fires -- as a partial
        gap, not as a wholly absent model."""
        err, _ = self._the_44602629787_shape()
        cells = next(l for l in err.splitlines()
                     if l.startswith("ANALYZE-CELLS"))
        self.assertIn(f"missing_by_model={self.MODEL}:2", cells)
        self.assertIn("wholly_absent=none", cells)

    def test_a_model_that_timed_out_everywhere_is_still_wholly_absent(self):
        """The #82 guard stays fatal: a model with no numbers anywhere means
        the comparison never happened, however its rows are labelled."""
        err, _ = self._run_main([[self._timed_out(p)]
                                 for p in [self.DONE, *self.TIMED_OUT]])
        cells = next(l for l in err.splitlines()
                     if l.startswith("ANALYZE-CELLS"))
        self.assertIn(f"wholly_absent={self.MODEL}", cells)

    def test_timed_out_rows_stay_out_of_the_results_table(self):
        """load_results feeds the table, the safety gate and analyze_claude;
        a row with no facts_score would crash all three."""
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "s.jsonl"
            f.write_text(json.dumps(self._row(self.DONE)) + "\n"
                         + json.dumps(self._timed_out(self.TIMED_OUT[0])) + "\n")
            _, standard, _ = analyze.load_results(f)
            self.assertEqual([r["payload"] for r in standard], [self.DONE])
            self.assertEqual([r["payload"] for r in analyze.load_timed_out(f)],
                             [self.TIMED_OUT[0]])

    def test_timed_out_agentic_and_log_triage_cells_count_toward_coverage(self):
        """model-testing#180: the rows the agentic and log-triage RUNNERS
        write on timeout (not hand-built ones) reach coverage as covered and
        the report as timed out."""
        import run_benchmark as rb
        from unittest import mock
        agentic = {"name": self.TIMED_OUT[0], "type": "agentic", "task": "t",
                   "scoped_files": [], "expected_writes": {}, "max_turns": 2}
        triage = rb.load_payloads("log_triage_replay")[0]
        triage["name"] = self.TIMED_OUT[1]
        triage["cases"] = triage["cases"][:1]
        with mock.patch.object(rb, "ollama_chat",
                               side_effect=TimeoutError("timed out")):
            rows = [rb.run_one(s, "http://x", self.MODEL, 1, 0, False)
                    for s in (agentic, triage)]
        self.assertNotIn(None, rows)
        err, report = self._run_main([[self._row(self.DONE)], *[[r] for r in rows]])
        marker = next(l for l in err.splitlines()
                      if l.startswith("ANALYZE-COVERAGE"))
        self.assertIn("missing=none", marker)
        self.assertIn("## Timed Out", report)
        for p in self.TIMED_OUT:
            self.assertIn(p, report.split("## Timed Out", 1)[1])


if __name__ == "__main__":
    unittest.main()
