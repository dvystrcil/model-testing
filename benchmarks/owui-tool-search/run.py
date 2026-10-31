#!/usr/bin/python3
"""owui-tool-search bench: does Tool Search or lazy skill loading pay off? (homelab#1871 AC2)

Both are GLOBAL OWUI settings (`chat.tool_search.enable`,
`skills.lazy_loading.enable`) with no per-model override, so they are measured
on a throwaway OWUI in namespace owui-bench, never on prod:

    run.py up                 # namespace, stub MCP, OWUI v0.12.5 (+ prod's middleware patch)
    run.py setup              # seed prod's non-secret config, test user, preset + skill copies
    run.py bench --report DIR [--arms baseline,tool_search,lazy,both] [--trials 3] [--cases a,b]
    run.py rescore --report DIR
    run.py down               # delete the namespace

Every turn goes through the UI's socket path as a NON-admin user
(homelab/bin/owui-socket-chat.py --skill-ids model), so OWUI applies the same
transforms it applies in the browser. MCP servers are stubs that serve the real
captured schemas (tools.lock.json) and return canned replies.

Scored per turn: the first domain tool called (meta tools like search_tools and
view_skill don't count; call_tool is unwrapped to its target) against the
case's expected set, and usage.input_tokens (summed over every LLM call in the
turn) as OWUI recorded it.

Last line of `bench`: OWUI-TOOL-SEARCH-COMPLETE records=N errors=E
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
NS = "owui-bench"
PORT = 18080
BASE = f"http://127.0.0.1:{PORT}"
HARNESS = Path(os.environ.get("OWUI_SOCKET_CHAT", Path.home() / "Code/homelab/bin/owui-socket-chat.py"))
HARNESS_PY = Path(os.environ.get("OWUI_SOCKET_PY", HARNESS.parent / ".venv-socket/bin/python"))
STATE = HERE / ".bench-state.json"  # gitignored: session tokens + test user id for this namespace's life
PROD_PG = ("open-webui", "postgres-operator.crunchydata.com/role=master", "owui")
STUB = "http://stub-mcp.owui-bench.svc.cluster.local:8080"

_OFF = {"chat.tool_search.always_loaded": []}
# tool_search_pinned keeps the-one-ring's small, judgement-heavy tool sets in the
# provider array (fnmatch patterns on chat.tool_search.always_loaded).
PINNED = ["rpg-dice-mcp_*", "tor-character-mcp_*"]
ARMS = {
    "baseline": {"chat.tool_search.enable": False, "skills.lazy_loading.enable": False, **_OFF},
    "tool_search": {"chat.tool_search.enable": True, "skills.lazy_loading.enable": False, **_OFF},
    "lazy": {"chat.tool_search.enable": False, "skills.lazy_loading.enable": True, **_OFF},
    "both": {"chat.tool_search.enable": True, "skills.lazy_loading.enable": True, **_OFF},
    "tool_search_pinned": {"chat.tool_search.enable": True, "skills.lazy_loading.enable": False,
                           "chat.tool_search.always_loaded": PINNED},
}
# Never copied from prod: credentials, auth, and connections the bench replaces.
EXCLUDE = re.compile(r"(^openai\.|^ollama\.|^tool_server\.|^terminal_server\.|^direct\.|^oauth\.|^ldap\.|"
                     r"api_key|secret|token|passw|smtp|webhook|license|credential|client_id|private|cookie)", re.I)
KEEP = re.compile(r"^chat\.context_compaction\.")
META_TOOLS = {"search_tools", "view_skill", "read_skill_file", "grep_skill_files", "create_tasks",
              "update_task", "get_current_timestamp", "calculate_timestamp"}


# ── pure helpers (tested in tests/test_owui_tool_search.py) ──────────────────

def keep_config_key(key: str) -> bool:
    return bool(KEEP.search(key)) or not EXCLUDE.search(key)


def repair_name(name: str, tools: set[str]) -> str:
    """Prod's tool-name repair (homelab#1152 patch-middleware.py), mirrored so
    the score matches what prod EXECUTES: an exact name stays; otherwise a
    suffix match wins only if exactly one tool has it. `wiki_search` runs as
    `wiki-mcp_wiki_search`; a made-up `mealie-mcp_search_recipes` stays wrong."""
    if not name or not tools or name in tools:
        return name
    candidates = [t for t in tools if t.endswith("_" + name)]
    return candidates[0] if len(candidates) == 1 else name


def preset_tools(preset: str) -> set[str]:
    """The MCP tool names a preset's copy is offered, as OWUI names them."""
    lock = json.loads((HERE / "tools.lock.json").read_text())["servers"]
    return {f"{srv}_{t['name']}" for srv in cases()["preset_servers"][preset] for t in lock[srv]}


