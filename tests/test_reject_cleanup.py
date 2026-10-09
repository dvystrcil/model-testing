"""Rejected candidates must not stay on max-01's disk.

2026-10-09: candidate discovery pre-flighted hf.co/unsloth/Qwen-Image-2.1-GGUF:F16
(an image-generation model; generate -> HTTP 500), recorded it as REJECT --
"will not be retried" -- and left its 14.2 GB on disk forever. The models live
on a hostPath on max-01's ROOT filesystem, which is also an etcd member's
system disk, and Ollama 0.40 already doubles every migrated model.

The rule: delete a candidate after a REJECT, but ONLY if this pre-flight is
what put it there. Deleting a model that was already present would take it
from whoever uses it; when presence is unknown, keep it.

Never after WEDGED / SKIPPED-UNHEALTHY either: the GPU needs a human, and
another API call (or the unload `ollama rm` triggers) is the wrong move.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


cand = _load("candidates", ROOT / "benchmarks" / "candidates.py")
pre = _load("preflight", ROOT / "bin" / "preflight-candidate.py")


class TestShouldRemove(unittest.TestCase):
    def r(self, **kw):
        base = {"pulled": True, "preexisting": False}
        base.update(kw)
        return base

    def test_a_reject_this_run_pulled_is_removed(self):
        self.assertTrue(cand.should_remove_after("REJECT", self.r()))

    def test_a_model_that_was_already_there_is_kept(self):
        self.assertFalse(cand.should_remove_after("REJECT", self.r(preexisting=True)))

    def test_unknown_presence_is_kept(self):
        r = self.r(); del r["preexisting"]
        self.assertFalse(cand.should_remove_after("REJECT", r))

    def test_nothing_pulled_nothing_removed(self):
        self.assertFalse(cand.should_remove_after("REJECT", self.r(pulled=False)))

    def test_an_accepted_model_is_kept(self):
        self.assertFalse(cand.should_remove_after("OK", self.r()))

    def test_never_after_a_wedge_or_unhealthy_gpu(self):
        for v in ("WEDGED", "SKIPPED-UNHEALTHY"):
            with self.subTest(v=v):
                self.assertFalse(cand.should_remove_after(v, self.r()))


class FakeOllama:
    """Stands in for pre._get / pre._post / pre._delete."""

    def __init__(self, tags=(), generate_fails=False):
        self.tags = list(tags)
        self.generate_fails = generate_fails
        self.deleted = []

    def get(self, url, timeout):
        if url.endswith("/api/tags"):
            return {"models": [{"name": n} for n in self.tags]}
        if url.endswith("/api/ps"):
            return {"models": []}
        raise AssertionError(url)

    def post(self, url, body, timeout):
        if url.endswith("/api/pull"):
            if body["model"] not in self.tags:
                self.tags.append(body["model"])
            return {"status": "success"}
        if url.endswith("/api/generate"):
            if body["model"] != pre.HEALTH_MODEL and body.get("prompt") and self.generate_fails:
                raise OSError("HTTP Error 500: Internal Server Error")
            return {"response": "ok", "done_reason": "stop"}
        if url.endswith("/api/show"):
            return {"capabilities": ["completion", "tools"], "details": {}}
        raise AssertionError(url)

    def delete(self, url, body, timeout):
        assert url.endswith("/api/delete"), url
        self.deleted.append(body["model"])
        self.tags.remove(body["model"])
        return {}


class WithFake(unittest.TestCase):
    def install(self, fake, module=None):
        # `module`: discover-candidates.py loads its OWN copy of the
        # preflight module, so the loop tests must patch that copy -- patching
        # this file's `pre` left the loop on the real network.
        m = module or pre
        saved = (m._get, m._post, getattr(m, "_delete", None))
        m._get, m._post, m._delete = fake.get, fake.post, fake.delete
        self.addCleanup(lambda: setattr(m, "_get", saved[0]))
        self.addCleanup(lambda: setattr(m, "_post", saved[1]))
        self.addCleanup(lambda: setattr(m, "_delete", saved[2]))


class TestPreflightRecordsPresence(WithFake):
    def test_absent_before_pull_is_recorded(self):
        f = FakeOllama(tags=[pre.HEALTH_MODEL]); self.install(f)
        r = pre.preflight("http://o", "hf.co/x/New-GGUF:Q4_K_M")
        self.assertIs(r["preexisting"], False)

    def test_present_before_pull_is_recorded(self):
        f = FakeOllama(tags=[pre.HEALTH_MODEL, "hf.co/x/Old-GGUF:Q4_K_M"]); self.install(f)
        r = pre.preflight("http://o", "hf.co/x/Old-GGUF:Q4_K_M")
        self.assertIs(r["preexisting"], True)

    def test_an_untagged_name_matches_latest(self):
        f = FakeOllama(tags=[pre.HEALTH_MODEL, "foo:latest"]); self.install(f)
        self.assertIs(pre.preflight("http://o", "foo")["preexisting"], True)


class TestRemoveModel(WithFake):
    def test_removes_and_says_so(self):
        f = FakeOllama(tags=["hf.co/x/Bad:F16"]); self.install(f)
        note = pre.remove_model("http://o", "hf.co/x/Bad:F16")
        self.assertEqual(f.deleted, ["hf.co/x/Bad:F16"])
        self.assertIn("removed", note)

    def test_a_failed_delete_is_a_note_not_a_crash(self):
        """The verdict is already decided and ledgered; a failed cleanup must
        not kill the run before the PR is opened."""
        def boom(url, body, timeout):
            raise OSError("connection refused")
        saved = getattr(pre, "_delete", None)
        pre._delete = boom
        self.addCleanup(lambda: setattr(pre, "_delete", saved))
        note = pre.remove_model("http://o", "m:x")
        self.assertIn("remove failed", note)


class TestTheLoop(WithFake):
    """The composition: discover-candidates' loop, not just its parts."""

    def loop(self):
        return _load("discover", ROOT / "bin" / "discover-candidates.py")

    def test_the_2026_10_09_shape(self):
        """Three candidates, as on 2026-10-09: two OK (already on disk), one
        REJECT pulled fresh. Only the reject is removed."""
        ok1, bad, ok2 = ("hf.co/a/Ornith-GGUF:BF16", "hf.co/b/Qwen-Image-GGUF:F16",
                         "hf.co/c/Qwen3.8-GGUF:Q8_0")
        f = FakeOllama(tags=[pre.HEALTH_MODEL, ok1, ok2])
        real_post = f.post

        def post(url, body, timeout):
            if url.endswith("/api/generate") and body["model"] == bad and body.get("prompt"):
                raise OSError("HTTP Error 500: Internal Server Error")
            return real_post(url, body, timeout)
        f.post = post
        d = self.loop()
        self.install(f, d.pre)
        accepted, rejected, breaker, results = d.preflight_all(
            [{"model": ok1}, {"model": bad}, {"model": ok2}], "http://o", 3)
        self.assertEqual(f.deleted, [bad])
        self.assertEqual([r["model"] for r in accepted], [ok1, ok2])
        self.assertEqual([r["model"] for r, _ in rejected], [bad])
        self.assertTrue(any("removed" in n for n in rejected[0][0]["notes"]))
        self.assertEqual(breaker, [])

    def test_a_reject_that_was_already_present_is_kept(self):
        m = "hf.co/x/AlreadyHere-GGUF:Q4_K_M"
        f = FakeOllama(tags=[pre.HEALTH_MODEL, m], generate_fails=True)
        d = self.loop()
        self.install(f, d.pre)
        _, rejected, _, _ = d.preflight_all([{"model": m}], "http://o", 3)
        self.assertEqual(f.deleted, [])
        self.assertEqual(len(rejected), 1)


if __name__ == "__main__":
    unittest.main()
