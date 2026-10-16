#!/usr/bin/env python3
"""Hermetic tests for bin/owui-visual-tools-eval.py (model-testing#126).

No ollama, no network, no browser. The model is a scripted fake that replays
the exact shapes seen on 2026-10-16 (homelab#981): a clean pass, a model that
writes the visual without calling the tool, nemotron's leaked DSML call
syntax, qwen3-coder-next calling visualize() five times and never writing
content, and a model calling a tool on a non-visual question.
"""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
_s = importlib.util.spec_from_file_location("ovt", BIN / "owui-visual-tools-eval.py")
ovt = importlib.util.module_from_spec(_s)
sys.modules["ovt"] = ovt
_s.loader.exec_module(ovt)

# Minimal stand-ins with the same surface as the real upstream files: the
# imports the harness must stub, the docstrings it must read, the builders
# it must call.
VIZ_SRC = '''
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
def _build_html(security_level="strict", title="Visualization", lang="en", chime=True):
    return "<!DOCTYPE html><html data-iv-build=\\"9.9.9\\"><body>" + title + "</body></html>"
class Tools:
    async def visualize(self, title: str = "Visualization", __event_emitter__=None):
        """
        Call this before every visualization.
        :param title: Short title.
        """
'''
OPENUI_SRC = '''
from starlette.responses import HTMLResponse
from pydantic import BaseModel, Field
_CDN_BASE = "https://cdn.jsdelivr.net/npm/@openuidev/browser-bundle@latest"
def _build_openui_html(code, title="Response", cdn_base=_CDN_BASE, theme="light"):
    return "<html><body data-cdn=\\"" + cdn_base + "\\">" + code + "</body></html>"
class Tools:
    async def render_openui(self, openui_lang_code: str, title: str = "Response"):
        """
        Render interactive UI components inline in the chat using OpenUI Lang.
        :param openui_lang_code: the program
        """
'''
SKILL = "# Inline Visualizer handbook\nUse @@@VIZ-START / @@@VIZ-END."

CHART = {"name": "chart", "positive": True, "prompt": "Make a bar chart: Jan 12, Feb 9.",
         "expect": ["Jan", "Feb"]}
NEGATIVE = {"name": "negative", "positive": False, "prompt": "Deployment vs StatefulSet?", "expect": []}

GOOD_FRAGMENT = ('<svg viewBox="0 0 680 240"><rect x="10" y="10" width="40" height="80"/>'
                 '<text x="10" y="120">Jan</text><text x="60" y="120">Feb</text></svg>')


def tc(fn, **args):
    return {"function": {"name": fn, "arguments": args}}


def scripted(*turns):
    """A fake ollama /api/chat: returns the scripted assistant messages in order."""
    seq = list(turns)
    seen = []

    def chat(model, messages, tools):
        seen.append({"model": model, "messages": [dict(m) for m in messages], "tools": tools})
        return seq.pop(0) if seq else {"role": "assistant", "content": ""}
    chat.seen = seen
    return chat


def sources():
    return {"visualizer": {"tool": VIZ_SRC, "skill": SKILL}, "openui": {"tool": OPENUI_SRC}}


class TestSourceLock(unittest.TestCase):
    def test_matching_sha_returns_text(self):
        body = b"print('hi')\n"
        entry = {"repo": "o/r", "commit": "abc", "path": "tool.py", "sha256": hashlib.sha256(body).hexdigest()}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ovt.fetch_source(entry, d, fetch=lambda url: body), "print('hi')\n")

    def test_sha_mismatch_fails_closed(self):
        entry = {"repo": "o/r", "commit": "abc", "path": "tool.py", "sha256": "0" * 64}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ovt.SourceMismatch):
                ovt.fetch_source(entry, d, fetch=lambda url: b"tampered")

    def test_fetch_url_is_pinned_to_the_commit(self):
        urls = []
        body = b"x"
        entry = {"repo": "Classic298/open-webui-plugins", "commit": "13490d6", "path": "inline-visualizer-v2/tool.py",
                 "sha256": hashlib.sha256(body).hexdigest()}
        with tempfile.TemporaryDirectory() as d:
            ovt.fetch_source(entry, d, fetch=lambda url: urls.append(url) or body)
        self.assertEqual(urls, ["https://raw.githubusercontent.com/Classic298/open-webui-plugins/13490d6/inline-visualizer-v2/tool.py"])

    def test_cached_copy_is_rechecked_not_trusted(self):
        body = b"good"
        entry = {"repo": "o/r", "commit": "c", "path": "t.py", "sha256": hashlib.sha256(body).hexdigest()}
        with tempfile.TemporaryDirectory() as d:
            ovt.fetch_source(entry, d, fetch=lambda url: body)
            cached = next(Path(d).rglob("t.py"))
            cached.write_text("edited in the cache")
            # A tampered cache is refetched, never silently used.
            self.assertEqual(ovt.fetch_source(entry, d, fetch=lambda url: body), "good")

    def test_shipped_lock_pins_every_tool_to_a_full_commit(self):
        lock = json.loads((Path(__file__).resolve().parent.parent / "benchmarks" / "owui-visual-tools"
                           / "tools.lock.json").read_text())
        for tool in ("visualizer", "openui"):
            for f in lock[tool]["files"].values():
                self.assertRegex(f["commit"], r"^[0-9a-f]{40}$")
                self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("@latest", lock["openui"]["cdn_base"])


