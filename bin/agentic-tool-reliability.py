#!/usr/bin/env python3
"""Agentic tool-reliability benchmark for Ollama's native /api/chat tool loop.

Runs deterministic, simulated recipe tools. No production services are called.
Each case scores tool order, arguments, recovery, final-answer grounding, and
latency/token usage. See benchmarks/agentic-tool-reliability/README.md.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS = [
    {"type": "function", "function": {"name": "recipe_search",
     "description": "Search the recipe library. Use this to find recipes by name or ingredient.",
     "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "recipe_get",
     "description": "Get a recipe by its exact slug. Returns ingredients and preparation notes.",
     "parameters": {"type": "object", "required": ["slug"], "properties": {"slug": {"type": "string"}}}}},
]
SCENARIOS = {
    "multi_step": {
        "prompt": "Find the lemon chicken orzo recipe in our library and open it. Tell me its key ingredients and cooking temperature.",
        "sequence": ["recipe_search", "recipe_get"],
        "checks": {"query_contains": "lemon", "slug": "lemon-chicken-orzo"},
        "facts": ["chicken", "orzo", "400°F"],
        "description": "Search, consume the returned slug, fetch the recipe, and ground the answer.",
    },
    "error_recovery": {
        "prompt": "Open the recipe with slug 'beef-stroganoff' and summarize its main ingredients.",
        "sequence": ["recipe_get", "recipe_get"],
        "checks": {"slug": "beef-stroganoff"},
        "facts": ["mushrooms", "beef"],
        "description": "Retry the same tool after a clearly transient tool error.",
    },
    "result_grounding": {
        "prompt": "Search the library for sourdough bread and tell me the hydration percentage stated in the recipe.",
        "sequence": ["recipe_search", "recipe_get"],
        "checks": {"query_contains": "sourdough", "slug": "sourdough-country-loaf"},
        "facts": ["78%"],
        "description": "Carry a precise fact from a tool result into the final answer.",
    },
    "no_tool": {
        "prompt": "How many tablespoons are in one quarter cup? Answer briefly.",
        "sequence": [],
        "checks": {},
        "facts": ["4 tablespoons"],
        "description": "Do not call tools for a simple self-contained question.",
    },
}
SYSTEM = ("You are a careful cooking assistant. Use the provided tools when needed. "
          "Never invent tool results. After a tool error, follow its recovery instruction. "
          "When a tool returns data, base your answer on that data.")
RECIPES = {
    "lemon-chicken-orzo": {"name": "Lemon Chicken Orzo", "ingredients": "chicken, orzo, lemon, spinach, garlic",
                           "temperature": "400°F", "instructions": "Bake at 400°F until the chicken is cooked."},
    "beef-stroganoff": {"name": "Beef Stroganoff", "ingredients": "beef, mushrooms, onion, sour cream",
                        "instructions": "Brown beef and mushrooms; simmer with sour cream."},
    "sourdough-country-loaf": {"name": "Sourdough Country Loaf", "ingredients": "flour, water, starter, salt",
                               "hydration": "78%", "instructions": "Bulk ferment, shape, and bake."},
}


def execute_tool(name, args, state):
    if name not in {"recipe_search", "recipe_get"}:
        return {"error": "Unknown tool. Use only tools in the supplied schema."}
    if not isinstance(args, dict):
        return {"error": "Arguments must be a JSON object."}
    if name == "recipe_search":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"error": "query must be a non-empty string"}
        matches = [{"slug": slug, "name": recipe["name"]} for slug, recipe in RECIPES.items()
                   if query.lower() in slug.lower() or query.lower() in recipe["name"].lower()
                   or query.lower() in recipe["ingredients"].lower()]
        return {"recipes": matches}
    if name == "recipe_get":
        slug = args.get("slug")
        if not isinstance(slug, str) or not slug:
            return {"error": "slug must be a non-empty string"}
        if state.get("scenario") == "error_recovery" and not state.get("transient_sent"):
            state["transient_sent"] = True
            return {"error": "Temporary service unavailable (503). Retry this exact request once."}
        recipe = RECIPES.get(slug)
        return recipe or {"error": f"No recipe found for slug '{slug}'."}
    return {"error": "unreachable"}


def ollama_chat(base_url, model, messages, seed, timeout):
    body = {"model": model, "messages": messages, "tools": TOOLS, "stream": False,
            "think": False, "options": {"temperature": 0.1, "seed": seed, "num_predict": 700}}
    req = urllib.request.Request(base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.load(response)
        return data, time.monotonic() - started, None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {}, time.monotonic() - started, f"{type(exc).__name__}: {exc}"


def run_case(base_url, model, case_id, seed, timeout, max_turns=6):
    case = SCENARIOS[case_id]
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": case["prompt"]}]
    calls, errors, latency, prompt_tokens, output_tokens = [], [], 0.0, 0, 0
    state = {"scenario": case_id}
    final = ""
    for turn in range(max_turns):
        response, elapsed, error = ollama_chat(base_url, model, messages, seed + turn, timeout)
        latency += elapsed
        if error:
            errors.append(error)
            break
        usage = response.get("prompt_eval_count") or 0
        prompt_tokens += usage
        output_tokens += response.get("eval_count") or 0
        msg = response.get("message") or {}
        messages.append(msg)
        final = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            break
        for call in tool_calls:
            fn = call.get("function") or {}
            name, args = fn.get("name"), fn.get("arguments")
            calls.append({"name": name, "arguments": args})
            result = execute_tool(name, args, state)
            messages.append({"role": "tool", "tool_name": name or "unknown",
                             "content": json.dumps(result, sort_keys=True)})
    else:
        errors.append(f"tool loop exceeded {max_turns} turns")

    expected = case["sequence"]
    actual = [c["name"] for c in calls]
    sequence_ok = actual == expected
    args_ok = True
    for c in calls:
        a = c["arguments"]
        if not isinstance(a, dict):
            args_ok = False
            continue
        if c["name"] == "recipe_search":
            target = case["checks"].get("query_contains")
            args_ok &= isinstance(a.get("query"), str) and (not target or target in a["query"].lower())
        elif c["name"] == "recipe_get":
            target = case["checks"].get("slug")
            args_ok &= isinstance(a.get("slug"), str) and (not target or a["slug"] == target)
        else:
            args_ok = False
    facts_ok = all(f.lower() in final.lower() for f in case["facts"])
    passed = not errors and sequence_ok and args_ok and facts_ok
    return {"model": model, "case": case_id, "seed": seed, "passed": bool(passed),
            "tool_sequence_ok": bool(sequence_ok), "arguments_ok": bool(args_ok),
            "final_facts_ok": bool(facts_ok), "expected_tools": expected, "actual_tools": actual,
            "calls": calls, "final_answer": final, "errors": errors, "latency_seconds": round(latency, 3),
            "prompt_tokens": prompt_tokens, "output_tokens": output_tokens}


def summarize(records):
    groups = {}
    for row in records:
        g = groups.setdefault(row["model"], {"runs": 0, "passed": 0, "sequence": 0, "args": 0,
                                              "facts": 0, "latency": [], "prompt": 0, "output": 0})
        g["runs"] += 1
        g["passed"] += int(row["passed"])
        g["sequence"] += int(row["tool_sequence_ok"])
        g["args"] += int(row["arguments_ok"])
        g["facts"] += int(row["final_facts_ok"])
        g["latency"].append(row["latency_seconds"])
        g["prompt"] += row["prompt_tokens"]
        g["output"] += row["output_tokens"]
    lines = ["# Agentic tool reliability results", "",
             "| Model | Pass | Tool sequence | Arguments | Final facts | Mean latency (s) | Prompt tok | Output tok |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for model, g in sorted(groups.items()):
        n = g["runs"]
        mean = sum(g["latency"]) / n if n else 0
        lines.append(f"| {model} | {g['passed']}/{n} | {g['sequence']}/{n} | {g['args']}/{n} | "
                     f"{g['facts']}/{n} | {mean:.2f} | {g['prompt']} | {g['output']} |")
    lines += ["", "A case passes only when there are no transport/loop errors, the exact expected tool "
              "sequence is used, arguments satisfy the scenario checks, and required facts appear in "
              "the final answer. Latency is end-to-end across model turns; tokens are summed across turns."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--models", default="qwen3.6:35b,qwen3.8:27B,nemotron-3.5-lightning:30b-a3b-q4_K_M,glm-4.7-flash")
    ap.add_argument("--cases", default=",".join(SCENARIOS), help="Comma-separated scenario IDs")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--seed", type=int, default=4100)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--report", type=Path, default=Path("benchmarks/reports/agentic-tool-reliability"))
    ap.add_argument("--max-turns", type=int, default=6)
    args = ap.parse_args()
    models = [x.strip() for x in args.models.split(",") if x.strip()]
    cases = [x.strip() for x in args.cases.split(",") if x.strip()]
    unknown = sorted(set(cases) - set(SCENARIOS))
    if unknown:
        ap.error(f"unknown cases: {', '.join(unknown)}")
    if args.runs < 1:
        ap.error("--runs must be >= 1")
    args.report.mkdir(parents=True, exist_ok=True)
    records = []
    out = args.report / "results.jsonl"
    with out.open("w") as stream:
        for model in models:
            for case in cases:
                for run in range(args.runs):
                    row = run_case(args.ollama, model, case, args.seed + run * 100, args.timeout, args.max_turns)
                    records.append(row)
                    stream.write(json.dumps(row, sort_keys=True) + "\n")
                    stream.flush()
                    print(f"{model} {case} run={run + 1}/{args.runs}: "
                          f"{'PASS' if row['passed'] else 'FAIL'} "
                          f"tools={row['actual_tools']} latency={row['latency_seconds']}s")
    summary = summarize(records)
    (args.report / "summary.md").write_text(summary + "\n")
    print("\n" + summary)
    print(f"AGENTIC-TOOL-RELIABILITY-COMPLETE records={len(records)} "
          f"passed={sum(r['passed'] for r in records)} report={args.report}")


if __name__ == "__main__":
    main()
