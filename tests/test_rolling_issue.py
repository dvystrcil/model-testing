#!/usr/bin/env python3
"""Pure-function tests for bin/rolling-issue.py.

The decision table is the whole point: a lane that only ever opens issues
turns detection into noise, so a healthy result must close what is open.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
_s = importlib.util.spec_from_file_location("ri", BIN / "rolling-issue.py")
ri = importlib.util.module_from_spec(_s)
sys.modules["ri"] = ri
_s.loader.exec_module(ri)


class DecideTest(unittest.TestCase):
    def test_breach_with_nothing_open_creates(self):
        self.assertEqual(ri.decide([], breached=True), ("create", None))

    def test_breach_with_issue_open_updates_it(self):
        self.assertEqual(ri.decide([7], breached=True), ("update", 7))

    def test_healthy_closes_the_open_issue(self):
        self.assertEqual(ri.decide([7], breached=False), ("close", 7))

    def test_healthy_with_nothing_open_does_nothing(self):
        self.assertEqual(ri.decide([], breached=False), ("none", None))

    def test_several_open_acts_on_the_oldest(self):
        self.assertEqual(ri.decide([12, 7, 9], breached=True), ("update", 7))


if __name__ == "__main__":
    unittest.main()
