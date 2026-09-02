#!/usr/bin/env python3
"""Weekly candidate discovery (model-testing#102).

`bin/hf-gguf-candidates.py` already finds GGUFs that would fit max-01. What
did not exist was the loop around it: filter what we already know about,
pre-flight each survivor, and propose the ones that work.

The whole design is shaped by one hazard, which models.yaml states plainly:

    Pre-flighting attended is deliberate -- a first-ever load failing at 3am
    is how this GPU gets wedged.

`qwen3.8:27b` is commented out of the roster right now for exactly that:
POST /v1/chat/completions never returns (upstream ollama/ollama#17790). An
unattended sweep would have stalled on it, and the sweep's own concurrency
comment records that killing a sweep mid-flight has wedged max-01 before.

So this must never (a) re-propose something already rejected, (b) keep
pulling models after the GPU has wedged, or (c) blame a candidate for a
wedge that was already there when it started.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "candidates", ROOT / "benchmarks" / "candidates.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["candidates"] = mod
_spec.loader.exec_module(mod)


MODELS_YAML = """
models:
  - name: qwen3.6:35b
  # a comment that is not a model
  - name: gemma4:26b-a4b-it-qat            # ~15 GB

  # DELIBERATELY EXCLUDED -- do not re-enable without checking upstream.
  # qwen3.8:27b hangs: POST /v1/chat/completions never returns.
  # - name: qwen3.8:27b
  #- name: gemma4:26b
"""


class KnownModelsTest(unittest.TestCase):
    """A commented-out model is the STRONGEST kind of known, not the weakest.

    Someone took it out on purpose and wrote down why. Re-proposing it every
    Monday is how an automation gets muted -- and in qwen3.8:27b's case it
    would be proposing a model that hangs the GPU.
    """

    def test_active_models_are_known(self):
        known = mod.known_models(MODELS_YAML)
        self.assertIn("qwen3.6:35b", known)
        self.assertIn("gemma4:26b-a4b-it-qat", known)

    def test_a_commented_out_model_is_STILL_known(self):
        known = mod.known_models(MODELS_YAML)
        self.assertIn("qwen3.8:27b", known)

    def test_the_tight_comment_form_is_caught_too(self):
        """`#- name:` with no space. A regex that only handles `# - name:`
        would silently re-propose it."""
        self.assertIn("gemma4:26b", mod.known_models(MODELS_YAML))

    def test_prose_is_not_mistaken_for_a_model(self):
        self.assertNotIn("a comment that is not a model",
                         mod.known_models(MODELS_YAML))

    def test_a_trailing_size_comment_is_not_part_of_the_name(self):
        self.assertNotIn("gemma4:26b-a4b-it-qat            # ~15 GB",
                         mod.known_models(MODELS_YAML))


class LedgerTest(unittest.TestCase):
    """Without a ledger this re-tests every rejected model every week, and
    each re-test is another first-ever load of something known to be bad."""

    LEDGER = {
        "hf.co/x/Broken-GGUF:Q4_K_M": {"verdict": "REJECT",
                                       "reason": "no tools capability"},
        "hf.co/x/Good-GGUF:Q4_K_M": {"verdict": "ACCEPT", "reason": "merged"},
    }

    def test_a_rejected_candidate_is_not_retried(self):
        out = mod.filter_candidates(
            [{"model": "hf.co/x/Broken-GGUF:Q4_K_M"}], set(), self.LEDGER)
        self.assertEqual(out, [])

    def test_an_accepted_candidate_is_not_re_proposed(self):
        out = mod.filter_candidates(
            [{"model": "hf.co/x/Good-GGUF:Q4_K_M"}], set(), self.LEDGER)
        self.assertEqual(out, [])

    def test_something_genuinely_new_survives(self):
        cand = {"model": "hf.co/x/New-GGUF:Q4_K_M"}
        self.assertEqual(mod.filter_candidates([cand], set(), self.LEDGER),
                         [cand])

    def test_a_model_already_in_the_roster_is_dropped(self):
        cand = {"model": "qwen3.6:35b"}
        self.assertEqual(mod.filter_candidates([cand], {"qwen3.6:35b"}, {}), [])

    def test_safety_stripped_repos_are_dropped_by_default(self):
        """homelab#1046: no model in the fleet reliably refuses a privileged
        container request, and the sweep carries family_* payloads. A model
        whose selling point is that refusals were removed is not a candidate
        for a family-facing preset."""
        cand = {"model": "hf.co/x/Qwen3-abliterated-GGUF:Q4_K_M"}
        self.assertEqual(mod.filter_candidates([cand], set(), {}), [])


class PreflightVerdictTest(unittest.TestCase):

    def test_a_model_that_generates_and_reports_tools_is_a_candidate(self):
        v = mod.classify_preflight({"pulled": True, "generated": True,
                                    "tools": True, "healthy_after": True})
        self.assertEqual(v, "OK")

    def test_no_tool_calling_is_a_reject_not_a_failure(self):
        """The sweep's agentic payloads are tool-call tests. A model without
        tools cannot answer the question the sweep exists to ask -- but it
        did not break anything, so it is a REJECT, not a breaker."""
        v = mod.classify_preflight({"pulled": True, "generated": True,
                                    "tools": False, "healthy_after": True})
        self.assertEqual(v, "REJECT")

    def test_a_model_that_never_returns_is_a_reject(self):
        """qwen3.8:27b's exact shape."""
        v = mod.classify_preflight({"pulled": True, "generated": False,
                                    "tools": None, "healthy_after": True})
        self.assertEqual(v, "REJECT")

    def test_a_wedged_gpu_after_the_probe_is_a_BREAKER(self):
        """The one outcome that must stop the loop. Continuing means pulling
        the next unknown model onto a GPU that is already stuck."""
        v = mod.classify_preflight({"pulled": True, "generated": False,
                                    "tools": None, "healthy_after": False})
        self.assertEqual(v, "WEDGED")

    def test_only_WEDGED_trips_the_breaker(self):
        self.assertTrue(mod.should_trip_breaker("WEDGED"))
        for v in ("OK", "REJECT"):
            self.assertFalse(mod.should_trip_breaker(v))

    def test_a_wedge_that_was_ALREADY_there_is_not_the_candidate_s_fault(self):
        """Blaming the current candidate would blacklist an innocent model
        permanently in the ledger, and hide that the GPU needs a human."""
        v = mod.classify_preflight({"healthy_before": False})
        self.assertEqual(v, "SKIPPED-UNHEALTHY")
        self.assertTrue(mod.should_trip_breaker(v))

    def test_a_skipped_candidate_is_not_written_to_the_ledger(self):
        """It was never actually tested. Recording a verdict would mean
        never testing it again."""
        self.assertFalse(mod.is_ledgerable("SKIPPED-UNHEALTHY"))
        self.assertFalse(mod.is_ledgerable("WEDGED"))
        self.assertTrue(mod.is_ledgerable("REJECT"))
        self.assertTrue(mod.is_ledgerable("OK"))


