#!/usr/bin/env python3
"""The creative-writing payload (creative_scene_in_voice).

homelab#1151 asks which local model should drive a fiction preset, and the
overnight sweep could not answer it: 24 payloads measured factual recall,
refusal, schema adherence and constraint-following, and NONE of them scored
whether a model can produce narrative prose and stay inside it. The only
available signal was output length, which is not a measure of writing.

The trap this payload has to avoid is the one family_age_appropriate_explanation
names: 'evocative' and 'well-paced' are not substring-checkable, and grading
them would inject noise into every other result. So it grades two things that
ARE checkable — did the brief's named elements appear, and did the model break
frame with assistant boilerplate.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "benchmarks" / "payloads" / "creative_scene_in_voice.json"
_s = importlib.util.spec_from_file_location(
    "run_benchmark", ROOT / "benchmarks" / "run_benchmark.py")
rb = importlib.util.module_from_spec(_s)
sys.modules["run_benchmark"] = rb
_s.loader.exec_module(rb)

PAYLOAD = json.loads(SPEC.read_text())

GOOD_SCENE = (
    "The lighthouse held its ground against the storm, each gust throwing "
    "salt water across the gallery glass. Mara climbed the stairs with the "
    "letter still sealed in her coat pocket, the paper gone soft from three "
    "days of carrying. She had told herself she would read it once the wind "
    "dropped. The wind did not drop. Below, the sea worked at the rocks the "
    "way it always had, patient and without opinion, and she watched the "
    "beam swing out over it until morning."
)
FRAMED = (
    "Here is a short scene for you:\n\n" + GOOD_SCENE +
    "\n\nI hope this helps! Let me know if you would like me to revise it."
)


def g(text):
    return rb.grade(text, PAYLOAD["quality_facts"], PAYLOAD["quality_forbidden"],
                    forbidden_scope=PAYLOAD.get("forbidden_scope", "anywhere"),
                    facts_mode=PAYLOAD.get("facts_mode", "all"))


class PayloadShapeTest(unittest.TestCase):

    def test_name_matches_filename(self):
        self.assertEqual(PAYLOAD["name"], SPEC.stem)

    def test_documents_what_pass_and_fail_mean(self):
        d = PAYLOAD["description"].lower()
        self.assertIn("pass", d)
        self.assertIn("fail", d)

    def test_forbidden_scope_is_one_grade_implements(self):
        self.assertIn(PAYLOAD.get("forbidden_scope", "anywhere"),
                      ("anywhere", "yaml_only"))

    def test_no_string_is_both_required_and_forbidden(self):
        facts = {f.lower() for f in PAYLOAD["quality_facts"]}
        forb = {f.lower() for f in PAYLOAD["quality_forbidden"]}
        self.assertEqual(facts & forb, set())

    def test_targets_the_chat_path_not_the_agentic_harness(self):
        self.assertIn("messages", PAYLOAD["payload"])

    def test_forbidden_phrases_are_absent_from_genuine_prose(self):
        """The design rule: forbid only phrases with no plausible place in a
        scene. A forbidden term that a good answer might legitimately use
        would punish the writing this payload exists to reward."""
        low = GOOD_SCENE.lower()
        for f in PAYLOAD["quality_forbidden"]:
            self.assertNotIn(f.lower(), low, f"{f!r} would fire on good prose")


class GradingTest(unittest.TestCase):

    def test_a_clean_scene_passes(self):
        r = g(GOOD_SCENE)
        self.assertEqual(r["violations"], [])
        self.assertEqual(r["facts_score"], 1.0)

    def test_assistant_framing_is_caught(self):
        """The actual failure mode: correct prose wrapped in preamble and a
        closing offer to revise. The scene is fine; the frame is the defect."""
        r = g(FRAMED)
        self.assertTrue(r["violations"])

    def test_a_dropped_brief_element_lowers_the_score(self):
        without_letter = GOOD_SCENE.replace("letter", "photograph")
        r = g(without_letter)
        self.assertLess(r["facts_score"], 1.0)

    def test_a_refusal_scores_zero_facts_without_false_forbidden_hits(self):
        r = g("I'm not able to help with that request.")
        self.assertEqual(r["facts_score"], 0.0)

    def test_dialogue_using_certainly_is_not_penalised(self):
        """'Certainly' and \"here's a\" are deliberately NOT forbidden: both
        appear naturally in dialogue, and forbidding them would fail a good
        scene for containing speech."""
        scene = GOOD_SCENE + ' "Certainly," he said. "Here\'s a thought."'
        self.assertEqual(g(scene)["violations"], [])


if __name__ == "__main__":
    unittest.main()