def called_tools(output, tools: set[str] | None = None) -> list[str]:
    """Tool names in call order, call_tool unwrapped and names repaired as prod does."""
    names = []
    for block in output if isinstance(output, list) else []:
        if not isinstance(block, dict) or block.get("type") != "function_call":
            continue
        name = block.get("name") or ""
        if name == "call_tool":
            try:
                name = json.loads(block.get("arguments") or "{}").get("tool_name") or "call_tool"
            except (ValueError, AttributeError):
                name = "call_tool"
        names.append(repair_name(name, tools or set()))
    return names


def score(names: list[str], expect: list[str]) -> dict:
    domain = [n for n in names if n not in META_TOOLS]
    first = domain[0] if domain else None
    ok = (first is None) if not expect else (first in expect)
    return {"ok": ok, "first_domain_tool": first, "used_search_tools": "search_tools" in names,
            "n_tool_calls": len(names)}


def summarize(records: list[dict]) -> str:
    """Markdown scoreboard per (arm, preset).

    tool choice: first domain tool right, over task cases that didn't error.
    overhead tokens: input_tokens of the "Reply with exactly OK" case, one LLM
      call, so it is the fixed cost every turn pays (system prompt, tool
      schemas, injected skills, manifests).
    task tokens: input_tokens summed over every call of a task turn; this
      grows with how long the model loops, so it is noisier."""
    cells = {}
    for r in records:
        c = cells.setdefault((r["arm"], r["preset"]), {"n": 0, "ok": 0, "err": 0, "tok": [], "over": []})
        if r.get("error"):
            c["err"] += 1
            continue
        if r.get("overhead"):
            if r.get("input_tokens"):
                c["over"].append(r["input_tokens"])
            continue
        c["n"] += 1
        c["ok"] += bool(r["ok"])
        if r.get("input_tokens"):
            c["tok"].append(r["input_tokens"])
    mean = lambda xs: round(sum(xs) / len(xs)) if xs else "-"
    lines = ["| arm | preset | tool choice | errors | overhead tokens/turn | task input tokens/turn |",
             "|---|---|---|---|---|---|"]
    for (arm, preset), c in sorted(cells.items(), key=lambda kv: (list(ARMS).index(kv[0][0]), kv[0][1])):
        lines.append(f"| {arm} | {preset} | {c['ok']}/{c['n']} | {c['err']} | {mean(c['over'])} | {mean(c['tok'])} |")
    return "\n".join(lines)


# ── cluster / HTTP plumbing ──────────────────────────────────────────────────

def sh(*args, input=None, check=True) -> str:
    r = subprocess.run(list(args), input=input, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"FAILED: {' '.join(args[:4])} ...\n{r.stderr[-800:]}")
    return r.stdout


def prod_sql(query: str) -> str:
    ns, sel, db = PROD_PG
    pod = sh("kubectl", "-n", ns, "get", "pod", "-l", sel, "-o", "name").split()[0]
    return sh("kubectl", "-n", ns, "exec", pod, "-c", "database", "--", "psql", "-d", db, "-Atc", query)


def bench_sqlite_upsert(rows: dict) -> None:
    """Write config rows straight into the bench DB. Config.get reads the DB on
    every call (no cache), so a change applies to the next request."""
    script = ("import json,sqlite3,sys,time\nrows=json.load(sys.stdin)\n"
              "c=sqlite3.connect('/app/backend/data/webui.db')\n"
              "c.executemany('insert into config(key,value,updated_at) values(?,?,?) on conflict(key) do update "
              "set value=excluded.value, updated_at=excluded.updated_at',"
              "[(k,json.dumps(v),int(time.time())) for k,v in rows.items()])\nc.commit();print(len(rows))")
    sh("kubectl", "-n", NS, "exec", "-i", "deploy/owui-bench", "--", "python3", "-c", script, input=json.dumps(rows))