class TestToolLoading(unittest.TestCase):
    def test_upstream_modules_load_without_owui_installed(self):
        ns = ovt.load_tool_module(OPENUI_SRC)
        self.assertIn("pinned", ns["_build_openui_html"]("root = Card([])", "t", "pinned"))

    def test_description_is_the_docstring_without_param_lines(self):
        self.assertEqual(ovt.tool_doc(VIZ_SRC, "visualize"), "Call this before every visualization.")

    def test_visualizer_offers_view_skill_and_visualize(self):
        specs = ovt.tool_specs("visualizer", sources()["visualizer"])
        self.assertEqual([s["function"]["name"] for s in specs], ["view_skill", "visualize"])

    def test_openui_offers_only_render_openui(self):
        specs = ovt.tool_specs("openui", sources()["openui"])
        self.assertEqual([s["function"]["name"] for s in specs], ["render_openui"])
        self.assertIn("openui_lang_code", specs[0]["function"]["parameters"]["required"])


class TestToolLoop(unittest.TestCase):
    def test_skill_result_is_the_shipped_skill(self):
        chat = scripted({"role": "assistant", "tool_calls": [tc("view_skill", name="visualize")]},
                        {"role": "assistant", "tool_calls": [tc("visualize", title="t")]},
                        {"role": "assistant", "content": "x"})
        ovt.run_case(chat, "visualizer", "m", CHART, sources()["visualizer"])
        tool_msgs = [m for m in chat.seen[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(tool_msgs[-1]["content"], SKILL)

    def test_unknown_skill_name_is_not_found(self):
        chat = scripted({"role": "assistant", "tool_calls": [tc("view_skill", name="charts")]},
                        {"role": "assistant", "content": ""})
        ovt.run_case(chat, "visualizer", "m", CHART, sources()["visualizer"])
        tool_msgs = [m for m in chat.seen[1]["messages"] if m["role"] == "tool"]
        self.assertIn("not found", tool_msgs[-1]["content"])

    def test_rounds_are_capped_and_recorded(self):
        loop = [{"role": "assistant", "tool_calls": [tc("visualize", title="t")]}] * 9
        rec = ovt.run_case(scripted(*loop), "visualizer", "m", CHART, sources()["visualizer"], max_rounds=5)
        self.assertEqual(rec["calls"], ["visualize"] * 5)
        self.assertTrue(rec["rounds_exhausted"])

    def test_openui_code_is_captured(self):
        chat = scripted({"role": "assistant", "tool_calls": [tc("render_openui", openui_lang_code="root = Card([])")]},
                        {"role": "assistant", "content": "Here it is."})
        rec = ovt.run_case(chat, "openui", "m", CHART, sources()["openui"])
        self.assertEqual(rec["code"], "root = Card([])")
        self.assertEqual(rec["final"], "Here it is.")


class TestVizScoring(unittest.TestCase):
    def rec(self, calls, final, exhausted=False):
        return {"tool": "visualizer", "calls": calls, "final": final, "rounds_exhausted": exhausted, "code": ""}

    def test_clean_pass(self):
        final = f"Here.\n\n@@@VIZ-START\n{GOOD_FRAGMENT}\n@@@VIZ-END\n\nDone."
        self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], final), CHART), ("pass", None))

    def test_markup_without_the_call_is_no_call(self):
        # qwen3.6:35b, 2026-10-16: read the skill, wrote the block, never called visualize().
        final = f"@@@VIZ-START\n{GOOD_FRAGMENT}\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["view_skill"], final), CHART)[0:2], ("fail", "NO_CALL"))

    def test_leaked_call_syntax_is_named(self):
        # nemotron-3.5-lightning, 2026-10-16.
        final = '<｜DSML｜function_calls>\n<｜DSML｜invoke name="visualize">\n</｜DSML｜function_calls>\n@@@VIZ-START\n<div>x</div>\n@@@VIZ-END'
        self.assertEqual(ovt.score(self.rec(["view_skill"] * 3, final), CHART)[1], "LEAKED_CALL_SYNTAX")

    def test_calls_that_never_produce_content_are_a_loop(self):
        # qwen3-coder-next, 2026-10-16: visualize() x5, no block.
        self.assertEqual(ovt.score(self.rec(["visualize"] * 5, "", exhausted=True), CHART)[1], "LOOP_NO_CONTENT")

    def test_skipping_the_skill_fails(self):
        final = f"@@@VIZ-START\n{GOOD_FRAGMENT}\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["visualize"], final), CHART)[1], "SKIPPED_SKILL")

    def test_code_fence_inside_block(self):
        final = f"@@@VIZ-START\n```html\n{GOOD_FRAGMENT}\n```\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], final), CHART)[1], "BAD_FRAGMENT")

    def test_full_document_instead_of_fragment(self):
        final = f"@@@VIZ-START\n<!DOCTYPE html><html><body>{GOOD_FRAGMENT}</body></html>\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], final), CHART)[1], "BAD_FRAGMENT")

    def test_two_blocks(self):
        b = f"@@@VIZ-START\n{GOOD_FRAGMENT}\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], b + "\n\n" + b), CHART)[1], "BAD_FRAGMENT")

    def test_unbalanced_tags(self):
        frag = '<div><div class="card"><span>Jan Feb</span></div>' + "x" * 80
        final = f"@@@VIZ-START\n{frag}\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], final), CHART)[1], "BAD_FRAGMENT")

    def test_script_waiting_for_page_load_never_runs(self):
        # qwen3.6:35b rerun, 2026-10-16: Chart.js init inside DOMContentLoaded. The
        # wrapper injects the fragment after that event has fired, so the chart
        # stays blank -- and every structural check still passed.
        for hook in ("document.addEventListener('DOMContentLoaded', () => {", "window.onload = function() {",
                     'window.addEventListener("load", () => {'):
            frag = GOOD_FRAGMENT + f"<script>{hook} draw(); }});</script>"
            final = f"@@@VIZ-START\n{frag}\n@@@VIZ-END"
            self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], final), CHART)[1], "WAITS_FOR_LOAD", hook)

    def test_load_words_outside_scripts_are_fine(self):
        frag = GOOD_FRAGMENT.replace("Jan</text>", "Jan</text><text>DOMContentLoaded</text>")
        final = f"@@@VIZ-START\n{frag}\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], final), CHART), ("pass", None))

    def test_expected_values_are_case_insensitive(self):
        # Scored a false failure on 2026-10-16: the model wrote "Harbor", the check wanted "harbor".
        case = dict(CHART, expect=["harbor"])
        frag = GOOD_FRAGMENT.replace("Jan", "Harbor")
        final = f"@@@VIZ-START\n{frag}\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], final), case), ("pass", None))

    def test_missing_values(self):
        case = dict(CHART, expect=["Mar"])
        final = f"@@@VIZ-START\n{GOOD_FRAGMENT}\n@@@VIZ-END"
        self.assertEqual(ovt.score(self.rec(["view_skill", "visualize"], final), case)[1], "MISSING_VALUES")

    def test_negative_with_a_call_fails(self):
        self.assertEqual(ovt.score(self.rec(["visualize"], ""), NEGATIVE)[1], "CALLED_ON_NEGATIVE")

    def test_negative_without_a_call_passes(self):
        self.assertEqual(ovt.score(self.rec([], "A Deployment is..."), NEGATIVE), ("pass", None))


