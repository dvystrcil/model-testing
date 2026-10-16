#!/usr/bin/env python3
"""Benchmark OWUI's in-chat rendering tools against local models (model-testing#126).

Asks: given a visual request, does this model drive this rendering tool well
enough that the user sees the right picture? Built for homelab#981, which had
to choose between Inline Visualizer (Classic298) and OpenUI - Generative UI
(thesysdev), and has to be re-asked whenever a new model arrives.

Three tiers, cheapest first:

  1. protocol   the model runs OWUI's native tool loop over the ollama API,
                with each tool's own docstring (and, for the visualizer, its
                own SKILL.md and result text). Scored mechanically.
  2. render     the output goes through the TOOL'S OWN rendering code (the
                visualizer's 2.x wrapper inside a same-origin mock chat;
                OpenUI's _build_openui_html) and chrome-headless-shell
                screenshots it. OpenUI is scored on the rendered DOM.
  3. real UI    benchmarks/owui-visual-tools/e2e/chat.js drives the actual
                OWUI chat in a browser. Run by hand; see the README there.

  bin/owui-visual-tools-eval.py --ollama http://127.0.0.1:11434 \\
      --models qwen3.6:35b,qwen3.8:27B --tools visualizer,openui \\
      --report benchmarks/reports/owui-visual-tools/<date>-<slug>

Writes <report>/raw/results.jsonl, <report>/shots/*.png and
<report>/summary.md. Upstream tool sources are fetched at the commits pinned
in benchmarks/owui-visual-tools/tools.lock.json and sha256-checked; a
mismatch stops the run.

Verdicts: pass | fail (with a reason code) | unscored (OpenUI output that
could not be rendered: a check that could not look must not pass).
Screenshots are for a human: "renders" is not "looks right", and the
2026-10-16 run found a chart with wrong bar heights that passed every
mechanical check. Review the shots before drawing conclusions.

Last line: OWUI-VISUAL-TOOLS-COMPLETE records=N pass=P fail=F unscored=U
"""
import argparse
import ast
import functools
import glob
import hashlib
import html
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmarks" / "owui-visual-tools"
TOOLS = ("visualizer", "openui")


class SourceMismatch(Exception):
    pass


# --- pinned upstream sources --------------------------------------------------

def _http_get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def fetch_source(entry, cache_dir, fetch=_http_get):
    """Text of one pinned upstream file, sha256-verified.

    The cache is a convenience, not a source of truth: a cached copy that no
    longer matches the lock is refetched, and a fresh fetch that does not
    match raises. A benchmark that silently tested different code than the
    report names would be worse than no benchmark.
    """
    path = Path(cache_dir) / entry["repo"].replace("/", "__") / entry["commit"] / entry["path"]
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]:
        return path.read_text()
    url = f"https://raw.githubusercontent.com/{entry['repo']}/{entry['commit']}/{entry['path']}"
    body = fetch(url)
    got = hashlib.sha256(body).hexdigest()
    if got != entry["sha256"]:
        raise SourceMismatch(f"{url}: sha256 {got} != pinned {entry['sha256']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body.decode()


def load_sources(lock, tools, cache_dir, fetch=_http_get):
    return {t: {k: fetch_source(e, cache_dir, fetch) for k, e in lock[t]["files"].items()} for t in tools}


# --- loading the tools' own code ----------------------------------------------

def _stub_modules():
    """The upstream files import OWUI's web stack only for their Tools class.
    The harness calls module-level builders, so empty stand-ins suffice."""
    resp = types.ModuleType("responses")
    resp.HTMLResponse = object
    web = types.ModuleType("web")
    web.responses = resp
    pyd = types.ModuleType("pydantic")
    pyd.BaseModel = type("BaseModel", (), {})
    pyd.Field = lambda *a, **k: None
    return {"fastapi": web, "fastapi.responses": resp, "starlette": web,
            "starlette.responses": resp, "pydantic": pyd}


def load_tool_module(src):
    saved = {k: sys.modules.get(k) for k in _stub_modules()}
    sys.modules.update(_stub_modules())
    try:
        ns = {"__name__": "owui_tool"}
        exec(compile(src, "owui_tool.py", "exec"), ns)
        return ns
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def tool_doc(src, fn_name):
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == fn_name)
    return ast.get_docstring(fn).split(":param")[0].strip()


