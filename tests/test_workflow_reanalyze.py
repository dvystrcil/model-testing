#!/usr/bin/env python3
"""The analyze job must be runnable against an EXISTING sweep's results.

Runs 33293895544 and 33472730762 both failed at the analyze step. Their raw
data survived intact on the PVC — 236 and 246 result rows — but the Claude
summary step runs *after* the assert that failed, so it was skipped in both.
Two consecutive sweeps, ~22h of GPU time, and the A3 paired-data row of
homelab#292 is still empty.

`gh run rerun` cannot recover it: a re-run pins to the original commit, so it
re-runs the same broken gate. Without a way to point the analyze job at an
existing results directory, the only route to that data is another 13-hour
sweep — which is why it has not been recovered.

These assertions are made against the SHIPPED workflow file. actionlint
proves it parses; nothing else proves it is wired correctly, and a
re-analyze that silently reads the wrong directory would report a confident
verdict about the wrong sweep.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

WF_PATH = Path(__file__).resolve().parent.parent / ".github/workflows/model-sweep.yaml"
WF_TEXT = WF_PATH.read_text()
WF = yaml.safe_load(WF_TEXT)

INPUT = "reanalyze_run_id"


def analyze_steps() -> list[dict]:
    return WF["jobs"]["analyze"]["steps"]


def step_named(fragment: str) -> dict:
    for s in analyze_steps():
        if fragment.lower() in (s.get("name") or "").lower():
            return s
    raise AssertionError(f"no analyze step matching {fragment!r}")


class ReanalyzeInputTest(unittest.TestCase):

    def test_the_input_exists_and_defaults_to_a_normal_sweep(self):
        """Defaulting to empty is what keeps this invisible to every
        ordinary dispatch."""
        inputs = WF[True]["workflow_dispatch"]["inputs"]
        self.assertIn(INPUT, inputs)
        self.assertEqual(inputs[INPUT].get("default", ""), "")
        self.assertFalse(inputs[INPUT].get("required", False))

    def test_the_sweep_job_is_skipped_when_reanalyzing(self):
        """The whole point: 13 hours of GPU time must not run again to
        re-read data already on disk."""
        self.assertIn(INPUT, str(WF["jobs"]["sweep"].get("if", "")))

    def test_analyze_still_runs_when_the_sweep_is_skipped(self):
        """`needs: [setup, sweep]` would otherwise skip analyze along with
        it, which turns the whole feature into a no-op run."""
        self.assertIn("always()", str(WF["jobs"]["analyze"].get("if", "")))


class ItMustReadTheRightDirectoryTest(unittest.TestCase):
    """A re-analyze that reads the wrong run reports a confident verdict
    about data nobody asked about."""

    def test_a_single_resolved_run_id_is_defined_once(self):
        env = WF["jobs"]["analyze"].get("env") or {}
        self.assertIn("SWEEP_RUN_ID", env,
                      "resolve the id once at job level, not per step")
        self.assertIn(INPUT, env["SWEEP_RUN_ID"])
        self.assertIn("github.run_id", env["SWEEP_RUN_ID"])

    def test_no_analyze_step_still_builds_a_path_from_github_run_id(self):
        """The failure mode this guards is a half-applied change: one step
        reads the reused run and another writes to the new one, so the
        report lands where nobody looks."""
        offenders = []
        for s in analyze_steps():
            for line in (s.get("run") or "").splitlines():
                # Specifically a RUN DIRECTORY built from this run's id.
                # `github.run_id` inside a re-analysis FILENAME is correct --
                # that file really is this run's output.
                if "run-${{ github.run_id }}" in line:
                    offenders.append((s.get("name"), line.strip()))
        self.assertEqual(offenders, [], f"dirs still pinned to this run: {offenders}")

    def test_the_report_header_names_the_sweep_the_data_came_from(self):
        """n8n greps `sweep_id:` to decide which sweep a report belongs to.
        Stamping the re-analysis run id there files the results under a
        sweep that never produced them."""
        body = step_named("Analyze results (local")["run"]
        m = re.search(r"sweep_id:\s+\S+", body)
        self.assertIsNotNone(m, "no sweep_id header found")
        self.assertIn("SWEEP_RUN_ID", m.group(0))
        self.assertNotIn("github.run_id", m.group(0))


class HistoryMustNotBeOverwrittenTest(unittest.TestCase):

    def test_a_reanalysis_does_not_clobber_the_original_report(self):
        """`report.md` on the PVC is what that sweep reported AT THE TIME.
        A re-analysis runs newer code over the same data and can legitimately
        reach a different verdict; overwriting would erase the record of what
        was actually acted on."""
        body = step_named("Analyze results (local")["run"]
        self.assertIn("REANALYZE", body,
                      "the write-back must branch on re-analyze mode")

    def test_the_claude_report_IS_written_to_the_original_run(self):
        """The one file that must land in the old directory — it is the
        thing being recovered, and it never existed there."""
        body = step_named("Analyze results (Claude")["run"]
        self.assertIn("report-claude.md", body)
        self.assertIn("SWEEP_RUN_ID", body)


class TheCoverageGateMustStayHonestTest(unittest.TestCase):
    """A re-analysis genuinely cannot reconstruct what was EXPECTED.

    models.yaml has already changed since both failed runs — cogito:32b was
    dropped — so validating a historical dataset against today's roster would
    report a model as `unexpected` for the crime of having been removed
    afterwards.

    Deriving the expected set from the data instead would be worse: it makes
    coverage tautologically complete and unable to detect absence, which is
    the exact defect ANALYZE-CELLS exists to catch.

    So a re-analysis reports coverage UNKNOWN and says so. The original run
    already published its coverage verdict; this mode is for recovering the
    Claude summary, not for re-litigating completeness.
    """

    def test_unknown_coverage_is_still_fatal_for_a_normal_sweep(self):
        body = step_named("Assert every requested payload")["run"]
        self.assertIn("expected=unknown", body)
        # The unknown-coverage branch must still contain a hard failure for
        # the non-reanalyze path. Tolerating it unconditionally would make a
        # normal sweep able to report nothing and pass.
        branch = body.split("expected=unknown", 1)[1].split(";;", 1)[0]
        self.assertIn("exit 1", branch)
        self.assertIn("REANALYZE", branch)

    def test_but_a_reanalysis_is_allowed_to_say_it_does_not_know(self):
        body = step_named("Assert every requested payload")["run"]
        self.assertIn("REANALYZE", body,
                      "the unknown-coverage branch must know which mode it "
                      "is in, or re-analysis can never pass")
