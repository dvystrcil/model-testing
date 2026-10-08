#!/usr/bin/env python3
"""Daily passive check of REAL tool-call health in Open WebUI (homelab#1205 AC5a-c).

Reads OWUI's admin chat export and counts every tool call's outcome over a
rolling window -- the same measurement homelab#1205 was built on, where
meal-time's tool calls went from 0 failures in 1,878 to failing, and nothing
noticed for four days. Uses no GPU: it reads what already happened.

  OWUI_API_KEY=... bin/toolcall-health.py --owui http://open-webui.open-webui.svc.cluster.local \\
      --md-out /tmp/body.md --json-out /tmp/health.json

Each failed call is classed by its error text:
  unknown_tool  the model named a tool that was never offered     (#1205 class 1)
  bad_args      argument validation failed                        (#1205 class 2)
  tool_error    the service behind the tool failed (400, no data) -- not the model

Only unknown_tool + bad_args count toward the alert: a Kroger outage is not
an ollama regression. The report carries counts, model and tool names only --
never chat content.

Last line is a marker the workflow asserts on:
  TOOLCALL-HEALTH-COMPLETE calls=N model_failures=K rate=R breached=yes|no
A run that cannot read the export prints TOOLCALL-HEALTH-CANNOT-LOOK and exits 2:
a check that saw nothing must not clear anything.
"""
import argparse, json, os, re, sys, time, urllib.request

MIN_CALLS = 10      # fewer calls than this in the window is not enough to call it
MAX_RATE = 0.10     # model-attributable failures above this rate breach
MODEL_CLASSES = ("unknown_tool", "bad_args")

_UNKNOWN = re.compile(r'Tool "[^"]+" not found')
_BAD_ARGS = re.compile(r"validation error|Input should be|missing \d+ required|required positional argument"
                       r"|HTTP error 422|\\?[\"']type\\?[\"']: ?\\?[\"']missing", re.I)


def classify_failure(text):
    if _UNKNOWN.search(text):
        return "unknown_tool"
    if _BAD_ARGS.search(text):
        return "bad_args"
    return "tool_error"


def _text(output):
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return " ".join(p.get("text", "") for p in output if isinstance(p, dict))
    return json.dumps(output)


def summarize(chats, since):
    models, calls = {}, 0
    for c in chats:
        for m in ((c.get("chat") or {}).get("history") or {}).get("messages", {}).values():
            outs = m.get("output") or []
            if not outs:
                continue
            ts = m.get("timestamp") or c.get("updated_at") or 0
            if ts < since:
                continue
            names = {o.get("call_id"): o.get("name") for o in outs if o.get("type") == "function_call"}
            for o in outs:
                if o.get("type") != "function_call_output" or o.get("status") not in ("completed", "failed"):
                    continue
                calls += 1
                row = models.setdefault(m.get("model") or "unknown", {"completed": 0, "failed": {}, "failed_tools": {}})
                if o["status"] == "completed":
                    row["completed"] += 1
                    continue
                cls = classify_failure(_text(o.get("output")))
                row["failed"][cls] = row["failed"].get(cls, 0) + 1
                tool = names.get(o.get("call_id")) or "?"
                row["failed_tools"][tool] = row["failed_tools"].get(tool, 0) + 1
    return {"calls": calls, "models": models}


def verdict(summary):
    model_failures = sum(r["failed"].get(k, 0) for r in summary["models"].values() for k in MODEL_CLASSES)
    calls = summary["calls"]
    rate = model_failures / calls if calls else 0.0
    return {"calls": calls, "model_failures": model_failures, "rate": rate,
            "breached": calls >= MIN_CALLS and rate > MAX_RATE}


def render(summary, v, days):
    lines = [f"Tool-call health over the last **{days} days** of real Open WebUI use "
             f"(`bin/toolcall-health.py`, homelab#1205 AC5).", "",
             f"**{v['model_failures']} model-attributable failures in {v['calls']} calls "
             f"({v['rate']:.1%})** -- alert threshold: >{MAX_RATE:.0%} with at least {MIN_CALLS} calls.", "",
             "| Model / preset | completed | unknown tool | bad args | tool-side error | failing tools |",
             "|---|---|---|---|---|---|"]
    for name, r in sorted(summary["models"].items(), key=lambda kv: -sum(kv[1]["failed"].values())):
        f = r["failed"]
        tools = ", ".join(f"`{t}` x{n}" for t, n in sorted(r["failed_tools"].items(), key=lambda kv: -kv[1])[:5])
        lines.append(f"| {name} | {r['completed']} | {f.get('unknown_tool', 0)} | {f.get('bad_args', 0)} "
                     f"| {f.get('tool_error', 0)} | {tools or '-'} |")
    lines += ["", "_unknown tool_ = #1205 class 1, _bad args_ = class 2. Tool-side errors (the service behind "
              "the tool failed) are listed but do not count toward the alert."]
    return "\n".join(lines)


def fetch(owui, key):
    req = urllib.request.Request(owui.rstrip("/") + "/api/v1/chats/all/db",
                                 headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.load(r)
    if not isinstance(data, list):
        raise ValueError(f"export is not a list: {type(data).__name__}")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owui", default="http://open-webui.open-webui.svc.cluster.local")
    ap.add_argument("--input", help="read a saved export instead of calling OWUI")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--md-out")
    ap.add_argument("--json-out")
    a = ap.parse_args()
    try:
        if a.input:
            chats = json.load(open(a.input))
        else:
            key = os.environ.get("OWUI_API_KEY")
            if not key:
                raise RuntimeError("OWUI_API_KEY is not set")
            chats = fetch(a.owui, key)
    except Exception as e:  # noqa: BLE001 -- any failure to look is the same verdict
        msg = f"Could not read the Open WebUI chat export: `{type(e).__name__}: {e}`"
        if a.md_out:
            open(a.md_out, "w").write(msg + "\n\nA check that saw nothing cannot clear anything.")
        print(f"TOOLCALL-HEALTH-CANNOT-LOOK error={type(e).__name__}")
        sys.exit(2)
    s = summarize(chats, since=time.time() - a.days * 86400)
    v = verdict(s)
    if a.md_out:
        open(a.md_out, "w").write(render(s, v, a.days))
    if a.json_out:
        json.dump({"summary": s, "verdict": v, "days": a.days}, open(a.json_out, "w"), indent=1)
    print(render(s, v, a.days))
    print(f"TOOLCALL-HEALTH-COMPLETE calls={v['calls']} model_failures={v['model_failures']} "
          f"rate={v['rate']:.3f} breached={'yes' if v['breached'] else 'no'}")


if __name__ == "__main__":
    main()