class TestOpenUIScoring(unittest.TestCase):
    def rec(self, code, visible=None, render="ok"):
        return {"tool": "openui", "calls": ["render_openui"] if code else [], "final": "", "code": code,
                "rounds_exhausted": False, "render": render, "visible_text": visible}

    def test_rendered_values_pass(self):
        self.assertEqual(ovt.score(self.rec("root = Card([])", "Monthly JAN feb"), CHART), ("pass", None))

    def test_no_call(self):
        self.assertEqual(ovt.score(self.rec(""), CHART)[1], "NO_CALL")

    def test_silent_partial_render_is_missing_values(self):
        # qwen3-coder-next, 2026-10-16: invented functions; only the title rendered, no error.
        self.assertEqual(ovt.score(self.rec("root = Card([t, grid])", "Service Status Dashboard"), CHART)[1],
                         "MISSING_VALUES")

    def test_unrendered_is_never_a_pass(self):
        self.assertEqual(ovt.score(self.rec("root = Card([])", None, render="skipped"), CHART),
                         ("unscored", "RENDER_SKIPPED"))


class TestRescore(unittest.TestCase):
    def test_rescore_applies_current_rules_to_stored_records(self):
        frag = GOOD_FRAGMENT + "<script>document.addEventListener('DOMContentLoaded', () => {});</script>"
        stored = [
            {"tool": "visualizer", "model": "m", "case": "chart", "calls": ["view_skill", "visualize"],
             "final": f"@@@VIZ-START\n{frag}\n@@@VIZ-END", "rounds_exhausted": False, "code": "", "secs": 9,
             "verdict": "pass", "reason": None, "render": "ok"},
            {"tool": "openui", "model": "m", "case": "chart", "calls": ["render_openui"], "final": "",
             "rounds_exhausted": False, "code": "root = Card([])", "secs": 3, "verdict": "pass", "reason": None,
             "render": "ok", "visible_text": "Jan Feb"},
        ]
        out = ovt.rescore(stored, {"chart": CHART})
        self.assertEqual([(r["verdict"], r["reason"]) for r in out], [("fail", "WAITS_FOR_LOAD"), ("pass", None)])
        self.assertEqual(out[0]["rescored_from"], {"verdict": "pass", "reason": None})
        self.assertNotIn("rescored_from", out[1])

    def test_errored_records_stay_errors(self):
        stored = [{"tool": "openui", "model": "m", "case": "chart", "calls": [], "final": "", "code": "",
                   "rounds_exhausted": False, "secs": 0, "verdict": "fail", "reason": "ERROR", "error": "timeout"}]
        self.assertEqual(ovt.rescore(stored, {"chart": CHART})[0]["reason"], "ERROR")


