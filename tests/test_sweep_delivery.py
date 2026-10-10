#!/usr/bin/env python3
"""A sweep's analysis must reach a person, or the run must say it did not.

model-testing#77. Run 31529368759 was green, and n8n answered

    {"status":"completed_orphan","sweep_id":"31529368759","issue_commented":false,
     "note":"no tracking issue found; fetch report manually via GHA artifact"}

The notify step never read that reply, so a delivered sweep and an undelivered
one were both a green check. Now the reply is classified, the outcome is on
the run page (job summary), an undelivered analysis opens an issue so it
still reaches a human, and if THAT fails the run fails.

Tests run the shipped benchmarks/sweep_delivery.py and the shipped workflow.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))
import sweep_delivery as sd  # noqa: E402

WF = yaml.safe_load((ROOT / ".github/workflows/model-sweep.yaml").read_text())

# The receiver's three replies, verbatim from n8n-workflow
# workflows/model_sweep_complete.json (Respond 200 completed / orphan / 400).
COMPLETED = json.dumps({"status": "completed", "sweep_id": "1", "issue_commented": True,
                        "discord_pinged": True})
BAD_INPUT = json.dumps({"error": "run_id is required"})
ORPHAN_31529368759 = json.dumps({
    "status": "completed_orphan", "sweep_id": "31529368759", "issue_commented": False,
    "note": "no tracking issue found; fetch report manually via GHA artifact"})


class TestClassify(unittest.TestCase):
    def test_the_real_orphan_reply_is_not_delivered(self):
        self.assertEqual(sd.classify(0, ORPHAN_31529368759), "orphan")

    def test_a_commented_tracking_issue_is_delivered(self):
        self.assertEqual(sd.classify(0, COMPLETED), "delivered")

    def test_a_400_reply_is_webhook_failed_although_curl_exits_0(self):
        """curl -sS returns 0 on HTTP 400; the body is the only evidence."""
        self.assertEqual(sd.classify(0, BAD_INPUT), "webhook_failed")

    def test_issue_commented_false_is_an_orphan_whatever_the_status_says(self):
        self.assertEqual(sd.classify(0, json.dumps({"status": "completed", "issue_commented": False})), "orphan")

    def test_a_missing_issue_commented_is_not_delivered(self):
        """No evidence of delivery is not delivery."""
        self.assertEqual(sd.classify(0, json.dumps({"status": "completed"})), "orphan")

    def test_curl_failure_is_webhook_failed(self):
        self.assertEqual(sd.classify(7, ""), "webhook_failed")

    def test_a_non_json_reply_is_webhook_failed(self):
        self.assertEqual(sd.classify(0, "<html>502 Bad Gateway</html>"), "webhook_failed")


class TestSummary(unittest.TestCase):
    def test_the_summary_states_the_outcome_and_the_reason(self):
        md = sd.summary("orphan", ORPHAN_31529368759)
        self.assertIn("## Delivery", md)
        self.assertIn("NOT delivered", md)
        self.assertIn("no tracking issue found", md)
        self.assertIn("issue_commented", md)

    def test_delivered_says_where(self):
        md = sd.summary("delivered", COMPLETED)
        self.assertIn("tracking issue", md)
        self.assertNotIn("NOT delivered", md)


class TestBody(unittest.TestCase):
    def test_body_carries_both_reports_and_fits_githubs_limit(self):
        d = Path(tempfile.mkdtemp())
        (d / "r.md").write_text("LOCAL " + "x" * 50000)
        (d / "c.md").write_text("CLAUDE " + "y" * 50000)
        body = sd.issue_body("31529368759", "https://run", "orphan", d / "r.md", d / "c.md")
        self.assertLessEqual(len(body), sd.GITHUB_BODY_LIMIT)
        self.assertIn("LOCAL", body)
        self.assertIn("CLAUDE", body)
        self.assertIn("truncated", body)
        self.assertIn("https://run", body)

    def test_missing_reports_still_make_a_body(self):
        body = sd.issue_body("1", "https://run", "webhook_failed", Path("/nope/a"), Path("/nope/b"))
        self.assertIn("no report", body.lower())


class TestCli(unittest.TestCase):
    def test_cli_prints_the_marker_and_writes_the_summary(self):
        d = Path(tempfile.mkdtemp())
        (d / "resp.json").write_text(ORPHAN_31529368759)
        summ = d / "summary.md"
        r = subprocess.run([sys.executable, str(ROOT / "benchmarks/sweep_delivery.py"), "classify",
                            "--rc", "0", "--response", str(d / "resp.json")],
                           env={"GITHUB_STEP_SUMMARY": str(summ), "PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SWEEP-DELIVERY outcome=orphan", r.stdout)
        self.assertIn("NOT delivered", summ.read_text())


def steps():
    return WF["jobs"]["analyze"]["steps"]


def idx(fragment):
    for i, s in enumerate(steps()):
        if fragment in (s.get("name") or ""):
            return i, s
    raise AssertionError(f"no step named *{fragment}*")


class TestWiring(unittest.TestCase):
    def test_notify_classifies_the_reply_and_exports_the_outcome(self):
        _, s = idx("Notify n8n sweep-complete webhook")
        self.assertEqual(s.get("id"), "notify")
        self.assertIn("sweep_delivery.py classify", s["run"])
        self.assertIn("GITHUB_OUTPUT", s["run"])

    def test_an_undelivered_sweep_opens_an_issue_after_notify(self):
        n, _ = idx("Notify n8n sweep-complete webhook")
        t, tok = idx("App token for undelivered-sweep issue")
        f, fb = idx("Deliver an undelivered sweep as an issue")
        self.assertLess(n, t)
        self.assertLess(t, f)
        for s in (tok, fb):
            self.assertIn("steps.notify.outputs.outcome != 'delivered'", s.get("if", ""))
            self.assertIn("always()", s.get("if", ""))
        self.assertEqual(tok["with"].get("permission-issues"), "write")
        self.assertIn("gh issue create", fb["run"])
        self.assertIn("sweep_delivery.py body", fb["run"])

    def test_if_the_fallback_cannot_deliver_the_run_fails(self):
        """AC1: never green when the analysis reached nobody."""
        _, fb = idx("Deliver an undelivered sweep as an issue")
        self.assertNotIn("continue-on-error", fb)
        self.assertRegex(fb["run"], r"set -e|\|\| *\{[^}]*exit 1|exit 1")


if __name__ == "__main__":
    unittest.main()
