#!/usr/bin/env python3
"""Tests for the surgical whole-file edit scorer (dvystrcil/model-testing#139).

The fixtures are real dmf responses from 2026-10-29 (homelab#1694, #1697),
captured from qwen3.6:35b and qwen3-coder-next on the same three prompts. The
production guard in homelab bin/cve-constraint-pr.py passed four of them and
refused two; the scorer has to agree with it on every one, or the sweep would
rank models on a different question from the one production asks.

    python3 -m pytest tests/test_surgical_edit.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import run_benchmark  # noqa: E402
from run_benchmark import score_surgical_edit, surgical_edit_original  # noqa: E402

PAYLOADS = Path(__file__).parent.parent / "benchmarks" / "payloads"
CAPTURES = Path(__file__).parent / "fixtures" / "surgical_edit"
NAMES = ["homelab_pip_removal", "kroger_pip_removal", "open_terminal_constraint"]


def spec(name):
    return json.loads((PAYLOADS / f"surgical_edit_{name}.json").read_text())


def capture(name, model):
    return (CAPTURES / f"{name}.{model}.response.md").read_text()


def fenced(text):
    return f"```dockerfile\n{text}```\n"


# ------------------------------------------------------------ payload validity

@pytest.mark.parametrize("name", NAMES)
def test_payload_is_named_after_its_file_and_validates_as_surgical_edit(name):
    s = spec(name)
    assert s["name"] == f"surgical_edit_{name}"
    assert s["validate"]["type"] == "surgical_edit"
    assert s["description"]


@pytest.mark.parametrize("name", NAMES)
def test_original_is_read_from_the_prompt_and_the_task_changes_it(name):
    """A payload whose expected file equals its original passes a model that
    returns the file untouched -- a gate already true before the change."""
    s = spec(name)
    original = surgical_edit_original(s)
    # The whole Dockerfile, not the prompt's earlier example snippet.
    assert any(l.startswith("FROM ") for l in original.splitlines()), original[:80]
    assert len(original.splitlines()) > 40
    if "expected" in s["validate"]:
        assert s["validate"]["expected"] != original
    assert score_surgical_edit(fenced(original), s) == ["unchanged"]


# --------------------------------------------- agree with the production guard

@pytest.mark.parametrize("name,model", [
    ("homelab_pip_removal", "qwen3.6"),
    ("homelab_pip_removal", "coder-next"),
    ("kroger_pip_removal", "qwen3.6"),
    ("open_terminal_constraint", "coder-next"),
])
def test_responses_the_production_guard_passed_score_clean(name, model):
    assert score_surgical_edit(capture(name, model), spec(name)) == []


def test_coder_next_kroger_preamble_and_second_block_are_violations():
    v = score_surgical_edit(capture("kroger_pip_removal", "coder-next"), spec("kroger_pip_removal"))
    assert "code_blocks=2" in v
    assert "text_outside_block" in v


def test_qwen36_open_terminal_collateral_edits_are_violations():
    """The real failure: right constraint, plus a rewritten apt path."""
    v = score_surgical_edit(capture("open_terminal_constraint", "qwen3.6"),
                            spec("open_terminal_constraint"))
    beyond = [x for x in v if x.startswith("changes beyond the requested edit")]
    assert beyond, v
    assert "/etc/sources.list.d/kubernetes.list" in beyond[0]


# ------------------------------------------------- each check, one at a time

def expected_of(name):
    return spec(name)["validate"]["expected"]


def test_no_code_block_is_a_violation():
    v = score_surgical_edit("I would append the uninstall to the last RUN.", spec("homelab_pip_removal"))
    assert v == ["code_blocks=0"]


def test_prose_around_a_correct_block_is_only_text_outside_block():
    s = spec("homelab_pip_removal")
    v = score_surgical_edit("Here is the file:\n\n" + fenced(expected_of("homelab_pip_removal")), s)
    assert v == ["text_outside_block"]


def test_a_think_block_is_not_text_outside_block():
    """run_once strips <think> before grading; the scorer must not depend on that."""
    s = spec("homelab_pip_removal")
    v = score_surgical_edit("<think>which RUN?</think>\n" + fenced(expected_of("homelab_pip_removal")), s)
    assert v == []


def test_reindenting_an_existing_line_is_a_violation():
    s = spec("homelab_pip_removal")
    body = expected_of("homelab_pip_removal").replace("\n    && apk del curl", "\n  && apk del curl")
    v = score_surgical_edit(fenced(body), s)
    assert len(v) == 1 and v[0].startswith("changes beyond the requested edit"), v


def test_inserted_lines_may_use_any_indentation():
    """The prompt says 'indented like the lines around it', which is a style
    call; the check is that nothing EXISTING moved."""
    s = spec("homelab_pip_removal")
    body = expected_of("homelab_pip_removal").replace(
        "\n    python3 -m pip uninstall -y pip", "\n        python3 -m pip uninstall -y pip")
    assert body != expected_of("homelab_pip_removal")
    assert score_surgical_edit(fenced(body), s) == []


def test_rewording_an_inserted_line_is_a_violation():
    """Indentation of an inserted line is free; its text is not."""
    s = spec("homelab_pip_removal")
    body = expected_of("homelab_pip_removal").replace(
        "python3 -m pip uninstall -y pip", "pip uninstall -y pip")
    v = score_surgical_edit(fenced(body), s)
    assert len(v) == 1 and "+pip uninstall -y pip" in v[0], v


def test_trailing_whitespace_is_ignored():
    s = spec("kroger_pip_removal")
    body = expected_of("kroger_pip_removal").replace("\n", "  \n", 3)
    assert score_surgical_edit(fenced(body), s) == []


def test_dropping_the_requested_change_is_a_violation():
    s = spec("homelab_pip_removal")
    body = expected_of("homelab_pip_removal").replace("    python3 -m pip uninstall -y pip\n", "")
    v = score_surgical_edit(fenced(body), s)
    assert len(v) == 1 and v[0].startswith("changes beyond the requested edit"), v


def ot_good():
    return surgical_edit_original(spec("open_terminal_constraint")).replace(
        "pypdf csvkit\n", 'pypdf csvkit \\\n    "sigstore>=4.1.1"\n', 1)


def test_token_mode_accepts_the_token_on_the_same_line_too():
    s = spec("open_terminal_constraint")
    assert score_surgical_edit(fenced(ot_good()), s) == []
    same_line = surgical_edit_original(s).replace(
        "pip install --no-cache-dir . \\", 'pip install --no-cache-dir . "sigstore>=4.1.1" \\', 1)
    assert score_surgical_edit(fenced(same_line), s) == []


def test_token_mode_missing_token_is_a_violation():
    s = spec("open_terminal_constraint")
    body = surgical_edit_original(s).replace("pypdf csvkit\n", "pypdf csvkit sigstore\n", 1)
    v = score_surgical_edit(fenced(body), s)
    assert "missing sigstore>=4.1.1" in v


def test_token_mode_token_twice_is_a_violation():
    s = spec("open_terminal_constraint")
    body = ot_good().replace(
        "pip install --no-cache-dir . \\", 'pip install --no-cache-dir . "sigstore>=4.1.1" \\', 1)
    v = score_surgical_edit(fenced(body), s)
    assert "sigstore>=4.1.1 added 2 times" in v


def test_token_mode_token_outside_a_pip_install_is_a_violation():
    s = spec("open_terminal_constraint")
    body = surgical_edit_original(s).replace(
        "\nRUN useradd", '\n# "sigstore>=4.1.1"\nRUN useradd', 1)
    v = score_surgical_edit(fenced(body), s)
    assert "sigstore>=4.1.1 is not inside a line matching the token_context" in v


def test_unknown_validate_keys_fail_loudly():
    s = spec("homelab_pip_removal")
    del s["validate"]["expected"]
    with pytest.raises(ValueError):
        score_surgical_edit(fenced("x\n"), s)


# --------------------------------------------- through the runner, not around it

@pytest.mark.parametrize("name,model,clean", [
    ("kroger_pip_removal", "qwen3.6", True),
    ("kroger_pip_removal", "coder-next", False),
])
def test_run_once_reports_surgical_edit_violations_as_schema_violations(monkeypatch, name, model, clean):
    reply = capture(name, model)
    monkeypatch.setattr(run_benchmark, "ollama_chat",
                        lambda url, payload: {"message": {"content": reply}, "eval_count": 1})
    r = run_benchmark.run_once(spec(name), "http://ollama.invalid", "m")
    assert (r["schema_violations"] == []) is clean, r["schema_violations"]


def test_surgical_edit_group_selects_exactly_these_payloads():
    got = sorted(s["name"] for s in run_benchmark.load_payloads("surgical_edit"))
    assert got == [f"surgical_edit_{n}" for n in NAMES]
