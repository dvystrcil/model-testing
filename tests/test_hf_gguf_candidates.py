#!/usr/bin/env python3
"""Pure-function tests for bin/hf-gguf-candidates.py.

No network: search() and quant_sizes() hit HuggingFace and are exercised by
running the tool. What is tested here is the two decisions that made the
first version of this tool actively misleading.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
_s = importlib.util.spec_from_file_location("hfc", BIN / "hf-gguf-candidates.py")
hfc = importlib.util.module_from_spec(_s)
sys.modules["hfc"] = hfc
_s.loader.exec_module(hfc)


class BestQuantTest(unittest.TestCase):
    """The first version took the SMALLEST quant that fit, which surfaced
    IQ1_S / IQ2_M / Q2_K rows. Those are heavily degraded: a 27B squeezed
    into IQ2 tells you nothing about whether the model is good, so the tool
    was ranking models by how badly they could be compressed."""

    def test_prefers_the_highest_quality_quant_that_fits(self):
        q, gb = hfc.best_quant({"IQ2_M": 10.3, "Q4_K_M": 18.0, "Q8_0": 29.0})
        self.assertEqual(q, "Q8_0")
        self.assertEqual(gb, 29.0)

    def test_does_not_simply_take_the_largest(self):
        # BF16 outranks Q8_0, so ordering is by quality, not by size.
        q, _ = hfc.best_quant({"Q8_0": 29.0, "BF16": 40.0})
        self.assertEqual(q, "BF16")

    def test_a_degraded_only_repo_still_returns_its_best(self):
        q, _ = hfc.best_quant({"IQ1_S": 8.0, "IQ2_M": 10.0})
        self.assertEqual(q, "IQ2_M")

    def test_unknown_quant_names_sort_last_rather_than_crashing(self):
        q, _ = hfc.best_quant({"WEIRD_NEW": 12.0, "Q4_K_M": 18.0})
        self.assertEqual(q, "Q4_K_M")

    def test_a_single_option_is_returned_as_is(self):
        self.assertEqual(hfc.best_quant({"Q4_K_M": 18.0}), ("Q4_K_M", 18.0))

    def test_usable_set_draws_the_line_at_iq4(self):
        self.assertIn("Q4_K_M", hfc.USABLE)
        self.assertIn("IQ4_NL", hfc.USABLE)
        self.assertNotIn("Q3_K_M", hfc.USABLE)
        self.assertNotIn("IQ2_M", hfc.USABLE)


class SafetyStrippedTest(unittest.TestCase):
    """Download rankings on HuggingFace are dominated by safety-stripped
    variants. Presenting those as neutral candidates would be wrong for a
    homelab that runs family_* payloads and already has homelab#1046 open
    on refusal reliability."""

    def test_flags_the_common_markers(self):
        for repo in ("huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF",
                     "OBLITERATUS/Qwen3.8-27B-OBLITERATED",
                     "orcarouter/Qwen3.8-27B-Uncensored-GGUF",
                     "outsourc-e/Qwen3.8-27B-Unleashed-GGUF",
                     "0bserverx/Qwen3.8-27B-Heretic-Abliterated"):
            self.assertTrue(hfc.is_safety_stripped(repo), repo)

    def test_is_case_insensitive(self):
        self.assertTrue(hfc.is_safety_stripped("Foo/BAR-UNCENSORED-GGUF"))

    def test_does_not_flag_ordinary_repos(self):
        for repo in ("unsloth/Qwen3.8-27B-GGUF",
                     "ornith-ai/Ornith-1.5-35B-A3B-GGUF",
                     "peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF",
                     "empero-ai/Qwen3.8-9B-Distill-GGUF"):
            self.assertFalse(hfc.is_safety_stripped(repo), repo)


if __name__ == "__main__":
    unittest.main()
