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


if __name__ == "__main__":
    unittest.main()
