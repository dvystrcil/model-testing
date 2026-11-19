#!/usr/bin/env python3
"""Tests for the `log_triage_replay` payload group (n8n-workflow#348).

The group replays the n8n Log Analysis Agent's `Ollama Triage` call over 31
real triage runs and scores each answer against hand labels. The fixtures
are private cluster logs, redacted before they were committed here, so the
first block of tests is a guard that fails if anything private is left in
them. The scoring tests then check that this repo's scorer reproduces the
numbers the original n8n-side replay published, from the same 124 answers.

    python3 -m pytest tests/test_log_triage_replay.py -q
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))
import run_benchmark  # noqa: E402

PAYLOAD = ROOT / "benchmarks" / "payloads" / "log_triage_replay.json"
REPORT = ROOT / "benchmarks" / "reports" / "task-model" / "2026-11-19-log-triage-replay"
RAW = REPORT / "raw"
GEMMA, QWEN = "gemma4:26b-a4b-it-qat", "qwen3.6:35b"


def spec():
    return json.loads(PAYLOAD.read_text())


def baseline(model):
    name = {GEMMA: "gemma4-26b-a4b-it-qat", QWEN: "qwen3.6-35b"}[model]
    return [json.loads(line) for line in (RAW / f"{name}.jsonl").read_text().splitlines()]


# ---------------------------------------------------------------- privacy guard
#
# Every file that carries replayed cluster data. The guard reads them as text,
# so a leak in any field -- a sample, a pod name, a model's answer echoing
# either -- is caught the same way.

def private_data_files():
    return [PAYLOAD, *sorted(REPORT.rglob("*")), ]


PRIVATE_PATTERNS = {
    "RFC1918 IPv4": re.compile(
        r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"),
    "UUID": re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    "cluster.local": re.compile(r"cluster\.local", re.I),
    "private domain": re.compile(r"sirddail", re.I),
    "node name": re.compile(r"\bk8s-(?:controller|node)-[\w-]+|\b(?:hpm|max)-\d{2}\b"),
    "private issue link": re.compile(
        r"github\.com/dvystrcil/(?:homelab|n8n-workflow)/issues", re.I),
    # Pod hashes and container/exec ids (32 and 64 hex). Not 40: that is a
    # git commit SHA, which the vendored prompt's provenance must carry.
    "long hex id": re.compile(r"\b(?:[0-9a-f]{32}|[0-9a-f]{64})\b"),
}


def private_hits(text):
    """[(pattern name, match)] for everything that looks private."""
    return [(name, m.group(0)) for name, rx in PRIVATE_PATTERNS.items()
            for m in rx.finditer(text)]


def scan(paths):
    hits = {}
    for p in paths:
        if Path(p).is_file():
            found = private_hits(Path(p).read_text(errors="replace"))
            if found:
                hits[str(p)] = found
    return hits


def test_the_guard_covers_the_payload_and_every_report_file():
    files = [p for p in private_data_files() if p.is_file()]
    assert PAYLOAD in files
    assert {p.name for p in files} >= {"README.md", "meta.yaml",
                                       "gemma4-26b-a4b-it-qat.jsonl", "qwen3.6-35b.jsonl"}


def test_no_private_identifiers_in_committed_fixtures_results_or_report():
    assert scan(private_data_files()) == {}


@pytest.mark.parametrize("line", [
    'error="write tcp 10.12.3.4:10001->10.12.3.5:39098: use of closed"',
    'URL:"https://172.20.0.9:10250/containerLogs/x"',
    'url "https://192.168.1.20:10250/x"',
    'volume pvc-0b1c2d3e-4f50-4a61-8b72-9c83d94ea5f6 is still attached',
    'host=harbor-core.harbor.svc.cluster.local',
    'endpoint https://ollama.sirddail.example',
    'attached to node k8s-node-zz-07',
    'kube-apiserver-k8s-controller-09',
    'kube-apiserver on hpm-07',
    'see https://github.com/dvystrcil/homelab/issues/1',
    'instance-manager-0123456789abcdef0123456789abcdef',
    'Error running exec ' + '0123456789abcdef' * 4 + ' in container',
])
def test_the_guard_flags_each_kind_of_private_identifier(line):
    """Synthetic lines, one per pattern, shaped like the raw fixtures."""
    assert private_hits(line), line


@pytest.mark.parametrize("line", [
    'error="write tcp 192.0.2.1:10001->192.0.2.1:39098"',
    'volume pvc-0002 is still attached to node node-e',
    'host=harbor-core.harbor.svc.cluster.lan',
    'kube-apiserver-node-c',
    'decision: https://github.com/dvystrcil/homelab/pull/1',
    'commit 6879fadcec94622b81f2bd3aa2f7d0ff27d1e0de',
])
def test_the_placeholders_used_by_the_redaction_pass_the_guard(line):
    assert private_hits(line) == []


# ------------------------------------------------------------- on demand only

def test_all_does_not_select_the_replay_group():
    names = {s["name"] for s in run_benchmark.load_payloads("all")}
    assert "log_triage_replay" not in names
    assert "factual_recall" in names  # still a real "all"


def test_the_group_is_selectable_by_name():
    got = run_benchmark.load_payloads("log_triage_replay")
    assert [s["name"] for s in got] == ["log_triage_replay"]


def _workflow_matrix(payload_input):
    """Run the shipped model-sweep setup step and return its matrix list."""
    wf = yaml.safe_load((ROOT / ".github/workflows/model-sweep.yaml").read_text())
    step = next(s for s in wf["jobs"]["setup"]["steps"] if s.get("id") == "set-matrix")
    script = step["run"].replace("${{ github.event.inputs.payload || 'all' }}", payload_input)
    assert "${{" not in script, "setup step uses an expression this test does not stub"
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out"
        subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=ROOT, check=True,
                       env={**os.environ, "GITHUB_OUTPUT": str(out)})
        line = out.read_text().strip()
    assert line.startswith("matrix=")
    return json.loads(line[len("matrix="):])["payload"]


def test_the_sweep_workflow_all_matrix_excludes_the_group():
    m = _workflow_matrix("all")
    assert "log_triage_replay" not in m
    assert "factual_recall" in m and "fiction_canon_adherence" in m


def test_the_sweep_workflow_runs_the_group_when_named():
    assert _workflow_matrix("log_triage_replay") == ["log_triage_replay"]


def test_the_sweep_workflow_groups_are_unchanged():
    assert all(p.startswith("surgical_edit_") for p in _workflow_matrix("surgical_edit"))
    assert all(p.startswith("fiction_") for p in _workflow_matrix("fiction"))


# ------------------------------------------------------------- payload shape

def test_payload_declares_itself_on_demand_and_describes_the_request_mapping():
    s = spec()
    assert s["name"] == "log_triage_replay" == s["type"]
    assert s["on_demand"] is True
    d = s["description"]
    for term in ("/v1", "think", "num_predict", "approximation"):
        assert term in d, term


def test_request_maps_the_nodes_openai_body_onto_native_api_chat():
    p = spec()["payload"]
    assert p["think"] is False
    assert p["stream"] is False
    assert p["options"] == {"temperature": 0.1, "num_predict": 2048}
    assert "messages" not in p  # built per case from the vendored prompt


def test_the_prompt_is_vendored_with_its_source():
    src = spec()["prompt_source"]
    assert src["repo"] == "dvystrcil/n8n-workflow"
    assert src["path"] == "workflows/log_analysis_agent.json"
    assert re.fullmatch(r"[0-9a-f]{40}", src["commit"])
    assert src["prompt_node"] == "Build Triage Prompt"
    js = src["prompt_js"]
    assert "const prompt = `You are a Kubernetes cluster health analyst." in js
    # The per-group line the Python port reproduces by hand.
    assert ("return `${i+1}. [${g.ns}] ${loc} (x${g.count} occurrences):"
            "${resHint}${rbacHint}\\n   ${g.sample.slice(0, 200)}`;") in js
    assert '"reasoning_effort": "none"' in src["request_json_body"]


def test_cases_and_labels():
    cases = spec()["cases"]
    assert len(cases) == 31
    assert len({c["id"] for c in cases}) == 31
    enum = {"NOISE", "WARNING", "CRITICAL", None}
    for c in cases:
        assert set(c["labels"]) == {"ac", "adjudicated"}
        assert set(c["labels"].values()) <= enum, c["id"]
        assert c["groups"]
        for g in c["groups"]:
            assert len(g["sample"]) <= 200
    def tally(key):
        ls = [c["labels"][key] for c in cases if c["labels"][key]]
        return len(ls), sum(x != "NOISE" for x in ls), ls.count("NOISE")
    assert tally("ac") == (27, 9, 18)
    assert tally("adjudicated") == (23, 14, 9)


# ------------------------------------------------------------- prompt rendering

GROUPS = [
    {"ns": "kube-system", "pod": "kube-vip-node-a", "container": "kube-vip", "count": 3,
     "sample": "lost leadership"},
    {"ns": "longhorn-system", "pod": "unknown", "container": "", "count": 1,
     "sample": "x" * 250,
     "resourceId": {"kind": "Pod", "namespace": "longhorn-system", "name": "im-1"}},
]


def test_render_matches_the_build_triage_prompt_node():
    case = {"rangeStart": "S", "rangeEnd": "E", "groups": GROUPS}
    got = run_benchmark.render_triage_prompt(spec(), case)
    assert got.startswith("You are a Kubernetes cluster health analyst. Analyze these error log "
                          "groups from a homelab cluster (time window: S to E).\n\n")
    assert ("Error groups:\n"
            "1. [kube-system] kube-vip-node-a/kube-vip (x3 occurrences):\n   lost leadership\n\n"
            "2. [longhorn-system] unknown (x1 occurrences): resource=Pod/longhorn-system/im-1\n   "
            + "x" * 200 + "\n\nReturn exactly 2 entries") in got
    assert '"index" is that group\'s number (1 to 2).' in got
    assert got.endswith('"executive_summary":"One warning: infisical operator failing on secret rotation"}')
    assert "${" not in got


def test_render_refuses_an_interpolation_it_does_not_know():
    s = spec()
    s["prompt_source"] = dict(s["prompt_source"],
                              prompt_js="const prompt = `a ${somethingNew} b`;")
    with pytest.raises(ValueError, match="somethingNew"):
        run_benchmark.render_triage_prompt(s, {"rangeStart": "", "rangeEnd": "", "groups": []})


# ------------------------------------------------------------- answer parsing
# A port of the node `Format Issue` + `Decide Action`: overall_severity from
# the JSON answer; anything that does not parse is UNKNOWN, which is filed.

@pytest.mark.parametrize("content,overall", [
    ('{"triaged":[],"overall_severity":"NOISE"}', "NOISE"),
    ('<think>hmm</think>\n```json\n{"overall_severity":"WARNING"}\n```', "WARNING"),
    ('```JSON\n{"overall_severity":"WARNING"}\n```', "UNKNOWN"),  # node's regex is [a-z]*
    ('{"triaged":[{"index":1]}', "UNKNOWN"),
    ('{"triaged":[]}', "UNKNOWN"),
    ('[1, 2]', "UNKNOWN"),
    ('{"overall_severity": NaN}', "UNKNOWN"),  # JSON.parse rejects NaN
    ('', "UNKNOWN"),
])
def test_triage_overall(content, overall):
    assert run_benchmark.triage_overall(content) == overall


# ------------------------------------------------------------- scoring
# Expected numbers are the published tables of the original replay (scored
# there by the shipped n8n jsCode in node), recomputed here from the same 124
# answers after redaction.

def scored(model):
    calls = [dict(c, overall=run_benchmark.triage_overall(c["content"])) for c in baseline(model)]
    return run_benchmark.score_log_triage(calls, spec()["cases"])


@pytest.mark.parametrize("model,label_set,want", [
    (GEMMA, "ac", dict(calls=54, exact=19, filed_agree=23, noise_n=36, noise_filed=31,
                       real_n=18, real_missed=0, invalid_json=0)),
    (QWEN, "ac", dict(calls=54, exact=20, filed_agree=24, noise_n=36, noise_filed=30,
                      real_n=18, real_missed=0, invalid_json=1)),
    (GEMMA, "adjudicated", dict(calls=46, exact=27, filed_agree=31, noise_n=18, noise_filed=14,
                                real_n=28, real_missed=1, invalid_json=0)),
    (QWEN, "adjudicated", dict(calls=46, exact=28, filed_agree=32, noise_n=18, noise_filed=14,
                               real_n=28, real_missed=0, invalid_json=1)),
])
def test_baseline_reproduces_the_published_label_scores(model, label_set, want):
    assert scored(model)["labels"][label_set] == want


@pytest.mark.parametrize("model,want", [
    (GEMMA, dict(median=10.9, p90=27.0, max=37.5, total=866)),
    (QWEN, dict(median=10.9, p90=23.7, max=28.3, total=788)),
])
def test_baseline_reproduces_the_published_wall_times(model, want):
    s = scored(model)
    assert s["calls"] == 62 and s["invalid_json"] == (1 if model == QWEN else 0)
    w = s["wall_s"]
    assert (round(w["median"], 1), round(w["p90"], 1), round(w["max"], 1),
            round(w["total"])) == tuple(want.values())


def test_the_baseline_has_two_reps_of_every_case_per_model():
    ids = {c["id"] for c in spec()["cases"]}
    for model in (GEMMA, QWEN):
        rows = baseline(model)
        assert len(rows) == 62
        assert {(r["case"], r["rep"]) for r in rows} == {(i, r) for i in ids for r in (1, 2)}
        assert {r["model"] for r in rows} == {model}


# ------------------------------------------------------------- through the runner

def test_run_one_posts_one_request_per_case_and_rep_and_scores_them(monkeypatch):
    s = spec()
    s["cases"] = s["cases"][:2]
    sent = []

    def fake_chat(url, payload):
        sent.append(payload)
        verdict = "NOISE" if len(sent) == 1 else "WARNING"
        return {"message": {"content": json.dumps({"triaged": [], "overall_severity": verdict})},
                "eval_count": 10, "eval_duration": 10**9, "prompt_eval_count": 100,
                "prompt_eval_duration": 10**8, "total_duration": 2 * 10**9, "done_reason": "stop"}

    monkeypatch.setattr(run_benchmark, "ollama_chat", fake_chat)
    row = run_benchmark.run_one(s, "http://ollama.invalid", "m:1", runs=2,
                                snippet_len=50, dry_run=False)
    assert len(sent) == 4
    body = sent[0]
    assert body["model"] == "m:1" and body["think"] is False and body["stream"] is False
    assert body["options"]["temperature"] == 0.1 and body["options"]["num_predict"] == 2048
    assert body["messages"] == [{"role": "user",
                                 "content": run_benchmark.render_triage_prompt(s, s["cases"][0])}]
    assert row["payload"] == "log_triage_replay" and row["model"] == "m:1" and row["runs"] == 2
    lt = row["log_triage"]
    assert lt["calls"] == 4 and lt["invalid_json"] == 0
    assert [(c["case"], c["rep"], c["overall"]) for c in lt["per_call"]] == [
        (s["cases"][0]["id"], 1, "NOISE"), (s["cases"][1]["id"], 1, "WARNING"),
        (s["cases"][0]["id"], 2, "WARNING"), (s["cases"][1]["id"], 2, "WARNING")]
    # Fields analyze.py's summary table reads.
    for k in ("facts_score", "eval_count", "eval_duration", "gen_tps", "forbidden_violations",
              "schema_violations"):
        assert k in row, k


def test_a_missed_real_problem_and_invalid_json_surface_as_schema_violations(monkeypatch):
    s = spec()
    real = next(c for c in s["cases"] if c["labels"]["ac"] == "WARNING")
    s["cases"] = [real]
    answers = iter(['{"overall_severity":"NOISE"}', "not json"])
    monkeypatch.setattr(run_benchmark, "ollama_chat",
                        lambda url, p: {"message": {"content": next(answers)}})
    row = run_benchmark.run_one(s, "http://ollama.invalid", "m:1", runs=2,
                                snippet_len=0, dry_run=False)
    assert "invalid_json: 1/2 calls" in row["schema_violations"]
    assert "missed_real(ac): 1/2" in row["schema_violations"]


def test_dry_run_sends_nothing(monkeypatch):
    monkeypatch.setattr(run_benchmark, "ollama_chat",
                        lambda *a: (_ for _ in ()).throw(AssertionError("called")))
    assert run_benchmark.run_one(spec(), "http://x", "m", 1, 0, dry_run=True) is None
