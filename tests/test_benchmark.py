#!/usr/bin/env python3
"""Unit tests for run_benchmark.py grading logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
from run_benchmark import grade, strip_think, stddev


def test_grade_all_hits():
    g = grade("The token expires in 15 min and 7 day", ["15 min", "7 day"], [])
    assert g["facts_score"] == 1.0
    assert g["hits"] == ["15 min", "7 day"]
    assert g["misses"] == []
    assert g["violations"] == []


def test_grade_partial_hits():
    g = grade("The token expires in 15 min", ["15 min", "7 day"], [])
    assert g["facts_score"] == 0.5
    assert "7 day" in g["misses"]


def test_grade_no_facts():
    g = grade("some response", [], [])
    assert g["facts_score"] == 1.0


def test_grade_forbidden_violation():
    g = grade("wallos-iu config here", ["filebrowser"], ["wallos-iu"])
    assert "wallos-iu" in g["violations"]


def test_grade_no_forbidden_violation():
    g = grade("filebrowser config here", ["filebrowser"], ["wallos-iu"])
    assert g["violations"] == []
    assert g["facts_score"] == 1.0


def test_grade_case_insensitive():
    g = grade("FILEBROWSER config", ["filebrowser"], ["WALLOS"])
    assert g["facts_score"] == 1.0
    assert g["hits"] == ["filebrowser"]


def test_strip_think_removes_block():
    text = "<think>internal reasoning here</think>Final answer"
    assert strip_think(text) == "Final answer"


def test_strip_think_multiline():
    text = "<think>\nline1\nline2\n</think>\nActual output"
    assert strip_think(text) == "Actual output"


def test_strip_think_no_block():
    text = "No thinking here"
    assert strip_think(text) == "No thinking here"


def test_stddev_single():
    assert stddev([42.0]) == 0.0


def test_stddev_uniform():
    assert stddev([5.0, 5.0, 5.0]) == 0.0


def test_stddev_known():
    # sample stddev (n-1): sqrt(32/7) ≈ 2.138
    result = stddev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert abs(result - 2.138) < 0.01


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
