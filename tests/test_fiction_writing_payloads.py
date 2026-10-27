#!/usr/bin/env python3
"""Contract and deterministic-grader tests for additive fiction-writing payloads.

These are smoke tests, not an LLM-as-judge: string grading can catch dropped
anchors and explicit forbidden phrases, but cannot prove literary quality or
recognise every paraphrased contradiction. Keep that limitation explicit.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_DIR = ROOT / "benchmarks" / "payloads"
SPEC = importlib.util.spec_from_file_location(
    "run_benchmark", ROOT / "benchmarks" / "run_benchmark.py")
rb = importlib.util.module_from_spec(SPEC)
sys.modules["run_benchmark"] = rb
SPEC.loader.exec_module(rb)

NAMES = (
    "fiction_canon_adherence",
    "fiction_continuity_audit",
    "fiction_wiki_edit_discipline",
    "fiction_semantic_canon_validation",
    "fiction_multifact_continuity",
    "fiction_wiki_semantic_preservation",
    "fiction_prose_quality_consistency",
)


class FictionPayloadContractTest(unittest.TestCase):
    def load(self, name):
        path = PAYLOAD_DIR / f"{name}.json"
        self.assertTrue(path.is_file(), f"missing payload: {path}")
        payload = json.loads(path.read_text())
        self.assertEqual(payload["name"], name)
        self.assertIn("pass", payload["description"].lower())
        self.assertIn("smoke test", payload["description"].lower())
        self.assertIn("messages", payload["payload"])
        self.assertIn("system", {m["role"] for m in payload["payload"]["messages"]})
        self.assertIn("user", {m["role"] for m in payload["payload"]["messages"]})
        self.assertIn(payload.get("forbidden_scope", "anywhere"), ("anywhere", "yaml_only"))
        facts = {x.lower() for x in payload["quality_facts"]}
        forbidden = {x.lower() for x in payload["quality_forbidden"]}
        self.assertFalse(facts & forbidden, f"{name}: facts overlap forbidden phrases")
        return payload

    def grade(self, payload, answer):
        return rb.grade(
            answer,
            payload["quality_facts"],
            payload["quality_forbidden"],
            forbidden_scope=payload.get("forbidden_scope", "anywhere"),
            facts_mode=payload.get("facts_mode", "all"),
        )

    def test_all_fiction_payloads_have_valid_contracts(self):
        for name in NAMES:
            with self.subTest(payload=name):
                self.load(name)

    def test_canon_adherence_detects_explicit_breaks(self):
        payload = self.load("fiction_canon_adherence")
        good = (
            "Mara held the brass key in her left hand. The sealed letter stayed "
            "in her pocket, unread; she did not know what lock the key belonged to."
        )
        bad = (
            "Mara held the brass key in her right hand and opened the letter. "
            "Then she swam across the river."
        )
        self.assertEqual(self.grade(payload, good)["violations"], [])
        self.assertEqual(self.grade(payload, good)["facts_score"], 1.0)
        self.assertTrue(self.grade(payload, bad)["violations"])

    def test_continuity_audit_requires_conflict_and_new_info_signal(self):
        payload = self.load("fiction_continuity_audit")
        good = (
            "Contradictions: Ilyan is left-handed in canon, but the passage says "
            "he signed with his right hand. Compatible new information: the "
            "dark green coat is new information because canon does not specify "
            "his coat. Unresolved ambiguities: none established by these facts."
        )
        bad = (
            "There are no contradictions. Both are consistent; the green coat "
            "was already established."
        )
        self.assertEqual(self.grade(payload, good)["violations"], [])
        self.assertEqual(self.grade(payload, good)["facts_score"], 1.0)
        self.assertTrue(self.grade(payload, bad)["violations"])

    def test_wiki_edit_discipline_preserves_validation_and_unknowns(self):
        payload = self.load("fiction_wiki_edit_discipline")
        good = (
            "author_validated: false\nMara carries a brass key; the lock is unknown. "
            "New fact: the key feels warm. The sealed letter remains unread."
        )
        bad = (
            "author_validated: true\nThe brass key opens the vault. Mara read the letter."
        )
        self.assertEqual(self.grade(payload, good)["violations"], [])
        self.assertEqual(self.grade(payload, good)["facts_score"], 1.0)
        self.assertTrue(self.grade(payload, bad)["violations"])


if __name__ == "__main__":
    unittest.main()