class PortForward:
    """kubectl port-forward dies now and then over a long run, so every API
    call and turn goes through ensure(), which restarts it when it's gone."""
    current = None

    def __enter__(self):
        self.p = None
        PortForward.current = self
        self.ensure()
        return self

    def healthy(self) -> bool:
        try:
            urllib.request.urlopen(BASE + "/health", timeout=3)
            return True
        except Exception:
            return False

    def ensure(self):
        if self.p and self.p.poll() is None and self.healthy():
            return
        if self.p:
            self.p.kill()
            self.p.wait()
        self.p = subprocess.Popen(["kubectl", "-n", NS, "port-forward", "svc/owui-bench", f"{PORT}:8080"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if self.healthy():
                return
            time.sleep(1)
        sys.exit("port-forward to owui-bench never became healthy")

    def __exit__(self, *exc):
        PortForward.current = None
        self.p.kill()
        self.p.wait()


def api(method: str, path: str, token: str, body=None, _retry=True):
    if PortForward.current:
        PortForward.current.ensure()
    req = urllib.request.Request(BASE + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code} {e.read().decode()[:300]}")
    except urllib.error.URLError:
        if not _retry:
            raise
        time.sleep(2)
        return api(method, path, token, body, _retry=False)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_up(_):
    sh("kubectl", "apply", "-f", str(HERE / "k8s/bench.yaml"))
    cm = sh("kubectl", "-n", NS, "create", "configmap", "stub-mcp", f"--from-file={HERE / 'stub_mcp.py'}",
            f"--from-file={HERE / 'tools.lock.json'}", "--dry-run=client", "-o", "yaml")
    sh("kubectl", "apply", "-f", "-", input=cm)
    patch = sh("kubectl", "-n", "open-webui", "get", "cm", "open-webui-patch-middleware", "-o", "json")
    data = json.loads(patch)["data"]
    sh("kubectl", "apply", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "ConfigMap",
       "metadata": {"name": "patch-middleware", "namespace": NS}, "data": data}))
    if not sh("kubectl", "-n", NS, "get", "secret", NS, "--ignore-not-found", "-o", "name"):
        key = base64.b64encode(os.urandom(32)).decode()
        sh("kubectl", "apply", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "Secret",
           "metadata": {"name": NS, "namespace": NS},
           "data": {"webui-secret-key": base64.b64encode(key.encode()).decode()}}))
    for d in ("stub-mcp", "owui-bench"):
        sh("kubectl", "-n", NS, "rollout", "status", f"deploy/{d}", "--timeout=600s")
    print("up")