VIZ_SYSTEM = ("You are a helpful assistant.\n\n<available_skills>\n- visualize: Render rich interactive visuals — "
              "SVG diagrams, HTML widgets, Chart.js charts, and interactive explainers — directly inline in chat "
              "using visualize(). Use only when the user explicitly asks for a visualization.\n</available_skills>\n"
              "Call view_skill(name) to read a skill before using it.")
OPENUI_SYSTEM = "You are a helpful assistant."

# visualize()'s own result text (2.2.3), so the model is told exactly what OWUI tells it.
VIZ_RESULT = ('Visualization wrapper "{t}" is mounted and waiting for content. Now emit the HTML/SVG in your NEXT '
              'text response wrapped in the TEXT delimiters @@@VIZ-START and @@@VIZ-END, each on their own line, '
              'with ONE EMPTY LINE after @@@VIZ-END before any follow-up text (otherwise markdown in that text will '
              'not render). The wrapper will tail your stream and render live. These are PLAIN TEXT markers — NOT a '
              '``` code fence, NOT HTML tags, NOT a ::: fence. Example:\n\n    @@@VIZ-START\n    <svg viewBox="0 0 '
              '680 240">…</svg>\n    @@@VIZ-END\n\nWrite explanatory prose BEFORE and AFTER the block — do not '
              'describe the HTML source itself. Emit exactly ONE @@@VIZ-START/@@@VIZ-END pair for this tool call.')


def tool_specs(tool, src):
    if tool == "visualizer":
        return [
            {"type": "function", "function": {
                "name": "view_skill", "description": "Read the full content of an available skill by name.",
                "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "skill name"}},
                               "required": ["name"]}}},
            {"type": "function", "function": {
                "name": "visualize", "description": tool_doc(src["tool"], "visualize"),
                "parameters": {"type": "object", "properties": {
                    "title": {"type": "string", "description": "Short descriptive title for the visualization."}}}}},
        ]
    return [{"type": "function", "function": {
        "name": "render_openui", "description": tool_doc(src["tool"], "render_openui"),
        "parameters": {"type": "object", "properties": {
            "openui_lang_code": {"type": "string", "description": "OpenUI Lang program"},
            "title": {"type": "string"}}, "required": ["openui_lang_code"]}}}]


# --- the tool loop ------------------------------------------------------------

def ollama_chat(base, num_ctx=32768, timeout=900):
    def chat(model, messages, tools):
        body = json.dumps({"model": model, "messages": messages, "tools": tools, "stream": False,
                           "options": {"num_ctx": num_ctx}}).encode()
        req = urllib.request.Request(f"{base}/api/chat", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["message"]
    return chat


def run_case(chat, tool, model, case, src, max_rounds=5):
    """One model x one tool x one prompt through OWUI's native tool loop."""
    msgs = [{"role": "system", "content": VIZ_SYSTEM if tool == "visualizer" else OPENUI_SYSTEM},
            {"role": "user", "content": case["prompt"]}]
    specs = tool_specs(tool, src)
    calls, code, exhausted = [], "", True
    t0 = time.time()
    for _ in range(max_rounds):
        m = chat(model, msgs, specs)
        msgs.append(m)
        tcs = m.get("tool_calls") or []
        if not tcs:
            exhausted = False
            break
        for tc in tcs:
            fn = tc["function"]["name"]
            args = tc["function"].get("arguments") or {}
            calls.append(fn)
            if fn == "view_skill":
                out = src["skill"] if str(args.get("name", "")).strip().lower() == "visualize" else "skill not found"
            elif fn == "visualize":
                out = VIZ_RESULT.format(t=args.get("title", "Visualization"))
            elif fn == "render_openui":
                code = code or args.get("openui_lang_code", "")
                out = "Rendered successfully."
            else:
                out = f'Tool "{fn}" not found'
            msgs.append({"role": "tool", "tool_name": fn, "content": out})
    final = msgs[-1].get("content") or "" if msgs[-1].get("role") == "assistant" else ""
    return {"tool": tool, "model": model, "case": case["name"], "calls": calls, "code": code,
            "final": final, "rounds_exhausted": exhausted, "secs": round(time.time() - t0)}


# --- scoring ------------------------------------------------------------------

_VIZ_BLOCK = re.compile(r"^@@@VIZ-START[ \t]*\n(.*?)\n@@@VIZ-END[ \t]*$", re.S | re.M)
# Tool-call syntax written into the reply instead of emitted as a call
# (nemotron's DSML, 2026-10-16; the same shape as raw <tool_call> tags).
_LEAK = re.compile(r"<｜DSML｜|<\|?tool_call\|?>|<\|start\|>|\[TOOL_CALLS\]")
# The wrapper injects the fragment into a document that has already loaded,
# so code waiting for the load event never runs: a blank chart that passes
# every structural check (qwen3.6:35b, 2026-10-16 rerun).
_WAITS_FOR_LOAD = re.compile(r"DOMContentLoaded|\bwindow\.onload\b|addEventListener\(\s*['\"]load['\"]")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
         "path", "circle", "rect", "line", "polyline", "polygon", "ellipse", "stop", "use"}


