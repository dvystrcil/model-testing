#!/usr/bin/env python3
"""Pure-function tests for benchmarks/owui-tool-search/run.py (homelab#1871 AC2).

No network, no cluster. The bench's verdicts come from two things: which tool
a turn really called (call_tool hides the target inside its arguments when
Tool Search is on) and which prod config rows are safe to copy into the bench.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

RUN = Path(__file__).resolve().parent.parent / "benchmarks/owui-tool-search/run.py"
_s = importlib.util.spec_from_file_location("ots", RUN)
ots = importlib.util.module_from_spec(_s)
sys.modules["ots"] = ots
_s.loader.exec_module(ots)


def call(name, args=None):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args or {})}


class CalledToolsTest(unittest.TestCase):
    def test_call_tool_is_unwrapped_to_its_target(self):
        out = [call("search_tools", {"query": "recipes"}),
               {"type": "function_call_output", "output": []},
               call("call_tool", {"tool_name": "mealie-mcp_get_recipes", "params": "{}"})]
        self.assertEqual(ots.called_tools(out), ["search_tools", "mealie-mcp_get_recipes"])

    def test_malformed_call_tool_arguments_stay_call_tool(self):
        out = [{"type": "function_call", "name": "call_tool", "arguments": "{not json"}]
        self.assertEqual(ots.called_tools(out), ["call_tool"])

    def test_non_list_output_is_no_calls(self):
        self.assertEqual(ots.called_tools(None), [])
        self.assertEqual(ots.called_tools("text"), [])

    def test_only_function_call_blocks_count(self):
        out = [{"type": "reasoning"}, {"type": "message"}, call("rpg-dice-mcp_roll")]
        self.assertEqual(ots.called_tools(out), ["rpg-dice-mcp_roll"])


class RepairNameTest(unittest.TestCase):
    TOOLS = {"wiki-mcp_wiki_search", "wiki-mcp_wiki_lookup", "mealie-mcp_get_recipes",
             "kroger-mcp_get_current_datetime", "tinysearch-mcp_get_current_datetime"}

    def test_dropped_server_prefix_is_repaired(self):
        self.assertEqual(ots.repair_name("wiki_search", self.TOOLS), "wiki-mcp_wiki_search")

    def test_ambiguous_suffix_is_not_repaired(self):
        self.assertEqual(ots.repair_name("get_current_datetime", self.TOOLS), "get_current_datetime")

    def test_a_made_up_name_stays_wrong(self):
        self.assertEqual(ots.repair_name("mealie-mcp_search_recipes", self.TOOLS), "mealie-mcp_search_recipes")

    def test_exact_name_and_builtins_pass_through(self):
        self.assertEqual(ots.repair_name("mealie-mcp_get_recipes", self.TOOLS), "mealie-mcp_get_recipes")
        self.assertEqual(ots.repair_name("view_skill", self.TOOLS), "view_skill")

    def test_called_tools_applies_the_repair(self):
        out = [call("wiki_search", {"query": "Rivendell"})]
        self.assertEqual(ots.called_tools(out, self.TOOLS), ["wiki-mcp_wiki_search"])

    def test_preset_tools_uses_the_lock_file_names(self):
        tools = ots.preset_tools("the-one-ring")
        self.assertIn("rpg-dice-mcp_roll", tools)
        self.assertIn("wiki-mcp_wiki_search", tools)
        self.assertNotIn("mealie-mcp_get_recipes", tools)


class ScoreTest(unittest.TestCase):
    def test_meta_tools_are_skipped_when_finding_the_first_domain_tool(self):
        s = ots.score(["search_tools", "view_skill", "kroger-mcp_view_current_cart"],
                      ["kroger-mcp_view_current_cart"])
        self.assertTrue(s["ok"])
        self.assertTrue(s["used_search_tools"])
        self.assertEqual(s["n_tool_calls"], 3)

    def test_a_wrong_first_domain_tool_fails_even_if_the_right_one_follows(self):
        s = ots.score(["mealie-mcp_get_tags", "mealie-mcp_get_recipes"], ["mealie-mcp_get_recipes"])
        self.assertFalse(s["ok"])
        self.assertEqual(s["first_domain_tool"], "mealie-mcp_get_tags")

    def test_no_tool_case_passes_only_without_a_domain_call(self):
        self.assertTrue(ots.score(["view_skill"], [])["ok"])
        self.assertFalse(ots.score(["tinysearch-mcp_search"], [])["ok"])

    def test_no_call_on_a_tool_case_fails(self):
        self.assertFalse(ots.score([], ["rpg-dice-mcp_roll"])["ok"])


class KeepConfigKeyTest(unittest.TestCase):
    def test_credentials_and_connections_are_never_copied(self):
        for k in ["openai.api_keys", "web.search.brave_search_api_key", "oauth.client_secret",
                  "tool_server.connections", "terminal_server.connections", "ollama.base_urls",
                  "code_interpreter.jupyter.auth_password", "ldap.server.app_password"]:
            self.assertFalse(ots.keep_config_key(k), k)

    def test_behaviour_settings_are_copied(self):
        for k in ["subagents.enable", "web.search.enable", "user.permissions", "memories.enable",
                  "chat.context_compaction.token_threshold"]:
            self.assertTrue(ots.keep_config_key(k), k)


class SummarizeTest(unittest.TestCase):
    def test_errors_are_excluded_from_accuracy_and_tokens(self):
        recs = [{"arm": "baseline", "preset": "p", "ok": True, "input_tokens": 100},
                {"arm": "baseline", "preset": "p", "ok": False, "input_tokens": 300},
                {"arm": "baseline", "preset": "p", "ok": False, "error": "timeout"}]
        self.assertIn("| baseline | p | 1/2 | 1 | - | 200 |", ots.summarize(recs))

    def test_overhead_cases_feed_only_the_overhead_column(self):
        recs = [{"arm": "lazy", "preset": "p", "ok": True, "overhead": True, "input_tokens": 9000},
                {"arm": "lazy", "preset": "p", "ok": False, "input_tokens": 500}]
        self.assertIn("| lazy | p | 0/1 | 0 | 9000 | 500 |", ots.summarize(recs))


if __name__ == "__main__":
    unittest.main()
