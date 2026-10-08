#!/usr/bin/env python3
"""Fixed tool-call regression probe for ollama upgrades (homelab#1205).

  bin/toolcall-regress.py --ollama http://ollama.ollama.svc.cluster.local > before.txt
  # ...upgrade ollama...
  bin/toolcall-regress.py --ollama http://ollama.ollama.svc.cluster.local > after.txt
  diff <(sed -n '/SUMMARY/,$p' before.txt) <(sed -n '/SUMMARY/,$p' after.txt)

Same prompts, tools, models and seeds every run, so a run on 0.32.15 and a
run on a candidate diff cleanly. Classifies each turn into the three failure
classes #1205 recorded, plus a KV-bleed probe (AC4).

  ok           expected tool called with schema-valid arguments
  unknown_tool called a tool that was never offered            (class 1)
  bad_args     offered tool, arguments violate the schema      (class 2)
  http_error   ollama returned non-200 (parser 500 etc.)       (class 3)
  no_call      answered in prose where a tool call was required
  bleed        response mentions the PREVIOUS request's topic  (AC4)
"""
import argparse, json, os, time, urllib.request, urllib.error

URL = "http://localhost:11434"
DEFAULT_MODELS = "qwen3.6:35b,gemma4:26b-a4b-it-qat,qwen3-coder-next:latest"

TOOLS = [
    {"type": "function", "function": {"name": "tinysearch-mcp_search",
        "description": "Search the web. Pass one item per independent query.",
        "parameters": {"type": "object", "required": ["items"], "properties": {
            "items": {"type": "array", "items": {"type": "object", "required": ["query"],
                      "properties": {"query": {"type": "string"}}}}}}}},
    {"type": "function", "function": {"name": "mealie-mcp_search_recipes",
        "description": "Search the family's Mealie recipe library by keyword.",
        "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "mealie-mcp_get_recipe",
        "description": "Fetch one Mealie recipe by its slug.",
        "parameters": {"type": "object", "required": ["slug"], "properties": {"slug": {"type": "string"}}}}},
]
OFFERED = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}
DECOYS = [{"type": "function", "function": {"name": f"{srv}_{verb}_{noun}",
    "description": f"{verb.title()} {noun.replace('_', ' ')} in {srv.split('-')[0]}.",
    "parameters": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}, "limit": {"type": "integer"}}}}}
    for srv in ["kroger-mcp", "mealie-mcp", "home-assistant-mcp", "string-tools"]
    for verb, noun in [("list", "items"), ("update", "item"), ("delete", "item"), ("get", "settings"), ("create", "shopping_list"), ("add", "to_cart")]][:25]
PAD = "\n\n".join(f"Household note {i}: The family prefers weeknight dinners under 45 minutes, keeps a well-stocked pantry with rice, pasta, canned tomatoes, onions and garlic, avoids peanuts for one child, likes leftovers for lunch, and plans the week's meals on Sunday afternoon with a shared shopping list." for i in range(400))
SYSTEM = "You are meal-time, a family cooking assistant. Use the tools when the user asks for recipes or web information."

CASES = [  # (name, messages, expected tool, extra check on args)
    ("web_single", [{"role": "user", "content": "find me beef stroganoff recipes from online"}], "tinysearch-mcp_search", None),
    ("web_two_items", [{"role": "user", "content": "Search the web for 'sourdough starter feeding schedule' and 'focaccia hydration percentage' in a single search call."}],
     "tinysearch-mcp_search", lambda a: len(a["items"]) >= 2),
    ("mealie_search", [{"role": "user", "content": "Do we have any chicken recipes in our Mealie library?"}], "mealie-mcp_search_recipes", None),
    ("mealie_get", [{"role": "user", "content": "Open our Mealie recipe with slug 'beef-stroganoff'."}], "mealie-mcp_get_recipe",
     lambda a: a["slug"] == "beef-stroganoff"),
    ("after_tool_result", [
        {"role": "user", "content": "Do we have any chicken recipes in our Mealie library?"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "mealie-mcp_search_recipes", "arguments": {"query": "chicken"}}}]},
        {"role": "tool", "tool_name": "mealie-mcp_search_recipes", "content": json.dumps({"items": [
            {"slug": "lemon-chicken-orzo", "name": "Lemon Chicken Orzo"}, {"slug": "chicken-tikka", "name": "Chicken Tikka"}]})},
        {"role": "user", "content": "Great, open the orzo one."}], "mealie-mcp_get_recipe",
     lambda a: a["slug"] == "lemon-chicken-orzo"),
]
BLEED_PAIRS = [("Write two sentences about giraffes.", "giraffe", "What is 17 multiplied by 23? Reply with only the number."),
               ("Write two sentences about lighthouses.", "lighthouse", "Name the capital of Japan. Reply with one word."),
               ("Write two sentences about volcanoes.", "volcano", "What colour is a ripe banana? One word.")]


