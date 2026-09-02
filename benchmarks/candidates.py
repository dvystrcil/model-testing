#!/usr/bin/env python3
"""Filtering and verdict logic for weekly candidate discovery.

`bin/hf-gguf-candidates.py` finds GGUFs that fit max-01. This is the loop
around it: decide what is worth pre-flighting, and decide what a pre-flight
result means.

The whole design is shaped by one hazard, which models.yaml states plainly:

    Pre-flighting attended is deliberate -- a first-ever load failing at 3am
    is how this GPU gets wedged.

`qwen3.8:27b` sits commented out of the roster for exactly that: POST
/v1/chat/completions never returns (upstream ollama/ollama#17790). So the
rules here are conservative on purpose -- this runs unattended, on a GPU
whose recorded failure modes need a human and a pod restart.
"""
from __future__ import annotations

import re

# Matches a roster entry whether it is live or commented out, in either
# comment form. A commented-out model is the STRONGEST kind of known: someone
# removed it deliberately and wrote down why. Re-proposing it every Monday is
# how an automation gets muted -- and for qwen3.8:27b it would mean proposing
# a model that hangs the GPU.
ROSTER_ENTRY = re.compile(r"^\s*#?\s*-\s*name:\s*(\S+)", re.M)

# Reused from hf-gguf-candidates.py's reasoning: safety-stripped variants
# dominate HF's download rankings and are actively wrong here. The sweep
# carries family_* payloads, and homelab#1046 already records that no model
# in the fleet reliably refuses a privileged-container request. A model whose
# selling point is that refusals were removed is not a candidate for a
# family-facing preset. The discovery tool flags them; this loop, which runs
# unattended, drops them.
STRIPPED = ("uncensored", "abliterated", "obliterated", "heretic", "unleashed",
            "unaligned", "unfiltered", "defiant", "nsfw", "unsafe", "jailbreak")


# An ollama library tag and a HuggingFace GGUF repo are different strings for
# the same weights. Discovery proposed `hf.co/unsloth/Qwen3.8-27B-GGUF:Q8_0`
# on its first dry run -- which is `qwen3.8:27b`, the model commented out of
# the roster because POST /v1/chat/completions never returns
# (ollama/ollama#17790). Exact-name matching does not connect those, so the
# loop was about to spend a first-ever cold load on the one model documented
# to hang this GPU.
_QUANT_SUFFIX = re.compile(r":[a-z0-9_]+$", re.I)
_NOISE = re.compile(r"-(gguf|instruct|it|chat|qat|hf|awq|gptq)\b", re.I)


def _normalise(model: str) -> str:
    """A comparable identity for a model, independent of where it came from.

        qwen3.8:27b                            -> qwen3.8-27b
        hf.co/unsloth/Qwen3.8-27B-GGUF:Q8_0    -> qwen3.8-27b
    """
    m = model.strip().lower()
    m = _QUANT_SUFFIX.sub("", m)          # drop :Q8_0 / :27b handled below
    if "/" in m:
        m = m.rsplit("/", 1)[-1]          # hf.co/org/repo -> repo
    else:
        m = model.strip().lower().replace(":", "-")
    m = _NOISE.sub("", m)
    return re.sub(r"[^a-z0-9.]+", "-", m).strip("-")


