#!/usr/bin/env python3
"""Keep ONE rolling GitHub issue per lane: open it on a breach, keep its body
current while the breach lasts, and close it when the lane is healthy again.

A lane that only ever opens issues turns perfect detection into noise, so a
healthy result closes what is open. A red scheduled GitHub Actions run
notifies nobody in this homelab -- the issue IS the alert.

  GH_TOKEN=... bin/rolling-issue.py --repo dvystrcil/homelab --lane toolcall-health \\
      --title "Tool-call health: ..." --body-file body.md --breached yes|no

The lane is a label (created if missing) next to `automated`. REST only:
model-testing-runner has no gh CLI.
"""
import argparse, json, os, sys, urllib.error, urllib.request

API = "https://api.github.com"


def decide(open_numbers, breached):
    """-> (action, issue number). Several open: act on the oldest."""
    target = min(open_numbers) if open_numbers else None
    if breached:
        return ("update", target) if target else ("create", None)
    return ("close", target) if target else ("none", None)


def _call(method, path, token, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as r:  # raises on 4xx/5xx
        return json.load(r) if r.status != 204 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--lane", required=True, help="label naming this lane")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--breached", choices=["yes", "no"], required=True)
    ap.add_argument("--run-url", default=os.environ.get("RUN_URL", ""))
    a = ap.parse_args()
    token = os.environ["GH_TOKEN"]
    body = open(a.body_file).read()
    if a.run_url:
        body += f"\n\n_Updated by [this run]({a.run_url})._"

    try:
        _call("GET", f"/repos/{a.repo}/labels/{a.lane}", token)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        _call("POST", f"/repos/{a.repo}/labels", token, {"name": a.lane, "color": "c5def5"})

    issues = _call("GET", f"/repos/{a.repo}/issues?state=open&labels={a.lane}&per_page=100", token)
    numbers = [i["number"] for i in issues if "pull_request" not in i]
    action, n = decide(numbers, a.breached == "yes")
    if action == "create":
        r = _call("POST", f"/repos/{a.repo}/issues", token,
                  {"title": a.title, "body": body, "labels": ["automated", a.lane]})
        n = r["number"]
    elif action == "update":
        _call("PATCH", f"/repos/{a.repo}/issues/{n}", token, {"title": a.title, "body": body})
    elif action == "close":
        _call("POST", f"/repos/{a.repo}/issues/{n}/comments", token, {"body": "**Recovered.**\n\n" + body})
        _call("PATCH", f"/repos/{a.repo}/issues/{n}", token, {"state": "closed", "state_reason": "completed"})
    print(f"ROLLING-ISSUE lane={a.lane} action={action} issue={n or '-'}")


if __name__ == "__main__":
    main()
