"""TDD for the head-to-head scorer (model-testing#20).

Five synthetic sides with known properties prove the scoring logic responds
correctly BEFORE it is used to judge real models. Runs under pytest, or directly:
    python benchmarks/head-to-head/tests/test_scoring.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import score  # noqa: E402


def row(task, output, side="opencode", run_n=1, latency=10.0, itok=100):
    return {
        "task_id": task, "side": side, "run_n": run_n,
        "latency_seconds": latency, "input_tokens": itok,
        "output_tokens": max(1, len(output) // 4), "raw_output": output, "scores": {},
    }


def const(v):
    return lambda task, dim: v


PERFECT_ISSUE = """\
## Background
Cost optionality per homelab#292.
## Pieces
1. budget store  2. request-time enforcement filter  3. admin view
## Acceptance Criteria
- [ ] AC1: InfisicalSecret-backed budget key; spend accrues per request via a Pipeline filter.
- [ ] AC2: over-cap requests rejected 402.
- [ ] AC3: admin usage view.
"""

GENERIC_XNACK = ("XNACK lets the GPU handle page faults by retrying memory "
                 "accesses. It is generally enabled via kernel flags.")
MEMORY_XNACK = ("SD WebUI is scaled to 0 -- XNACK alone was insufficient, PyTorch "
                "on gfx1151 still segfaults. Canonical param is amdgpu.noretry=0.")

CLEAN_COMMIT = "Add retry to webhook notifier\n\nWhy: transient 5xx dropped alerts."
CHATTY_COMMIT = ("Sure! I'd be happy to help you write a great commit message for "
                 "this wonderful change. " * 20) + "\nAdd retry to webhook notifier"

CLEAN_DIARY = ("I defaulted to the shortcut twice today even with the rule loaded. "
               "Noting the pull toward the simpler path under time pressure.")
CELEBRATORY_DIARY = ("🎉 Great progress today! I'm so proud of how much we crushed it. "
                     "Amazing work all around!")


def test_perfect_side_maxes_every_dimension():
    rows = score.score_rows([row("01-issue-scoping", PERFECT_ISSUE)], const(3))
    s = rows[0]["scores"]
    assert s["format"] == 3
    assert s["memory"] == 3
    assert s["correctness"] == 3 and s["faithfulness"] == 3


def test_broken_side_bottoms_out():
    rows = score.score_rows([row("01-issue-scoping", "I don't know.", latency=0.1)], const(0))
    s = rows[0]["scores"]
    assert s["format"] == 0
    assert s["memory"] == 0
    assert s["correctness"] == 0
    assert s["latency"] <= 0.2
    assert rows[0]["output_tokens"] <= 5


def test_chatty_side_drops_structured_format_and_inflates_tokens():
    clean = score.score_rows([row("11-commit-message", CLEAN_COMMIT)], const(2))[0]
    chatty = score.score_rows([row("11-commit-message", CHATTY_COMMIT)], const(2))[0]
    assert clean["scores"]["format"] == 3
    assert chatty["scores"]["format"] == 0          # preamble breaks the title check
    assert chatty["output_tokens"] > clean["output_tokens"]
    assert chatty["scores"]["correctness"] == clean["scores"]["correctness"]  # human dim unaffected


def test_memory_bypassing_side_zeros_memory_only():
    using = score.score_rows([row("13-memory-qa", MEMORY_XNACK)], const(2))[0]
    bypass = score.score_rows([row("13-memory-qa", GENERIC_XNACK)], const(2))[0]
    assert using["scores"]["memory"] == 3
    assert bypass["scores"]["memory"] == 0
    assert bypass["scores"]["correctness"] == using["scores"]["correctness"]  # unaffected


def test_celebratory_side_drops_voice_only():
    plain = score.score_rows([row("16-diary-entry", CLEAN_DIARY)], const(2))[0]
    party = score.score_rows([row("16-diary-entry", CELEBRATORY_DIARY)], const(2))[0]
    assert plain["scores"]["voice"] == 3
    assert party["scores"]["voice"] == 0
    assert party["scores"]["correctness"] == plain["scores"]["correctness"]  # unaffected


def test_aggregate_flags_high_variance():
    rows = [
        row("06-tdd-design", "x == 1\n" * 6, run_n=1),
        row("06-tdd-design", "no assertions here", run_n=2),
    ]
    score.score_rows(rows, const(0))
    # force a >1 spread on a human dim across runs
    rows[0]["scores"]["correctness"] = 3
    rows[1]["scores"]["correctness"] = 0
    agg = score.aggregate(rows)
    assert any(v[2] == "correctness" for v in agg["variance"])


ALL = [v for k, v in list(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in ALL:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n# suite summary: {len(ALL) - failures}/{len(ALL)} passed")
    sys.exit(1 if failures else 0)