def chat(model, messages, tools=None, seed=0, heavy=False):
    system = SYSTEM + ("\n\n" + PAD if heavy else "")
    opts = {"seed": seed, "num_predict": 1024}
    if heavy:
        opts["num_ctx"] = 49152
        tools = tools + DECOYS
    body = {"model": model, "messages": [{"role": "system", "content": system}] + messages,
            "stream": False, "think": False, "options": opts}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(URL + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return 200, json.load(r), time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}, time.time() - t
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # A hang or a dropped connection is exactly what this probe exists to
        # catch (qwen3.8 on ollama#17790 never answered): score it, don't crash.
        return 0, {"error": f"{type(e).__name__}: {e}"[:200]}, time.time() - t


def valid(schema, args):
    if not isinstance(args, dict):
        return False
    for k in schema.get("required", []):
        if k not in args:
            return False
    for k, v in args.items():
        p = schema["properties"].get(k)
        if p is None:
            continue
        if p["type"] == "array":
            if not isinstance(v, list) or not all(isinstance(i, dict) and isinstance(i.get("query"), str) for i in v):
                return False
        elif p["type"] == "string" and not isinstance(v, str):
            return False
    return True


def classify(code, resp, expected, check):
    if code != 200:
        return "http_error", resp.get("error", "")
    calls = resp.get("message", {}).get("tool_calls") or []
    if not calls:
        return "no_call", (resp.get("message", {}).get("content") or "")[:80]
    for c in calls:
        name, args = c["function"]["name"], c["function"].get("arguments")
        if name not in OFFERED:
            # Decoys are offered in heavy mode: calling one is the wrong tool, not a hallucinated one.
            if name in {d["function"]["name"] for d in DECOYS}:
                return "wrong_tool", name
            return "unknown_tool", name
        if not valid(OFFERED[name], args):
            return "bad_args", json.dumps(args)[:80]
    first = calls[0]["function"]
    if first["name"] != expected:
        return "wrong_tool", first["name"]
    try:
        if check and not check(first["arguments"]):
            return "bad_args", json.dumps(first["arguments"])[:80]
    except Exception:
        return "bad_args", json.dumps(first["arguments"])[:80]
    return "ok", ""


ABSOLUTE = ("http_error", "unknown_tool", "bleed")  # never acceptable, with or without a heavy_ prefix
OK_KEYS = ("ok", "heavy_ok", "bleed_ok")


def regressions(prev, cur):
    """AC5e: what makes a new ollama version a regression against the last one tested.

    prev/cur are --json-out documents ({"version", "totals": {model: {verdict: n}}}).
    prev may be None (first run): only the absolute rules apply then."""
    out = []
    for model, t in cur["totals"].items():
        for k, n in t.items():
            if n and k.removeprefix("heavy_") in ABSOLUTE:
                out.append(f"{model}: {k}={n}")
    if prev:
        for model, p in prev["totals"].items():
            c = cur["totals"].get(model)
            if c is None:
                out.append(f"{model}: missing from this run")
                continue
            before, after = sum(p.get(k, 0) for k in OK_KEYS), sum(c.get(k, 0) for k in OK_KEYS)
            if before - after >= 2:
                out.append(f"{model}: ok {before} -> {after}")
    return out