def _unbalanced(frag):
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", frag, flags=re.S | re.I)
    stack, stray = [], []
    for close, tag, selfclose in re.findall(r"<(/?)([a-zA-Z][\w:-]*)[^>]*?(/?)>", body):
        tag = tag.lower()
        if tag in _VOID or selfclose:
            continue
        if not close:
            stack.append(tag)
        elif stack and stack[-1] == tag:
            stack.pop()
        elif tag in stack:
            while stack.pop() != tag:
                pass
        else:
            stray.append(tag)
    return stray, stack


def check_fragment(final):
    blocks = _VIZ_BLOCK.findall(final)
    if len(blocks) != 1:
        return f"expected 1 @@@VIZ block, found {len(blocks)}"
    frag = blocks[0]
    if re.search(r"^\s*(```|~~~|:::)", frag, re.M):
        return "code fence inside the block"
    if re.search(r"<!DOCTYPE|<html|<body|<head", frag, re.I):
        return "full document, not a fragment"
    if len(frag.strip()) < 80:
        return "fragment too small"
    stray, open_ = _unbalanced(frag)
    if stray or open_:
        return f"unbalanced tags: stray {stray[:3]} open {open_[:3]}"
    return None


def _missing(expect, text):
    low = (text or "").lower()
    return [e for e in expect if e.lower() not in low]


def score(rec, case):
    """(verdict, reason) — reason is a stable code, None on pass."""
    called = rec["calls"]
    if not case["positive"]:
        target = "visualize" if rec["tool"] == "visualizer" else "render_openui"
        return ("fail", "CALLED_ON_NEGATIVE") if target in called else ("pass", None)

    if rec["tool"] == "visualizer":
        final = rec["final"]
        if _LEAK.search(final):
            return "fail", "LEAKED_CALL_SYNTAX"
        if "visualize" not in called:
            return "fail", "NO_CALL"
        if not _VIZ_BLOCK.search(final) and rec.get("rounds_exhausted"):
            return "fail", "LOOP_NO_CONTENT"
        if "view_skill" not in called:
            return "fail", "SKIPPED_SKILL"
        if check_fragment(final):
            return "fail", "BAD_FRAGMENT"
        frag = _VIZ_BLOCK.search(final).group(1)
        scripts = " ".join(re.findall(r"<script[^>]*>(.*?)</script>", frag, re.S | re.I))
        if _WAITS_FOR_LOAD.search(scripts):
            return "fail", "WAITS_FOR_LOAD"
        if _missing(case["expect"], _VIZ_BLOCK.search(final).group(1)):
            return "fail", "MISSING_VALUES"
        return "pass", None

    if not rec["code"]:
        return "fail", "NO_CALL"
    if rec.get("render") != "ok":
        return "unscored", "RENDER_SKIPPED"
    if _missing(case["expect"], rec.get("visible_text")):
        return "fail", "MISSING_VALUES"
    return "pass", None


def detail(rec, case):
    if rec["tool"] == "visualizer" and rec["verdict"] == "fail" and rec["reason"] == "BAD_FRAGMENT":
        return check_fragment(rec["final"])
    if rec["reason"] == "MISSING_VALUES":
        text = rec.get("visible_text") if rec["tool"] == "openui" else (_VIZ_BLOCK.search(rec["final"]) or ["", ""])[1]
        return f"missing {_missing(case['expect'], text)}"
    return None