class TestPages(unittest.TestCase):
    def test_viz_mock_chat_holds_the_wrapper_and_the_reply(self):
        page = ovt.viz_chat_page("<html data-iv-build=\"2.2.3\"></html>", "Here.\n@@@VIZ-START\n<b>x</b>\n@@@VIZ-END")
        self.assertIn('class="chat-assistant"', page)
        self.assertIn('id="message-t1-embeds-0"', page)
        self.assertIn("allow-same-origin", page)
        self.assertIn("data-iv-build=&quot;2.2.3&quot;", page)       # wrapper escaped into srcdoc
        self.assertIn("@@@VIZ-START\n&lt;b&gt;x&lt;/b&gt;", page)    # reply escaped as chat text

    def test_dom_text_drops_scripts_and_styles(self):
        dom = "<html><style>.a{}</style><script>var Jan=1</script><body><p>Feb</p></body></html>"
        self.assertEqual(ovt.dom_text(dom).split(), ["Feb"])


class TestSummary(unittest.TestCase):
    def test_scoreboard_rows_and_counts(self):
        recs = [
            {"tool": "visualizer", "model": "m1", "case": "chart", "verdict": "pass", "reason": None, "secs": 30},
            {"tool": "openui", "model": "m1", "case": "chart", "verdict": "fail", "reason": "NO_CALL", "secs": 5},
            {"tool": "openui", "model": "m1", "case": "negative", "verdict": "pass", "reason": None, "secs": 2},
        ]
        md = ovt.summary_md(recs)
        self.assertIn("| m1 |", md)
        self.assertIn("fail NO_CALL", md)
        self.assertIn("visualizer: 1/1 pass", md)
        self.assertIn("openui: 1/2 pass", md)

    def test_marker_counts_unscored_separately(self):
        recs = [{"verdict": "pass"}, {"verdict": "unscored"}, {"verdict": "fail"}]
        self.assertEqual(ovt.marker(recs), "OWUI-VISUAL-TOOLS-COMPLETE records=3 pass=1 fail=1 unscored=1")


if __name__ == "__main__":
    unittest.main()
