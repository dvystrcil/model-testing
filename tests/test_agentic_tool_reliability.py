#!/usr/bin/env python3
"""Hermetic tests for the synthetic tool backend in agentic-tool-reliability."""
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "agentic-tool-reliability.py"
spec = importlib.util.spec_from_file_location("agentic_tool_reliability", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class SyntheticToolsTest(unittest.TestCase):
    def test_search_returns_slug_and_name(self):
        result = mod.execute_tool("recipe_search", {"query": "lemon chicken"}, {})
        self.assertEqual(result["recipes"], [{"slug": "lemon-chicken-orzo", "name": "Lemon Chicken Orzo"}])

    def test_search_finds_sourdough_recipe(self):
        result = mod.execute_tool("recipe_search", {"query": "sourdough"}, {})
        self.assertEqual(result["recipes"][0]["slug"], "sourdough-country-loaf")

    def test_get_returns_grounding_facts(self):
        result = mod.execute_tool("recipe_get", {"slug": "sourdough-country-loaf"}, {"scenario": "result_grounding"})
        self.assertEqual(result["hydration"], "78%")

    def test_error_recovery_injects_one_transient_failure(self):
        state = {"scenario": "error_recovery"}
        first = mod.execute_tool("recipe_get", {"slug": "beef-stroganoff"}, state)
        second = mod.execute_tool("recipe_get", {"slug": "beef-stroganoff"}, state)
        self.assertIn("Temporary service unavailable", first["error"])
        self.assertEqual(second["name"], "Beef Stroganoff")

    def test_invalid_arguments_are_reported_as_tool_errors(self):
        self.assertIn("error", mod.execute_tool("recipe_get", {"slug": 42}, {}))
        self.assertIn("error", mod.execute_tool("recipe_search", {"query": ""}, {}))

    def test_unknown_tool_is_not_silently_accepted(self):
        self.assertIn("error", mod.execute_tool("made_up_tool", {}, {}))


if __name__ == "__main__":
    unittest.main()
