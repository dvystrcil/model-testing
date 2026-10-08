#!/usr/bin/env python3
"""Pure-function tests for bin/toolcall-health.py (homelab#1205 AC5a-c).

No network. Record shapes are the real OWUI export shapes
(/api/v1/chats/all/db, 2026-10-08): a message's `output[]` holds a
`function_call` (call_id, name) and a `function_call_output` (call_id,
status, output=[{"type": "input_text", "text": ...}]). The failure texts are
the real ones seen in that export.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
_s = importlib.util.spec_from_file_location("tch", BIN / "toolcall-health.py")
tch = importlib.util.module_from_spec(_s)
sys.modules["tch"] = tch
_s.loader.exec_module(tch)

NOW = 1_800_000_000
DAY = 86400


def out(call_id, status, text):
    return {"type": "function_call_output", "call_id": call_id, "status": status,
            "output": [{"type": "input_text", "text": text}]}


def msg(model, ts, calls):
    """calls: [(name, status, text)]"""
    o = []
    for i, (name, status, text) in enumerate(calls):
        o.append({"type": "function_call", "call_id": f"c{i}", "name": name, "status": status})
        o.append(out(f"c{i}", status, text))
    return {"role": "assistant", "model": model, "timestamp": ts, "output": o}


def chat(*messages, updated_at=NOW):
    return {"updated_at": updated_at,
            "chat": {"history": {"messages": {f"m{i}": m for i, m in enumerate(messages)}}}}


class ClassifyFailureTest(unittest.TestCase):
    def test_unknown_tool(self):
        self.assertEqual(tch.classify_failure('Error: Tool "web_search" not found.'), "unknown_tool")

    def test_validation_errors_are_bad_args(self):
        for t in ["Error executing tool search: 1 validation error for searchArguments items Input should be a valid list",
                  "Tools.kubectl_get() missing 1 required positional argument: 'kind'",
                  'HTTP error 422: {"detail":[{"type":"missing","loc":["body","command"]}]}']:
            self.assertEqual(tch.classify_failure(t), "bad_args", t)

    def test_downstream_errors_are_tool_errors(self):
        # The model did its job; the service behind the tool failed.
        for t in ["400 Client Error: Bad Request for url: https://api.kroger.com/v1/x",
                  "No products found with ID 'beef stroganoff'",
                  '{"error": "Message not found"}']:
            self.assertEqual(tch.classify_failure(t), "tool_error", t)


class SummarizeTest(unittest.TestCase):
    def test_counts_by_model_and_class_within_window(self):
        chats = [chat(
            msg("meal-time", NOW - DAY, [("tinysearch-mcp_search", "completed", "ok"),
                                        ("web_search", "failed", 'Error: Tool "web_search" not found.')]),
            msg("meal-time", NOW - 10 * DAY, [("old", "failed", 'Error: Tool "old" not found.')]),  # outside 7d
        )]
        s = tch.summarize(chats, since=NOW - 7 * DAY)
        m = s["models"]["meal-time"]
        self.assertEqual(m["completed"], 1)
        self.assertEqual(m["failed"], {"unknown_tool": 1})
        self.assertEqual(m["failed_tools"], {"web_search": 1})
        self.assertEqual(s["calls"], 2)

    def test_in_progress_is_not_counted(self):
        s = tch.summarize([chat(msg("x", NOW, [("t", "in_progress", "")]))], since=NOW - DAY)
        self.assertEqual(s["calls"], 0)

    def test_message_without_timestamp_falls_back_to_chat_updated_at(self):
        m = msg("x", None, [("t", "completed", "ok")])
        del m["timestamp"]
        s = tch.summarize([chat(m, updated_at=NOW)], since=NOW - DAY)
        self.assertEqual(s["calls"], 1)

    def test_no_chat_content_in_summary(self):
        # Family chats: the report may carry counts, model and tool names only.
        secret = "PRIVATE-FAMILY-TEXT"
        c = chat(msg("meal-time", NOW, [("t", "failed", f"400 Client Error {secret}")]))
        c["chat"]["history"]["messages"]["u"] = {"role": "user", "content": secret, "timestamp": NOW}
        s = tch.summarize([c], since=NOW - DAY)
        md = tch.render(s, tch.verdict(s), days=7)
        self.assertNotIn(secret, repr(s))
        self.assertNotIn(secret, md)


class VerdictTest(unittest.TestCase):
    def s(self, completed, unknown=0, bad=0, tool_err=0):
        failed = {k: v for k, v in {"unknown_tool": unknown, "bad_args": bad, "tool_error": tool_err}.items() if v}
        calls = completed + unknown + bad + tool_err
        return {"calls": calls, "models": {"m": {"completed": completed, "failed": failed, "failed_tools": {}}}}

    def test_breach_needs_min_calls(self):
        # 3/4 failing (2026-09-17's real shape) is too few calls to call it.
        self.assertFalse(tch.verdict(self.s(1, unknown=3))["breached"])

    def test_model_failure_rate_over_threshold_breaches(self):
        # 2026-08-29..31: 17 failures in 118 calls, ~14%.
        self.assertTrue(tch.verdict(self.s(101, unknown=9, bad=8))["breached"])

    def test_tool_errors_do_not_breach(self):
        # A Kroger outage is not an ollama regression.
        self.assertFalse(tch.verdict(self.s(80, tool_err=20))["breached"])

    def test_under_threshold_is_healthy(self):
        self.assertFalse(tch.verdict(self.s(86, unknown=3))["breached"])  # 2026-09-19: ~3%

    def test_zero_calls_is_healthy_not_breached(self):
        v = tch.verdict({"calls": 0, "models": {}})
        self.assertFalse(v["breached"])


if __name__ == "__main__":
    unittest.main()