def render_compare(prev, cur, regs):
    lines = [f"Tool-call probe on ollama **{cur['version']}** vs the last version tested "
             f"(**{prev['version'] if prev else 'none'}**), `bin/toolcall-regress.py` (homelab#1205 AC5).", "",
             "| Model | " + ("previous | " if prev else "") + "this run |", "|---|" + ("---|" if prev else "") + "---|"]
    fmt = lambda t: " ".join(f"{k}={v}" for k, v in sorted(t.items())) if t else "-"
    for model in sorted(set(cur["totals"]) | set(prev["totals"] if prev else [])):
        row = f"| {model} | " + (f"{fmt(prev['totals'].get(model))} | " if prev else "") + f"{fmt(cur['totals'].get(model))} |"
        lines.append(row)
    lines += ["", ("**Regressions:**\n" + "\n".join(f"- {r}" for r in regs)) if regs else "No regressions.",
              "", "Rules: any `http_error`, `unknown_tool` or `bleed` fails; so does a model's ok count dropping "
              "by 2 or more, or a model missing from the run."]
    return "\n".join(lines)


def main():
    global URL
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ollama", default=URL, help="ollama base URL (default %(default)s)")
    ap.add_argument("--runs", type=int, default=3, help="seeded runs per case (default %(default)s)")
    ap.add_argument("--models", default=DEFAULT_MODELS, help="comma-separated (default %(default)s)")
    ap.add_argument("--json-out", help="write {version, runs, totals} here")
    ap.add_argument("--compare", nargs=2, metavar=("PREV_JSON", "CUR_JSON"),
                    help="compare two --json-out files instead of probing; PREV_JSON may be absent")
    ap.add_argument("--md-out", help="with --compare: write the markdown report here")
    a = ap.parse_args()
    if a.compare:
        prev = json.load(open(a.compare[0])) if os.path.exists(a.compare[0]) else None
        cur = json.load(open(a.compare[1]))
        regs = regressions(prev, cur)
        md = render_compare(prev, cur, regs)
        if a.md_out:
            open(a.md_out, "w").write(md)
        print(md)
        print(f"TOOLCALL-PROBE-COMPARE version={cur['version']} prev={prev['version'] if prev else '-'} "
              f"regressions={len(regs)}")
        return
    URL, RUNS = a.ollama.rstrip("/"), a.runs
    ver = json.load(urllib.request.urlopen(URL + "/api/version"))["version"]
    print(f"# ollama {ver}  runs={RUNS}")
    totals = {}
    for model in a.models.split(","):
        chat(model, [{"role": "user", "content": "hi"}])  # load, not scored
        for heavy in (False, True):
            for name, msgs, expected, check in CASES:
                for run in range(RUNS):
                    code, resp, dt = chat(model, msgs, TOOLS, seed=run, heavy=heavy)
                    verdict, detail = classify(code, resp, expected, check)
                    key = ("heavy_" if heavy else "") + verdict
                    totals.setdefault(model, {}).setdefault(key, 0)
                    totals[model][key] += 1
                    ptok = resp.get("prompt_eval_count", "")
                    print(f"{model:28s} {('H:' if heavy else '')+name:20s} run{run} {verdict:12s} {dt:5.1f}s p={ptok} {detail}")
        for run in range(RUNS):
            for prev, topic, probe in BLEED_PAIRS:
                chat(model, [{"role": "user", "content": prev}], seed=run)
                code, resp, dt = chat(model, [{"role": "user", "content": probe}], seed=run)
                text = (resp.get("message", {}).get("content") or "").lower()
                verdict = "http_error" if code != 200 else ("bleed" if topic in text else "ok")
                totals[model].setdefault("bleed" if verdict == "bleed" else ("bleed_ok" if verdict == "ok" else verdict), 0)
                totals[model]["bleed" if verdict == "bleed" else ("bleed_ok" if verdict == "ok" else verdict)] += 1
                print(f"{model:28s} kv_bleed_{topic:9s} run{run} {verdict:12s} {dt:5.1f}s {text[:60]!r}")
    print("\n# SUMMARY")
    for m, t in totals.items():
        print(f"{m:28s} " + " ".join(f"{k}={v}" for k, v in sorted(t.items())))
    if a.json_out:
        json.dump({"version": ver, "runs": RUNS, "totals": totals}, open(a.json_out, "w"), indent=1)
    print(f"TOOLCALL-PROBE-COMPLETE version={ver} models={len(totals)}")


if __name__ == "__main__":
    main()
