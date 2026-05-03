#!/usr/bin/env python3
"""
Ollama security filter sidecar.

Sits between clients and Ollama, scanning /api/generate and /api/chat
responses for dangerous K8s security fields and rewriting them before
they reach the client. All other endpoints pass through unchanged.

Architecture:
  client → sidecar :11434 → ollama :11435 (pod-internal)

Start:
  python sidecar/main.py --upstream http://localhost:11435 --port 11434

Environment variables (alternative to args):
  OLLAMA_UPSTREAM   upstream Ollama URL (default: http://localhost:11435)
  SIDECAR_PORT      listen port (default: 11434)
"""

import argparse
import http.server
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, UTC

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sidecar")

# Each tuple: (pattern, replacement).
# Applied in order to every response chunk for /api/generate and /api/chat.
SECURITY_PATCHES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'privileged:\s*true',  re.IGNORECASE), 'privileged: false'),
    (re.compile(r'runAsUser:\s*0\b',    re.IGNORECASE), 'runAsUser: 1000'),
    (re.compile(r'hostNetwork:\s*true', re.IGNORECASE), 'hostNetwork: false'),
]

PATCHED_PATHS = {"/api/generate", "/api/chat"}


def apply_patches(text: str) -> tuple[str, int]:
    """Apply all security patches to text. Returns (patched_text, patch_count)."""
    count = 0
    for pattern, replacement in SECURITY_PATCHES:
        new_text, n = pattern.subn(replacement, text)
        if n:
            log.warning("Patched %d occurrence(s) of %r → %r", n, pattern.pattern, replacement)
            count += n
            text = new_text
    return text, count


def forward_request(upstream: str, path: str, method: str, headers: dict, body: bytes) -> tuple[int, dict, bytes]:
    """Forward request to upstream Ollama. Returns (status, headers, body)."""
    url = upstream.rstrip("/") + path
    req = urllib.request.Request(url, data=body or None, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=310) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:
        log.error("Upstream error: %s", e)
        return 502, {}, json.dumps({"error": str(e)}).encode()


class SidecarHandler(http.server.BaseHTTPRequestHandler):
    upstream: str

    def log_message(self, fmt, *args):
        pass  # suppress default access log; we do our own

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _proxy(self, needs_patch: bool):
        body = self._read_body()
        forward_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length")
        }
        if body:
            forward_headers["Content-Length"] = str(len(body))

        status, resp_headers, resp_body = forward_request(
            self.upstream, self.path, self.command, forward_headers, body
        )

        patch_count = 0
        if needs_patch and resp_body:
            try:
                text = resp_body.decode("utf-8")
                patched, patch_count = apply_patches(text)
                if patch_count:
                    resp_body = patched.encode("utf-8")
            except UnicodeDecodeError:
                pass  # binary response, skip patching

        self.send_response(status)
        skip_headers = {"transfer-encoding", "content-length"}
        for k, v in resp_headers.items():
            if k.lower() not in skip_headers:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        if patch_count:
            self.send_header("X-Security-Patched", str(patch_count))
        self.end_headers()
        self.wfile.write(resp_body)

        log.info("%s %s → %d%s", self.command, self.path, status,
                 f" [patched={patch_count}]" if patch_count else "")

    def do_GET(self):
        self._proxy(needs_patch=False)

    def do_POST(self):
        needs_patch = self.path.split("?")[0] in PATCHED_PATHS
        self._proxy(needs_patch=needs_patch)

    def do_DELETE(self):
        self._proxy(needs_patch=False)

    def do_HEAD(self):
        self._proxy(needs_patch=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--upstream", default=os.environ.get("OLLAMA_UPSTREAM", "http://localhost:11435"))
    p.add_argument("--port", type=int, default=int(os.environ.get("SIDECAR_PORT", "11434")))
    args = p.parse_args()

    SidecarHandler.upstream = args.upstream
    log.info("Security filter sidecar listening on :%d → %s", args.port, args.upstream)
    log.info("Patching paths: %s", PATCHED_PATHS)
    log.info("Active patches: %d", len(SECURITY_PATCHES))

    server = http.server.HTTPServer(("0.0.0.0", args.port), SidecarHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