class RosterPatchTest(unittest.TestCase):
    """The PR this opens edits models.yaml. A bad patch does not fail loudly
    -- it produces a roster that still parses and sweeps the wrong set."""

    BASE = "models:\n  - name: qwen3.6:35b\n"

    ACCEPTED = [{
        "model": "hf.co/org/Cool-30B-GGUF:Q4_K_M",
        "size_gb": 18.4, "params": "30.5B", "quant": "Q4_K_M",
        "capabilities": ["completion", "tools"],
        "notes": ["done_reason=stop"],
    }]

    def test_the_new_model_is_appended(self):
        out = mod.render_roster_addition(self.BASE, self.ACCEPTED)
        self.assertIn("- name: hf.co/org/Cool-30B-GGUF:Q4_K_M", out)

    def test_the_existing_roster_is_preserved_verbatim(self):
        """A rewrite that reformats the file loses the comments that carry
        every decision -- why qwen3.8:27b is excluded, what each arm tests."""
        out = mod.render_roster_addition(self.BASE, self.ACCEPTED)
        self.assertTrue(out.startswith(self.BASE.rstrip("\n")))

    def test_the_addition_carries_its_evidence(self):
        """A name alone tells the reviewer nothing, and the reviewer is the
        gate this whole design is built around."""
        out = mod.render_roster_addition(self.BASE, self.ACCEPTED)
        for fragment in ("30.5B", "Q4_K_M", "18.4", "tools"):
            self.assertIn(fragment, out)

    def test_it_is_parseable_yaml_with_the_new_model_live(self):
        import yaml
        d = yaml.safe_load(mod.render_roster_addition(self.BASE, self.ACCEPTED))
        names = [m["name"] for m in d["models"]]
        self.assertIn("hf.co/org/Cool-30B-GGUF:Q4_K_M", names)
        self.assertIn("qwen3.6:35b", names)

    def test_nothing_accepted_means_no_edit_at_all(self):
        """An empty PR every Monday is how a weekly automation gets ignored."""
        self.assertEqual(mod.render_roster_addition(self.BASE, []), self.BASE)