def rescore(records, cases_by_name):
    """Re-apply the current rules to stored records without re-running models.

    Rules improve after runs (WAITS_FOR_LOAD was found by looking at a
    screenshot). A changed verdict keeps the old one in `rescored_from`, so
    the report shows what moved and why. OpenUI is rescored from its stored
    `visible_text`; errored records stay errors.
    """
    out = []
    for r in records:
        r = dict(r)
        if r.get("reason") != "ERROR":
            new = score(r, cases_by_name[r["case"]])
            if new != (r.get("verdict"), r.get("reason")):
                r["rescored_from"] = {"verdict": r.get("verdict"), "reason": r.get("reason")}
                r["verdict"], r["reason"] = new
                r["detail"] = detail(r, cases_by_name[r["case"]])
        out.append(r)
    return out


# --- rendering ----------------------------------------------------------------

def viz_chat_page(wrapper_html, final, height=620):
    """A mock OWUI assistant message: the visualizer's own wrapper in a
    same-origin iframe inside the embeds container, followed by the model's
    reply as chat text. The wrapper's observer finds the @@@VIZ block in its
    parent exactly as it does in OWUI. Must be served over http: file://
    origins are opaque and the wrapper cannot reach its parent."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{font-family:system-ui;margin:0;padding:12px;background:#fff;width:760px}}
iframe{{width:100%;border:0;height:{height}px}}
#response-content-container{{white-space:pre-wrap;font-size:13px;color:#444}}
</style></head><body>
<div class="chat-assistant" id="message-t1">
 <div id="message-t1-embeds-container"><div id="message-t1-embeds-0">
  <iframe sandbox="allow-scripts allow-same-origin allow-popups" srcdoc="{html.escape(wrapper_html, quote=True)}"></iframe>
 </div></div>
 <div id="response-content-container"><p>{html.escape(final)}</p></div>
</div></body></html>"""


def dom_text(dom):
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", dom, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", body))


def find_chrome(explicit=None):
    if explicit:
        return explicit
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    hits = sorted(glob.glob(os.path.expanduser(
        "~/.cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")))
    return hits[-1] if hits else None


class _Server:
    def __init__(self, root):
        handler = functools.partial(_QuietHandler, directory=str(root))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _chrome(chrome, args, timeout=120):
    return subprocess.run([chrome, "--no-sandbox", "--hide-scrollbars", "--virtual-time-budget=15000", *args],
                          capture_output=True, text=True, timeout=timeout)


def render(rec, src, cdn_base, report, server, chrome):
    """Screenshot the output through the tool's own code. Sets rec['render']."""
    name = f"{rec['tool']}-{re.sub(r'[^A-Za-z0-9._-]', '_', rec['model'])}-{rec['case']}"
    shots = Path(report) / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    if rec["tool"] == "visualizer":
        if "@@@VIZ-START" not in rec["final"] or "visualize" not in rec["calls"]:
            rec["render"] = "nothing-mounted"   # no frame in OWUI either: the user sees the raw text
            return
        ns = load_tool_module(src["tool"])
        page = viz_chat_page(ns["_build_html"]("strict", rec["case"], "en", chime=False), rec["final"])
    else:
        if not rec["code"]:
            rec["render"] = "nothing-mounted"
            return
        ns = load_tool_module(src["tool"])
        try:
            page = ns["_build_openui_html"](rec["code"], rec["case"], cdn_base, "light")
        except TypeError:   # pre-0.5 signature has no theme
            page = ns["_build_openui_html"](rec["code"], rec["case"], cdn_base)
    (shots / f"{name}.html").write_text(page)
    if not chrome:
        rec["render"] = "skipped"
        return
    url = f"http://127.0.0.1:{server.port}/shots/{name}.html"
    _chrome(chrome, ["--window-size=784,700", f"--screenshot={shots / (name + '.png')}", url])
    if rec["tool"] == "openui":
        rec["visible_text"] = dom_text(_chrome(chrome, ["--dump-dom", url]).stdout)
    rec["screenshot"] = f"shots/{name}.png"
    rec["render"] = "ok" if (shots / f"{name}.png").exists() else "failed"


# --- reporting ----------------------------------------------------------------

