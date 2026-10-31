"""Stub MCP servers for the owui-tool-search bench (homelab#1871 AC2).

Serves each server in tools.lock.json at /<server>/mcp with its REAL tool
names, descriptions and input schemas, captured from the live servers. Every
call returns canned JSON and touches nothing, so a wrong pick in the bench
can't add to the Kroger cart or edit a Mealie recipe.

Runs in the open-webui image (it ships mcp, starlette and uvicorn):
    python3 stub_mcp.py /config/tools.lock.json   # listens on :8080
"""

import contextlib
import json
import sys

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route


def canned(server: str, tool: str, args: dict) -> dict:
    """A plausible, inert reply. The bench scores the CALL, not the answer."""
    return {"stub": True, "server": server, "tool": tool, "args": args,
            "result": "ok (canned bench reply; nothing was read or changed)"}


def build_server(name: str, specs: list[dict]) -> Server:
    tools = [types.Tool(name=s["name"], description=s.get("description") or "",
                        inputSchema=s.get("inputSchema") or {"type": "object"})
             for s in specs]

    async def on_list_tools(ctx, params):
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params):
        body = canned(name, params.name, params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(body))])

    return Server(name, version="bench", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def build_app(lock: dict) -> Starlette:
    managers = {n: StreamableHTTPSessionManager(build_server(n, specs), stateless=True, json_response=True)
                for n, specs in lock["servers"].items() if isinstance(specs, list)}

    def asgi(manager):
        async def app(scope, receive, send):
            await manager.handle_request(scope, receive, send)
        return app

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as stack:
            for m in managers.values():
                await stack.enter_async_context(m.run())
            yield

    routes = [Route("/healthz", lambda r: PlainTextResponse("ok"))]
    routes += [Mount(f"/{n}/mcp", app=asgi(m)) for n, m in managers.items()]
    return Starlette(routes=routes, lifespan=lifespan)


if __name__ == "__main__":
    lock = json.load(open(sys.argv[1]))
    uvicorn.run(build_app(lock), host="0.0.0.0", port=8080, log_level="warning")