def cmd_setup(_):
    # 1. prod's non-secret config, plus the bench's own MCP connections (same ids, stub URLs).
    rows = {}
    for line in prod_sql("select key, value::text from config").splitlines():
        key, _, value = line.partition("|")
        if keep_config_key(key):
            rows[key] = json.loads(value)
    conns = json.loads(prod_sql("select value::text from config where key='tool_server.connections'"))
    mcp = []
    for c in conns:
        if c.get("type") == "mcp":
            sid = c["info"]["id"]
            mcp.append({**c, "url": f"{STUB}/{sid}/mcp", "auth_type": "none", "key": ""})
    rows["tool_server.connections"] = mcp
    rows["terminal_server.connections"] = []
    rows.update(ARMS["baseline"])
    bench_sqlite_upsert(rows)
    print(f"seeded {len(rows)} config rows ({len(mcp)} MCP connections -> stub)")
    # Restart once so anything read at startup sees the seeded rows. emptyDir keeps the DB.
    sh("kubectl", "-n", NS, "exec", "deploy/owui-bench", "--", "python3", "-c", "import os,signal;os.kill(1,signal.SIGTERM)",
       check=False)
    time.sleep(5)
    sh("kubectl", "-n", NS, "wait", "--for=condition=Ready", "pod", "-l", "app=owui-bench", "--timeout=600s")

    with PortForward():
        # 2. admin = first signup; then a non-admin test user.
        pw = base64.urlsafe_b64encode(os.urandom(18)).decode()
        r = urllib.request.urlopen(urllib.request.Request(BASE + "/api/v1/auths/signup", method="POST",
            data=json.dumps({"name": "bench-admin", "email": "bench-admin@bench.local", "password": pw}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60)
        admin = json.loads(r.read())["token"]
        user = api("POST", "/api/v1/auths/add", admin, {"name": "bench-user", "email": "bench-user@bench.local",
                                                          "password": pw, "role": "user"})
        # A real sign-in, not a minted JWT: v0.12.5 rejects any token whose
        # session_stamp differs from the account's, and new accounts get one.
        r = urllib.request.urlopen(urllib.request.Request(BASE + "/api/v1/auths/signin", method="POST",
            data=json.dumps({"email": "bench-user@bench.local", "password": pw}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60)
        user_token = json.loads(r.read())["token"]
        grant = [{"principal_type": "user", "principal_id": user["id"], "permission": "read"}]
        # 3. preset + skill copies, read-shared with the test user only.
        presets = [p for p in cases()["presets"]]
        skills = set()
        for pid in presets:
            row = json.loads(prod_sql(f"select json_build_object('base', base_model_id, 'name', name, "
                                      f"'meta', meta::json, 'params', params::json) from model where id='{pid}'"))
            meta = row["meta"]
            meta["toolIds"] = [t for t in meta.get("toolIds") or [] if t.startswith("server:mcp:")]
            skills |= set(meta.get("skillIds") or [])
            # v0.12.5 makes an unregistered base model admin-only, even through a
            # shared preset (access_control.has_base_model_access). Register the
            # base with prod's params when prod has a row, so the user can reach it.
            base = json.loads(prod_sql(f"select coalesce((select params::text from model where id='{row['base']}'), "
                                       f"'{{\"function_calling\": \"native\"}}')"))
            api("POST", "/api/v1/models/create", admin, {"id": row["base"], "base_model_id": None, "name": row["base"],
                "meta": {}, "params": base, "access_grants": grant})
            api("POST", "/api/v1/models/create", admin, {"id": pid, "base_model_id": row["base"], "name": row["name"],
                "meta": meta, "params": row["params"], "access_grants": grant})
        for sid in sorted(skills):
            out = prod_sql(f"select json_build_object('name', name, 'description', description, 'content', content) "
                           f"from skill where id='{sid}'")
            if not out.strip():
                print(f"  skill {sid!r} is referenced by a preset but has no row in prod; skipped (as prod does)")
                continue
            s = json.loads(out)
            api("POST", "/api/v1/skills/create", admin, {"id": sid, "name": s["name"], "description": s["description"],
                "files": [{"path": "SKILL.md", "content": s["content"]}], "access_grants": grant})
        api("GET", "/api/models?refresh=true", admin)
    STATE.write_text(json.dumps({"admin_token": admin, "user_id": user["id"], "user_token": user_token}))
    print(f"setup: presets={presets} skills={sorted(skills)} user={user['id']}")


def cases() -> dict:
    return json.loads((HERE / "cases.json").read_text())


def run_turn(token: str, preset: str, prompt: str, title: str, timeout: int) -> dict:
    env = {**os.environ, "OWUI_API_KEY": token}
    PortForward.current.ensure()
    t = time.time()
    r = subprocess.run([str(HARNESS_PY), str(HARNESS), "--base-url", BASE, "--model", preset, "--no-nudge",
                        "--no-cleanup", "--skill-ids", "model", "--title", title, "--timeout", str(timeout)],
                       input=prompt, capture_output=True, text=True, env=env, timeout=timeout + 120)
    meta = {}
    for line in r.stderr.splitlines():
        m = re.search(r"(\{\"chat_id\".*\})\s*$", line)
        if m:
            meta = json.loads(m.group(1))
    if not meta.get("chat_id"):
        return {"error": f"harness rc={r.returncode}: {r.stderr[-300:]}", "elapsed_s": round(time.time() - t, 1)}
    chat = api("GET", f"/api/v1/chats/{meta['chat_id']}", token)
    msg = chat["chat"]["history"]["messages"].get(meta["assist_id"], {})
    usage = msg.get("usage") or {}
    return {"chat_id": meta["chat_id"], "done": meta.get("done"), "output": msg.get("output"),
            "reply": (r.stdout or "")[-400:], "input_tokens": usage.get("input_tokens"),
            "last_prompt_tokens": usage.get("prompt_tokens"), "elapsed_s": round(time.time() - t, 1),
            **({"error": msg.get("error") or "not done"} if not meta.get("done") else {})}


def cmd_bench(a):
    st = json.loads(STATE.read_text())
    token = st["user_token"]
    cfg = cases()
    picked = [c for c in cfg["cases"] if not a.cases or c["id"] in a.cases.split(",")]
    report = Path(a.report)
    (report / "raw").mkdir(parents=True, exist_ok=True)
    out = report / "raw/results.jsonl"
    records = [json.loads(l) for l in out.read_text().splitlines()] if out.exists() else []
    done = {(r["arm"], r["case"], r["trial"]) for r in records}
    with PortForward(), out.open("a") as f:
        for arm in a.arms.split(","):
            bench_sqlite_upsert(ARMS[arm])
            for trial in range(a.trials):
                for c in picked:  # grouped by preset in cases.json, so ollama swaps models rarely
                    if (arm, c["id"], trial) in done:
                        continue
                    res = run_turn(token, c["preset"], c["prompt"], f"bench {arm} {c['id']} t{trial}", a.timeout)
                    rec = {"arm": arm, "case": c["id"], "preset": c["preset"], "trial": trial, "expect": c["expect"],
                           "overhead": bool(c.get("overhead")),
                           **res, **score(called_tools(res.get("output"), preset_tools(c["preset"])), c["expect"])}
                    rec["called"] = called_tools(res.get("output"), preset_tools(c["preset"]))
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    records.append(rec)
                    print(f"{arm:11} {c['id']:24} t{trial} ok={rec['ok']!s:5} first={rec['first_domain_tool']} "
                          f"tok={rec.get('input_tokens')} {rec.get('error') or ''}", flush=True)
        bench_sqlite_upsert(ARMS["baseline"])
    (report / "summary.md").write_text(summarize(records) + "\n")
    print(summarize(records))
    print(f"OWUI-TOOL-SEARCH-COMPLETE records={len(records)} errors={sum(1 for r in records if r.get('error'))}")


def cmd_rescore(a):
    out = Path(a.report) / "raw/results.jsonl"
    expect = {c["id"]: c["expect"] for c in cases()["cases"]}
    records = []
    for line in out.read_text().splitlines():
        r = json.loads(line)
        r["expect"] = expect.get(r["case"], r["expect"])  # cases.json is the current truth
        r["called"] = called_tools(r.get("output"), preset_tools(r["preset"]))
        r.update(score(r["called"], r["expect"]))
        records.append(r)
    (Path(a.report) / "summary.md").write_text(summarize(records) + "\n")
    print(summarize(records))


def cmd_down(_):
    sh("kubectl", "delete", "namespace", NS, "--ignore-not-found", "--wait=true")
    STATE.unlink(missing_ok=True)
    print("down")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up")
    sub.add_parser("setup")
    b = sub.add_parser("bench")
    b.add_argument("--report", required=True)
    b.add_argument("--arms", default="baseline,tool_search,lazy,both")
    b.add_argument("--trials", type=int, default=3)
    b.add_argument("--cases", default="")
    b.add_argument("--timeout", type=int, default=900)
    r = sub.add_parser("rescore")
    r.add_argument("--report", required=True)
    sub.add_parser("down")
    a = ap.parse_args()
    {"up": cmd_up, "setup": cmd_setup, "bench": cmd_bench, "rescore": cmd_rescore, "down": cmd_down}[a.cmd](a)


if __name__ == "__main__":
    main()
