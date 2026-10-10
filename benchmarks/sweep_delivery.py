#!/usr/bin/env python3
"""Did the sweep's analysis reach a person? model-testing#77.

The analyze job POSTs the reports to n8n's model-sweep-complete webhook, which
comments on the sweep's tracking issue. Run 31529368759 got back

    {"status":"completed_orphan", ..., "issue_commented":false,
     "note":"no tracking issue found; fetch report manually via GHA artifact"}

and the step ignored it: green, delivered nowhere. This classifies the reply
so the workflow can put the outcome on the run page and, when the analysis
reached nobody, open an issue that carries it.

    sweep_delivery.py classify --rc N --response FILE
        prints `SWEEP-DELIVERY outcome=<delivered|orphan|webhook_failed>`,
        appends a "## Delivery" section to $GITHUB_STEP_SUMMARY; exit 0
    sweep_delivery.py body --run-id ID --run-url URL --outcome O
                           --report F --report-claude F
        prints an issue body holding both reports, within GitHub's limit

Only an explicit `issue_commented: true` counts as delivered: no evidence of
delivery is not delivery.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MARK = "SWEEP-DELIVERY"
GITHUB_BODY_LIMIT = 65536
_ROOM = 2000  # header/footer headroom inside the limit


def _reply(text: str) -> dict | None:
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def classify(curl_rc: int, response_text: str) -> str:
    if curl_rc != 0:
        return "webhook_failed"
    d = _reply(response_text)
    if d is None or "error" in d or "status" not in d:
        return "webhook_failed"     # incl. a 400 reply, which curl -sS exits 0 on
    return "delivered" if d.get("issue_commented") is True else "orphan"


def summary(outcome: str, response_text: str) -> str:
    d = _reply(response_text) or {}
    lines = ["## Delivery", ""]
    if outcome == "delivered":
        lines.append("Delivered: the analysis was commented on the sweep's tracking issue.")
    elif outcome == "orphan":
        lines += ["**NOT delivered** to a tracking issue "
                  f"(n8n: `{d.get('status')}`, `issue_commented: {str(d.get('issue_commented')).lower()}`"
                  + (f" -- {d['note']}" if d.get("note") else "") + ").",
                  "", "An issue carrying the analysis is opened by the next step."]
    else:
        lines += ["**NOT delivered**: the sweep-complete webhook failed or gave no usable reply"
                  + (f" (`{response_text.strip()[:200]}`)" if response_text.strip() else "") + ".",
                  "", "An issue carrying the analysis is opened by the next step."]
    return "\n".join(lines) + "\n"


def _read(path: Path, what: str) -> str:
    try:
        return path.read_text()
    except OSError:
        return f"_(no report: {what} was not produced)_\n"


def issue_body(run_id: str, run_url: str, outcome: str, report: Path, report_claude: Path) -> str:
    head = (f"The analysis of sweep [{run_id}]({run_url}) did not reach a tracking issue "
            f"(`{outcome}`), so it is delivered here instead (model-testing#77).\n\n")
    parts = [("Local analysis (report.md)", _read(report, "report.md")),
             ("Claude analysis (report-claude.md)", _read(report_claude, "report-claude.md"))]
    budget = (GITHUB_BODY_LIMIT - _ROOM - len(head)) // len(parts)
    out = [head]
    for title, text in parts:
        if len(text) > budget:
            text = text[:budget] + (f"\n\n_(truncated at {budget} characters; the full report is in "
                                    f"the run's `report-{run_id}` artifact)_\n")
        out.append(f"## {title}\n\n{text}\n")
    return "".join(out)[:GITHUB_BODY_LIMIT]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--rc", type=int, required=True)
    c.add_argument("--response", type=Path, required=True)
    b = sub.add_parser("body")
    for a in ("--run-id", "--run-url", "--outcome"):
        b.add_argument(a, required=True)
    b.add_argument("--report", type=Path, required=True)
    b.add_argument("--report-claude", type=Path, required=True)
    a = p.parse_args(argv)
    if a.cmd == "classify":
        text = a.response.read_text() if a.response.exists() else ""
        outcome = classify(a.rc, text)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write(summary(outcome, text))
        print(f"{MARK} outcome={outcome}")
        return 0
    sys.stdout.write(issue_body(a.run_id, a.run_url, a.outcome, a.report, a.report_claude))
    return 0


if __name__ == "__main__":
    sys.exit(main())
