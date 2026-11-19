#!/usr/bin/env python3
"""The confabulation guard and the run's own environment (model-testing#177).

Run 44602629787 failed analyze on a correct report:

    ANALYZE-CONFABULATION invented=a9ba7a4,model-testing-runner-runner-kk8f6

`a9ba7a4` is the benchmark's git commit and the other is the runner host.
The benchmark records both in its metadata row and the report prints them in
its Environment table; the guard compared the analysis against result rows
only, so the report describing itself read as invention.

These tests drive `main()` end to end over fixture result files, with only
the summariser call replaced by the fixture's analysis text, and read the
ANALYZE-CONFABULATION marker the CI gate reads. The three real catches the
guard made before must still fail.
"""
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parent.parent / "benchmarks"
_spec = importlib.util.spec_from_file_location("analyze", BIN / "analyze.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["analyze"] = mod
_spec.loader.exec_module(mod)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "confab-177"


def confab_marker(case: str) -> str:
    """Run analyze.py's main() on a fixture; return its confabulation marker."""
    d = FIXTURES / case
    analysis = (d / "analysis.md").read_text()
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(mod, "ollama_chat", return_value=analysis), \
            mock.patch.object(sys, "argv", [
                "analyze.py", str(d / "results.jsonl"),
                "--out", str(Path(tmp) / "report.md")]), \
            contextlib.redirect_stderr(err):
        mod.main()
    lines = [ln for ln in err.getvalue().splitlines()
             if ln.startswith("ANALYZE-CONFABULATION")]
    assert len(lines) == 1, err.getvalue()
    return lines[0]


class EnvironmentIsNotInventionTest(unittest.TestCase):
    """AC1 and AC3: identifiers from the run environment are known tokens."""

    def test_the_44602629787_report_shape_passes(self):
        """Commit, runner host (from both metadata rows of a merged sweep)
        and ollama version are named; none of them is a model."""
        self.assertEqual(
            confab_marker("env-44602629787"),
            "ANALYZE-CONFABULATION invented=none ambiguous=none "
            "abbreviated=none")

    def test_a_host_the_code_has_never_seen_passes(self):
        """The known tokens come from the run's metadata, not an allowlist:
        a brand-new runner host needs no code change."""
        self.assertNotIn("brand-new-gpu-host", mod.Path(mod.__file__).read_text())
        self.assertEqual(
            confab_marker("env-unseen-host"),
            "ANALYZE-CONFABULATION invented=none ambiguous=none "
            "abbreviated=none")

    def test_environment_tokens_come_from_every_metadata_row(self):
        toks = mod.environment_tokens(
            FIXTURES / "env-44602629787" / "results.jsonl")
        for t in ("a9ba7a4", "model-testing-runner-runner-kk8f6",
                  "model-testing-runner-runner-6pz89", "0.40.16"):
            self.assertIn(t, toks)

    def test_an_environment_token_does_not_excuse_a_model_name(self):
        """Only exact environment values are excused; a model-shaped name
        that merely contains the host is still invention."""
        invented, _, _ = mod.classify_named_models(
            "`runner-kk8f6-7b` won", ["qwen3.6:35b"],
            environment={"runner-kk8f6"})
        self.assertEqual(invented, ["runner-kk8f6-7b"])


class RealCatchesStillFailTest(unittest.TestCase):
    """AC2: the guard's three real catches, with an environment present."""

    def test_the_a3a_typo_from_41560733837_is_invented(self):
        self.assertEqual(
            confab_marker("typo-41560733837"),
            "ANALYZE-CONFABULATION "
            "invented=Qwen3-Coder-30B-A3A-Instruct-GGUF:Q8_0 "
            "ambiguous=none abbreviated=none")

    def test_the_coder_next_nickname_from_41076227331_is_invented(self):
        self.assertEqual(
            confab_marker("nickname-41076227331"),
            "ANALYZE-CONFABULATION invented=coder-next "
            "ambiguous=none abbreviated=none")

    def test_the_qwen3_6_short_name_from_40533339607_is_ambiguous(self):
        self.assertEqual(
            confab_marker("ambiguous-40533339607"),
            "ANALYZE-CONFABULATION invented=none "
            "ambiguous=qwen3.6 abbreviated=none")


if __name__ == "__main__":
    unittest.main()