def same_model(a: str, b: str) -> bool:
    """Whether two names plausibly denote the same weights.

    Deliberately errs toward MATCHING. A false match costs one candidate the
    sweep never sees; a false miss costs a cold load of something already
    known to be bad, which on this GPU can cost the night.
    """
    na, nb = _normalise(a), _normalise(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def known_models(models_yaml_text: str) -> set[str]:
    """Every model name the roster mentions, live or commented out."""
    return {m.group(1).strip() for m in ROSTER_ENTRY.finditer(models_yaml_text)}


def is_safety_stripped(model: str) -> bool:
    low = model.lower()
    return any(t in low for t in STRIPPED)


def filter_candidates(found: list[dict], known: set[str],
                      ledger: dict) -> list[dict]:
    """Candidates worth spending a first-ever GPU load on.

    A ledger entry of ANY verdict excludes: REJECT means we already know it
    does not work, ACCEPT means it is already ours. Re-testing either is
    another unattended cold load bought for nothing.
    """
    out = []
    for c in found:
        name = c["model"]
        if is_safety_stripped(name):
            continue
        if any(same_model(name, k) for k in known):
            continue
        if any(same_model(name, k) for k in ledger):
            continue
        # And against what we have already picked THIS run. HF routinely
        # lists several packagings of the same weights (…-GGUF and
        # …-GGUF-MTP); pre-flighting both spends two cold multi-GB loads to
        # learn one thing. First wins, and `found` arrives ranked by
        # downloads, so that keeps the more popular packaging.
        if any(same_model(name, c2["model"]) for c2 in out):
            continue
        out.append(c)
    return out


def classify_preflight(result: dict) -> str:
    """OK | REJECT | WEDGED | SKIPPED-UNHEALTHY.

    The distinction that matters is REJECT vs WEDGED. Both start as "the
    model did not produce a completion", and they call for opposite actions:
    a REJECT is a fact about the model, recorded so it is never retried; a
    WEDGED is a fact about the GPU, and the only safe response is to stop.
    """
    # Checked first: a wedge that was already there is not this candidate's
    # fault. Blaming it would blacklist an innocent model permanently AND
    # hide that the GPU needs a human.
    if not result.get("healthy_before", True):
        return "SKIPPED-UNHEALTHY"
    if not result.get("healthy_after", True):
        return "WEDGED"
    if not result.get("pulled") or not result.get("generated"):
        return "REJECT"
    if not result.get("tools"):
        # The sweep's agentic payloads ARE tool-call tests, so a model
        # without tools cannot answer the question the sweep exists to ask.
        # It broke nothing, though, so this is a reject and not a breaker.
        return "REJECT"
    return "OK"


def should_trip_breaker(verdict: str) -> bool:
    """Stop the loop. Continuing means pulling the next unknown model onto a
    GPU that is already stuck."""
    return verdict in ("WEDGED", "SKIPPED-UNHEALTHY")


def is_ledgerable(verdict: str) -> bool:
    """Whether the verdict is a fact about the MODEL worth remembering.

    A candidate skipped because the GPU was unhealthy was never actually
    tested; recording a verdict would mean never testing it again.
    """
    return verdict in ("OK", "REJECT")


def render_roster_addition(models_yaml_text: str, accepted: list[dict],
                           today: str = "") -> str:
    """Append pre-flighted candidates to models.yaml, preserving the file.

    APPEND, never rewrite. Round-tripping this file through a YAML dumper
    would parse and re-emit correctly and silently drop every comment in it
    -- and the comments ARE the content here: why qwen3.8:27b is excluded,
    what each control arm tests, what a previous sweep concluded.

    Each entry carries its pre-flight evidence, because the human merging
    the PR is the gate this whole design is built around and a bare name
    gives them nothing to judge.
    """
    if not accepted:
        return models_yaml_text
    stamp = f" ({today})" if today else ""
    lines = [models_yaml_text.rstrip("\n"), "",
             f"  # --- Added by weekly candidate discovery{stamp} "
             f"------------------",
             "  # Each of these was pulled, generated, and unloaded cleanly on",
             "  # max-01 before this PR was opened. Pre-flight proves it RUNS;",
             "  # the sweep is what decides whether it is any good.", ""]
    for c in accepted:
        caps = ",".join(c.get("capabilities") or []) or "unknown"
        lines += [
            f"  # {c.get('params') or '?'} params, {c.get('quant') or '?'}, "
            f"{c.get('size_gb', 0):.1f} GB. capabilities: {caps}",
            f"  - name: {c['model']}",
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def updated_ledger(ledger: dict, results: list[tuple[dict, str]]) -> dict:
    """Fold pre-flight results into the ledger.

    Only verdicts that are facts about the MODEL are written. A WEDGED or
    SKIPPED-UNHEALTHY result is a fact about the GPU; recording it here would
    permanently blacklist a model that may be perfectly fine, and would hide
    the thing that actually needs attention.
    """
    out = dict(ledger)
    for result, verdict in results:
        if not is_ledgerable(verdict):
            continue
        out[result["model"]] = {
            "verdict": verdict,
            "reason": "; ".join(result.get("notes") or []) or verdict,
        }
    return out
