#!/usr/bin/env python3
"""The sweep cache key must change when the HARNESS changes.

Sweep 33831285722 ran for nine hours across 25 payloads and produced no
`answer_words` at all, so ANALYZE-TERSENESS reported `terse=none` and the
whole point of model-testing#104 silently did not happen.

The results were never generated. Every JSONL on the PVC carried an mtime
inside the same minute (`Sep 4 02:56`) for a nine-hour sweep, and the
truncation counts came back byte-identical to the previous run:

    truncated=gemma4:26b-a4b-it-qat:2,glm-4.7-flash:q4_K_M:1,
              nemotron-3.5-lightning:30b-a3b-q4_K_M:4,qwen3.5:27B:8,
              qwen3.6:27B:2,qwen3.6:35b:2

They were restored from cache. The key is built from the payload file,
models.yaml, runs, model override, system prompt and the ollama version --
and NOT from `run_benchmark.py`. #104 changed what the harness records
without touching any of those, so the key was unchanged and stale results
were served in place of the run.

This is the same class the ollama version was added for. test-cache.yaml
says so in its own words:

    an ollama 0.24 -> 0.30.2 upgrade silently hit cache because the
    version was not part of the key

Same shape, different input: anything that changes what a run PRODUCES
must be part of what identifies it.

Run: python3 -m pytest tests/test_sweep_cache_key.py
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

WF = (Path(__file__).resolve().parent.parent
      / ".github/workflows/model-sweep.yaml").read_text()


def _key_step() -> str:
    """The `Generate cache key` step's shell body."""
    m = re.search(r"name: Generate cache key(.*?)\n      - name: ", WF, re.S)
    assert m, "Generate cache key step not found"
    return m.group(1)


class CacheKeyCoversTheHarnessTest(unittest.TestCase):

    def test_the_step_still_exists(self):
        """Guard the guard: if the step is renamed, every assertion below
        would fail loudly rather than pass vacuously."""
        self.assertIn("KEY=", _key_step())

    def test_the_harness_is_hashed_into_the_key(self):
        """The actual fix. Without this, changing what run_benchmark.py
        RECORDS leaves the key identical and the cache serves results from
        before the change."""
        step = _key_step()
        self.assertIn("run_benchmark.py", step,
                      "cache key does not depend on the benchmark harness")

    def test_the_harness_hash_is_actually_used_in_the_key(self):
        """Computing a hash and forgetting to concatenate it would look
        entirely correct in review and change nothing at all."""
        step = _key_step()
        m = re.search(r"HARNESS_HASH=\$\(sha256sum ([^\)]*run_benchmark\.py)", step)
        self.assertIsNotNone(m, "no HARNESS_HASH computed from run_benchmark.py")
        key_line = next(l for l in step.splitlines() if l.strip().startswith("KEY="))
        self.assertIn("HARNESS_HASH", key_line,
                      "HARNESS_HASH is computed but never used in KEY")

    def test_the_inputs_that_already_mattered_are_still_there(self):
        """Adding one input must not quietly drop another -- each of these
        was added because omitting it served stale results once."""
        key_line = next(l for l in _key_step().splitlines()
                        if l.strip().startswith("KEY="))
        for token in ("PAYLOAD_HASH", "MODELS_HASH", "RUNS",
                      "SYSPROMPT_HASH", "OLLAMA_VERSION"):
            with self.subTest(token=token):
                self.assertIn(token, key_line)
