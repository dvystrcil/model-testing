#!/usr/bin/env python3
"""Pre-flight ONE candidate model against live ollama, and say what happened.

    bin/preflight-candidate.py --model hf.co/org/repo:Q4_K_M

models.yaml states the hazard this exists to manage:

    Pre-flighting attended is deliberate -- a first-ever load failing at 3am
    is how this GPU gets wedged.

This is the attended part, automated: every step bounded, every recorded
gfx1151 wedge signature probed for, and a hard stop the moment one appears.
It does NOT decide whether a model is good -- that is what the sweep is for.
It decides whether the model can be put in front of the sweep at all.

Wedge signatures probed, from the recorded incidents:

  server-dead      /api/tags stops answering (2026-05-08). Cheap to check.
  queue-pressure   /api/tags answers fine while real inference times out at
                   OLLAMA_LOAD_TIMEOUT (2026-05-11). Only a REAL generation
                   catches this, which is why the health probe generates.
  mid-unload       `Stopping...` for many minutes on gfx1151 (2026-05-17).
                   Small-model inference still WORKS during it, so neither
                   probe above sees it -- but /api/ps still shows the model
                   resident after an unload, which is the tell.

Exit codes are deliberately all 0 for model verdicts: a REJECT is a normal
result, not a script failure. Only an unusable argument exits non-zero.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# A model small enough that its round-trip cannot be confused for load time.
HEALTH_MODEL = "llama3.2:1b"
HEALTH_TIMEOUT = 90
# Generous: this may be a cold first-ever load of ~20GB over the network.
PULL_TIMEOUT = 3600
# Not generous: a candidate that cannot emit ONE token in this long is the
# qwen3.8:27b shape (never returns), and waiting longer just holds the GPU.
GENERATE_TIMEOUT = 300
UNLOAD_GRACE = 60


def _post(url: str, body: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _get(url: str, timeout: int) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def healthy(base: str) -> tuple[bool, str]:
    """Is ollama in a state where pulling an unknown model is safe?

    Both halves are load-bearing. /api/tags alone reports healthy through a
    queue-pressure wedge; a real generation alone is slower and cannot
    distinguish "server gone" from "model slot busy".
    """
    try:
        _get(f"{base}/api/tags", timeout=15)
    except Exception as e:
        return False, f"/api/tags unreachable: {e}"
    try:
        _post(f"{base}/api/generate",
              {"model": HEALTH_MODEL, "prompt": "hi", "stream": False,
               "options": {"num_predict": 1}}, timeout=HEALTH_TIMEOUT)
    except Exception as e:
        return False, f"{HEALTH_MODEL} could not generate: {e}"
    return True, "ok"


def resident(base: str) -> set[str]:
    try:
        return {m.get("name", "") for m in _get(f"{base}/api/ps", 15).get("models") or []}
    except Exception:
        return set()


def preflight(base: str, model: str) -> dict:
    out = {"model": model, "healthy_before": None, "pulled": False,
           "generated": False, "tools": None, "healthy_after": None,
           "notes": []}

    ok, why = healthy(base)
    out["healthy_before"] = ok
    if not ok:
        # Do NOT proceed. Whatever happens next would be attributed to this
        # candidate, blacklisting an innocent model and masking a GPU that
        # already needs a human.
        out["notes"].append(f"ollama unhealthy BEFORE pull: {why}")
        return out

    try:
        _post(f"{base}/api/pull", {"model": model, "stream": False},
              timeout=PULL_TIMEOUT)
        out["pulled"] = True
    except Exception as e:
        out["notes"].append(f"pull failed: {e}")
        out["healthy_after"] = healthy(base)[0]
        return out

    try:
        r = _post(f"{base}/api/generate",
                  {"model": model, "prompt": "Reply with the word ok.",
                   "stream": False, "options": {"num_predict": 8}},
                  timeout=GENERATE_TIMEOUT)
        out["generated"] = bool(r.get("response") is not None)
        out["notes"].append(f"done_reason={r.get('done_reason')}")
    except Exception as e:
        # The qwen3.8:27b shape. Recorded, not fatal -- unless the GPU is
        # left wedged, which the health probe below decides.
        out["notes"].append(f"generate failed: {e}")

    try:
        show = _post(f"{base}/api/show", {"model": model}, timeout=60)
        caps = show.get("capabilities") or []
        out["tools"] = "tools" in caps
        out["capabilities"] = caps
        det = show.get("details") or {}
        out["params"] = det.get("parameter_size")
        out["quant"] = det.get("quantization_level")
    except Exception as e:
        out["notes"].append(f"show failed: {e}")

    # Unload, then prove it actually unloaded. The mid-unload wedge leaves
    # the model resident while everything else keeps working, so this is the
    # only probe that sees it.
    try:
        _post(f"{base}/api/generate",
              {"model": model, "prompt": "", "keep_alive": 0}, timeout=120)
    except Exception as e:
        out["notes"].append(f"unload request failed: {e}")
    deadline = time.time() + UNLOAD_GRACE
    while time.time() < deadline and model in resident(base):
        time.sleep(5)
    if model in resident(base):
        out["notes"].append(
            f"still resident {UNLOAD_GRACE}s after unload — mid-unload wedge "
            f"signature (gfx1151, 2026-05-17)")
        out["healthy_after"] = False
        return out

    ok, why = healthy(base)
    out["healthy_after"] = ok
    if not ok:
        out["notes"].append(f"ollama unhealthy AFTER: {why}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--ollama", default="http://localhost:11434")
    a = ap.parse_args()

    result = preflight(a.ollama.rstrip("/"), a.model)
    print(json.dumps(result, indent=2))
    # A completion marker, so "ran and rejected" is distinguishable from
    # "died partway" by anything reading the log from outside.
    print(f"PREFLIGHT-COMPLETE model={a.model} pulled={result['pulled']} "
          f"generated={result['generated']} tools={result['tools']} "
          f"healthy_after={result['healthy_after']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
