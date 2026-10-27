#!/usr/bin/env python3
"""The analysis prompt must describe the payloads that actually ran (run 40533339607).

That sweep ran only the seven fiction_* payloads (#135). build_prompt took
payload descriptions from a dict hard-coded in analyze.py that covered 16 of
the 32 payload files and none of the fiction ones, so the prompt's
"## Payload descriptions" section was EMPTY. It also opened with "candidates
for an AI coding assistant role in a Kubernetes/homelab context", so the
summariser ranked fiction-writing results by "code-suggestion latency" and
recommended a model for "strict config schemas".

Every payload file already carries a `description`. These tests hold the
prompt to the files, and to an optional per-payload `role`.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "benchmarks"
_spec = importlib.util.spec_from_file_location("analyze", BIN / "analyze.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["analyze"] = mod
_spec.loader.exec_module(mod)

PAYLOADS = {p.stem: json.loads(p.read_text()) for p in (BIN / "payloads").glob("*.json")}
FICTION = sorted(n for n in PAYLOADS if n.startswith("fiction_"))


def rows(payloads, model="m:1"):
    return [{"model": model, "payload": p, "facts_score": 1.0} for p in payloads]


def guide(prompt):
    return prompt.split("## Payload descriptions", 1)[1].split("## Scoring", 1)[0]


class PayloadDescriptionsTest(unittest.TestCase):
    def test_the_40533339607_run_gets_a_non_empty_guide(self):
        g = guide(mod.build_prompt("", rows(FICTION), None))
        self.assertTrue(g.strip(), "payload guide is empty")
        for name in FICTION:
            self.assertIn(f"**{name}**", g)

    def test_every_payload_file_is_described_from_its_own_description(self):
        """A dict maintained by hand next to 32 files is the absence a gate
        cannot see: a new payload is simply never added to it."""
        g = guide(mod.build_prompt("", rows(sorted(PAYLOADS)), None))
        for name, spec in PAYLOADS.items():
            self.assertIn(f"**{name}**", g)
            self.assertIn(spec["description"][:60], g, name)

    def test_a_payload_with_no_file_is_flagged_not_dropped(self):
        g = guide(mod.build_prompt("", rows(["no_such_payload"]), None))
        self.assertIn("**no_such_payload**", g)
        self.assertIn("no description", g.lower())

    def test_only_payloads_that_ran_are_described(self):
        g = guide(mod.build_prompt("", rows(["factual_recall"]), None))
        self.assertNotIn("fiction_", g)

    def test_any_of_scoring_is_still_marked(self):
        # From the files, not from _any_of_payloads(): asking the code under
        # test which payload to check let an inverted check pass (mutation).
        any_of = sorted(n for n, s in PAYLOADS.items() if s.get("facts_mode") == "any")
        all_of = sorted(n for n, s in PAYLOADS.items() if s.get("facts_mode") != "any")
        self.assertTrue(any_of, "fixture: expected at least one any-of payload")
        g = guide(mod.build_prompt("", rows([any_of[0], all_of[0]]), None))
        line = {l.split("**")[1]: l for l in g.splitlines() if l.startswith("- **")}
        self.assertIn("any-of scoring", line[any_of[0]])
        self.assertNotIn("any-of scoring", line[all_of[0]])


class RoleFramingTest(unittest.TestCase):
    def test_a_fiction_only_run_is_not_framed_as_a_coding_assistant(self):
        p = mod.build_prompt("", rows(FICTION), None)
        self.assertNotIn("coding assistant", p)
        self.assertIn(PAYLOADS[FICTION[0]]["role"], p)

    def test_the_kubernetes_payloads_keep_the_coding_assistant_role(self):
        p = mod.build_prompt("", rows(["schema_adherence", "scope_creep"]), None)
        self.assertIn("coding assistant", p)

    def test_a_mixed_run_names_every_role_and_asks_per_role(self):
        p = mod.build_prompt("", rows(["schema_adherence"] + FICTION), None)
        self.assertIn("coding assistant", p)
        self.assertIn(PAYLOADS[FICTION[0]]["role"], p)
        self.assertIn("for each role", p)

    def test_every_role_on_disk_is_a_non_empty_string(self):
        for name, spec in PAYLOADS.items():
            if "role" in spec:
                self.assertIsInstance(spec["role"], str, name)
                self.assertTrue(spec["role"].strip(), name)

    def test_the_writing_payloads_declare_a_role(self):
        """The payloads the 40533339607 prompt mis-framed. New fiction
        payloads must declare one too, or they inherit the coding default."""
        for name in FICTION + ["creative_scene_in_voice"]:
            self.assertIn("role", PAYLOADS[name], name)


if __name__ == "__main__":
    unittest.main()