class LedgerWriteTest(unittest.TestCase):

    def test_a_reject_records_why(self):
        led = mod.updated_ledger({}, [
            ({"model": "hf.co/x/A:Q4_K_M", "notes": ["generate failed: timeout"]},
             "REJECT")])
        self.assertEqual(led["hf.co/x/A:Q4_K_M"]["verdict"], "REJECT")
        self.assertIn("timeout", led["hf.co/x/A:Q4_K_M"]["reason"])

    def test_a_wedge_is_NOT_recorded_against_the_model(self):
        """It is a fact about the GPU. Writing it here would permanently
        blacklist a model that may be perfectly fine."""
        led = mod.updated_ledger({}, [({"model": "hf.co/x/B:Q4_K_M"}, "WEDGED")])
        self.assertEqual(led, {})

    def test_existing_entries_survive(self):
        led = mod.updated_ledger({"old": {"verdict": "REJECT", "reason": "x"}},
                                 [({"model": "new", "notes": []}, "OK")])
        self.assertIn("old", led)
        self.assertIn("new", led)


class SameModelUnderAnotherNameTest(unittest.TestCase):
    """Found by dry-running the real discovery loop, first try.

    The roster excludes `qwen3.8:27b` because POST /v1/chat/completions never
    returns (ollama/ollama#17790). Discovery proposed:

        hf.co/unsloth/Qwen3.8-27B-GGUF:Q8_0

    Exact-name matching does not connect those, so the loop was about to
    spend a first-ever cold load on the one model documented to hang this
    GPU -- the precise hazard the whole design exists to avoid.

    An ollama library tag and a HuggingFace GGUF repo are different strings
    for the same weights. The comparison has to be on the model, not the tag.
    """

    def test_the_hf_repo_of_an_excluded_model_is_recognised(self):
        self.assertTrue(mod.same_model(
            "qwen3.8:27b", "hf.co/unsloth/Qwen3.8-27B-GGUF:Q8_0"))

    def test_it_survives_the_real_roster(self):
        known = mod.known_models(MODELS_YAML)
        out = mod.filter_candidates(
            [{"model": "hf.co/unsloth/Qwen3.8-27B-GGUF:Q8_0"}], known, {})
        self.assertEqual(out, [], "would pre-flight a model known to hang")

    def test_the_two_hf_models_already_local_are_recognised(self):
        """Both arrived by word of mouth and are in the roster under their
        full hf.co paths; discovery must not re-proposethem from another org."""
        self.assertTrue(mod.same_model(
            "hf.co/peculiar-ragdoll/Dagger-Qwen3.6-27B-GGUF:Q4_K_M",
            "hf.co/someone-else/Dagger-Qwen3.6-27B-GGUF:Q5_K_M"))

    def test_quantisation_alone_is_not_a_different_model(self):
        self.assertTrue(mod.same_model("hf.co/x/Foo-GGUF:Q4_K_M",
                                       "hf.co/x/Foo-GGUF:Q8_0"))

    def test_genuinely_different_models_are_not_conflated(self):
        """Over-matching is not the safe direction here -- it silently
        starves the sweep of new models and nobody notices."""
        for a, b in (("qwen3.8:27b", "hf.co/unsloth/Qwen3.6-27B-GGUF:Q8_0"),
                     ("gemma4:31b-it-qat", "hf.co/x/Llama-3.3-70B-GGUF:Q4_K_M"),
                     ("qwen3.6:35b", "hf.co/x/Mistral-Small-GGUF:Q4_K_M")):
            with self.subTest(a=a, b=b):
                self.assertFalse(mod.same_model(a, b))

    def test_near_duplicates_within_one_batch_are_collapsed(self):
        """Also from the real dry run: HF returned both

            hf.co/peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF:Q4_K_S
            hf.co/peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF-MTP:Q4_K_S

        Neither is in the roster, so both survived the known/ledger checks
        and the loop would have spent two cold multi-GB loads on the same
        weights. The budget here is GPU time, not API calls."""
        out = mod.filter_candidates([
            {"model": "hf.co/peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF:Q4_K_S"},
            {"model": "hf.co/peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF-MTP:Q4_K_S"},
        ], set(), {})
        self.assertEqual(len(out), 1)

    def test_the_first_one_wins_so_the_order_is_the_ranking(self):
        """found[] arrives sorted by downloads, so keeping the first keeps
        the more popular packaging of the same weights."""
        out = mod.filter_candidates([
            {"model": "hf.co/a/Foo-GGUF:Q4_K_M"},
            {"model": "hf.co/b/Foo-GGUF-MTP:Q4_K_M"},
        ], set(), {})
        self.assertEqual(out[0]["model"], "hf.co/a/Foo-GGUF:Q4_K_M")
