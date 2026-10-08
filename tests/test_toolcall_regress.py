#!/usr/bin/env python3
"""Pure-function tests for bin/toolcall-regress.py (homelab#1205).

No network. The probe's value is its classifier: each verdict maps to one of
the failure classes #1205 recorded, and a wrong mapping would make a version
look clean (or broken) when it is not. The shapes below are the real ones:
the string-list `items` is what qwen3.6:35b sent on ollama 0.32.15 under a
~28k-token prompt, and the decoy call is what gemma4 did once.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
_s = importlib.util.spec_from_file_location("tcr", BIN / "toolcall-regress.py")
tcr = importlib.util.module_from_spec(_s)
sys.modules["tcr"] = tcr
_s.loader.exec_module(tcr)


def call(name, args):
    return {"message": {"tool_calls": [{"function": {"name": name, "arguments": args}}]}}


class ClassifyTest(unittest.TestCase):
    def test_valid_call_is_ok(self):
        r = call("tinysearch-mcp_search", {"items": [{"query": "beef stroganoff"}]})
        self.assertEqual(tcr.classify(200, r, "tinysearch-mcp_search", None)[0], "ok")

    def test_items_as_string_list_is_bad_args(self):
        # Seen live on 0.32.15: class 2, argument-type violation.
        r = call("tinysearch-mcp_search", {"items": ["beef stroganoff recipe"]})
        self.assertEqual(tcr.classify(200, r, "tinysearch-mcp_search", None)[0], "bad_args")

    def test_items_as_stringified_json_is_bad_args(self):
        # The #1205 production shape: the list arrived as a JSON string.
        r = call("tinysearch-mcp_search", {"items": '[{"query": "x"}]'})
        self.assertEqual(tcr.classify(200, r, "tinysearch-mcp_search", None)[0], "bad_args")

    def test_wrong_parameter_name_is_bad_args(self):
        r = call("mealie-mcp_get_recipe", {"id": "beef-stroganoff"})
        self.assertEqual(tcr.classify(200, r, "mealie-mcp_get_recipe", None)[0], "bad_args")

    def test_never_offered_tool_is_unknown(self):
        # Class 1: e.g. `web_search` when only tinysearch is offered.
        r = call("web_search", {"query": "x"})
        self.assertEqual(tcr.classify(200, r, "tinysearch-mcp_search", None)[0], "unknown_tool")

    def test_decoy_call_is_wrong_tool_not_unknown(self):
        # Decoys ARE offered in heavy mode; calling one is a choice error, not
        # a hallucinated name. The first live run mislabelled this.
        decoy = tcr.DECOYS[0]["function"]["name"]
        r = call(decoy, {"id": "1"})
        self.assertEqual(tcr.classify(200, r, "mealie-mcp_search_recipes", None)[0], "wrong_tool")

    def test_http_500_is_http_error(self):
        # Class 3: the qwen3.5 parser returning 500 (ollama#16383).
        self.assertEqual(tcr.classify(500, {"error": "XML syntax error"}, "x", None)[0], "http_error")

    def test_prose_answer_is_no_call(self):
        r = {"message": {"content": "Here are some recipes..."}}
        self.assertEqual(tcr.classify(200, r, "tinysearch-mcp_search", None)[0], "no_call")

    def test_failed_extra_check_is_bad_args(self):
        r = call("mealie-mcp_get_recipe", {"slug": "chicken-tikka"})
        check = lambda a: a["slug"] == "lemon-chicken-orzo"
        self.assertEqual(tcr.classify(200, r, "mealie-mcp_get_recipe", check)[0], "bad_args")


class FixtureTest(unittest.TestCase):
    def test_heavy_prompt_is_meal_time_sized(self):
        # #1205's failures were at 28-45k-token prompts; a short prompt passes
        # on every version and cannot discriminate.
        self.assertGreater(len(tcr.PAD) // 4, 20000)

    def test_cases_are_stable(self):
        # Two runs are only comparable if the case set is frozen.
        self.assertEqual([c[0] for c in tcr.CASES],
                         ["web_single", "web_two_items", "mealie_search", "mealie_get", "after_tool_result"])


if __name__ == "__main__":
    unittest.main()