def summary_md(recs):
    out = []
    for tool in TOOLS:
        rs = [r for r in recs if r["tool"] == tool]
        if not rs:
            continue
        cases = list(dict.fromkeys(r["case"] for r in rs))
        models = list(dict.fromkeys(r["model"] for r in rs))
        passed = sum(r["verdict"] == "pass" for r in rs)
        out += [f"## {tool}", "", f"{tool}: {passed}/{len(rs)} pass", "",
                "| model | " + " | ".join(cases) + " |", "|---|" + "---|" * len(cases)]
        for m in models:
            cells = []
            for c in cases:
                r = next((r for r in rs if r["model"] == m and r["case"] == c), None)
                cells.append("—" if r is None else
                             f"{r['verdict']}{' ' + r['reason'] if r['reason'] else ''} {r['secs']}s")
            out.append(f"| {m} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)


def marker(recs):
    n = {v: sum(r["verdict"] == v for r in recs) for v in ("pass", "fail", "unscored")}
    return f"OWUI-VISUAL-TOOLS-COMPLETE records={len(recs)} pass={n['pass']} fail={n['fail']} unscored={n['unscored']}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    ap.add_argument("--models", help="comma-separated ollama model ids (required unless --rescore)")
    ap.add_argument("--tools", default="visualizer,openui")
    ap.add_argument("--cases", default="", help="comma-separated case names (default: all)")
    ap.add_argument("--report", required=True, help="output directory")
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--no-render", action="store_true", help="protocol tier only; OpenUI is then unscored")
    ap.add_argument("--chrome", help="chrome-headless-shell path (default: $CHROME or the playwright cache)")
    ap.add_argument("--cache", default=os.path.expanduser("~/.cache/owui-visual-tools"))
    ap.add_argument("--rescore", action="store_true",
                    help="re-apply current rules to <report>/raw/results.jsonl; no model calls")
    a = ap.parse_args()
    if a.rescore:
        path = Path(a.report) / "raw" / "results.jsonl"
        cases_by_name = {c["name"]: c for c in json.loads((BENCH / "cases.json").read_text())["cases"]}
        recs = rescore([json.loads(l) for l in path.read_text().splitlines() if l.strip()], cases_by_name)
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        (Path(a.report) / "summary.md").write_text(summary_md(recs))
        for r in recs:
            if "rescored_from" in r:
                print(f"rescored  {r['tool']:10} {r['model']:40} {r['case']:10} "
                      f"{r['rescored_from']['verdict']} -> {r['verdict']} {r['reason'] or ''}")
        print(marker(recs))
        return 0
    if not a.models:
        ap.error("--models is required")

    lock = json.loads((BENCH / "tools.lock.json").read_text())
    cases = json.loads((BENCH / "cases.json").read_text())["cases"]
    if a.cases:
        want = a.cases.split(",")
        cases = [c for c in cases if c["name"] in want]
    tools = a.tools.split(",")
    src = load_sources(lock, tools, a.cache)
    chrome = None if a.no_render else find_chrome(a.chrome)
    if not a.no_render and not chrome:
        print("::warning::no chrome-headless-shell found — rendering skipped, OpenUI will be unscored", file=sys.stderr)

    report = Path(a.report)
    (report / "raw").mkdir(parents=True, exist_ok=True)
    server = _Server(report)
    chat = ollama_chat(a.ollama)
    recs = []
    out = open(report / "raw" / "results.jsonl", "w")
    try:
        for model in a.models.split(","):
            for tool in tools:
                for case in cases:
                    try:
                        rec = run_case(chat, tool, model, case, src[tool], a.max_rounds)
                    except Exception as e:   # a model that errors is a result, not a crash
                        rec = {"tool": tool, "model": model, "case": case["name"], "calls": [], "code": "",
                               "final": "", "rounds_exhausted": False, "secs": 0, "error": str(e)[:300]}
                    rec["render"] = "not-attempted"
                    if not a.no_render and "error" not in rec:
                        render(rec, src[tool], lock["openui"].get("cdn_base"), report, server, chrome)
                    if "error" in rec:
                        rec["verdict"], rec["reason"] = "fail", "ERROR"
                    else:
                        rec["verdict"], rec["reason"] = score(rec, case)
                    rec["detail"] = rec.get("error") or detail(rec, case)
                    rec["source"] = {"name": lock[tool]["name"], "version": lock[tool]["version"]}
                    recs.append(rec)
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    print(f"{rec['verdict']:9} {tool:10} {model:40} {case['name']:10} {rec['secs']:4}s "
                          f"{rec['reason'] or ''}", flush=True)
    finally:
        out.close()
        server.close()
    (report / "summary.md").write_text(summary_md(recs))
    print(marker(recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
