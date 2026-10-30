```dockerfile
FROM python:3.15-slim

# Install the upstream Kroger MCP server from PyPI. The package is
# `kroger-mcp` (PyPI canonical), built from CupOfOwls/kroger-mcp source
# (FastMCP 2.x library, MIT-licensed).
#
# Pin to a specific version rather than tracking latest so builds are
# reproducible. Bump this version when sync'ing upstream changes after
# a regression check.
ARG KROGER_MCP_VERSION=0.3.1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install upstream + our pinned runtime deps. fastmcp is jlowin's package,
# separate from the mcp SDK's bundled FastMCP — different API from mealie-mcp's
# upstream.
#
# fastmcp 3.x: kroger-mcp 0.3.0+ REQUIRES fastmcp 3.x (tested against 3.4.4) and
# fixed its `Image` import for it (upstream #17), so the earlier `fastmcp<3` pin
# — and our fastmcp.Image shim in wrapper.py — are no longer needed. Bound <4 to
# avoid an unvetted future major. kroger-api>=0.3.0 is pulled transitively by
# kroger-mcp 0.3.x.
RUN pip install --no-cache-dir \
    "fastmcp>=3,<4" \
    "kroger-mcp==${KROGER_MCP_VERSION}" \
    "uvicorn[standard]>=0.30" && \
    # pip is build-time only; its vendored urllib3/msgpack/setuptools carry CVEs (homelab#1697).
    python3 -m pip uninstall -y pip

# Our cluster-resident entrypoint — wraps the upstream MCP server with
# streamable-http transport instead of stdio. Same family of fixes as
# mealie-mcp but for jlowin's fastmcp 2.x API.
COPY src/server.py /app/wrapper.py

# KROGER_TOKEN_DIR is where kroger-mcp 0.3.0+ persists ALL local state (OAuth
# tokens + kroger_cart.json + kroger_order_history.json) — it replaced the old
# CWD-based storage (and the `KROGER_CART_DIR` env, which 0.3.x no longer reads).
# Point it at the /data PVC so shadow state survives restarts. KROGER_CART_DIR is
# kept only for wrapper.py's defensive chdir (harmless no-op on 0.3.x).
ENV MCP_PORT=8765 \
    KROGER_TOKEN_DIR=/data \
    KROGER_CART_DIR=/data

# /data is the PVC mount point for kroger_cart.json + kroger_order_history.json
# (shadow state — Kroger Public API doesn't expose cart-read, so we keep
# our own record of what's been added in this dir).
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request,urllib.error,sys; \
        try: urllib.request.urlopen('http://127.0.0.1:${MCP_PORT}/mcp', timeout=3); sys.exit(0); \
        except urllib.error.HTTPError as e: sys.exit(0 if e.code in (405,406,400,406) else 1); \
        except: sys.exit(1)" || exit 1

ENTRYPOINT ["python3", "/app/wrapper.py"]
```
