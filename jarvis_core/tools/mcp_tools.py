"""Allgemeiner stdio-MCP-Adapter für vom Nutzer konfigurierte Server."""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from .extension_utils import S, confirm, result
from .process import clean_env
from .registry import Tool, ToolError


def server_config(ctx, name):
    cfg = ctx.cfg.extensions.get("mcp") or {}
    server = (cfg.get("servers") or {}).get(name)
    if not cfg.get("enabled") or not isinstance(server, dict) or not server.get("command"):
        raise ToolError("MCP-Server nicht eingerichtet: extensions.mcp.enabled und servers konfigurieren")
    if not isinstance(server.get("args", []), list) or not all(isinstance(x, str) for x in server.get("args", [])):
        raise ToolError("MCP args muss eine Stringliste sein")
    return server


async def request(server, name=None, arguments=None):
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from jsonschema import validate
    except ImportError:
        raise ToolError("MCP-Abhängigkeiten fehlen: pip install -r requirements-tools.txt") from None
    env = clean_env()
    for key in server.get("env_names", []):
        if key in os.environ:
            env[key] = os.environ[key]
    params = StdioServerParameters(command=str(server["command"]), args=server.get("args", []), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            available, cursor = {}, None
            for _ in range(10):
                page = await session.list_tools(cursor=cursor)
                available.update({t.name: t for t in page.tools})
                if len(available) > 1000:
                    raise ToolError("MCP-Server bietet zu viele Werkzeuge an (Limit 1000)")
                cursor = getattr(page, "nextCursor", None)
                if not cursor:
                    break
            if name is None:
                return {"tools": [{"name": t.name, "description": (t.description or "")[:500],
                                   "inputSchema": t.inputSchema} for t in available.values()]}
            if name not in available:
                raise ToolError("Werkzeug nicht vom konfigurierten Server angeboten")
            validate(arguments or {}, available[name].inputSchema)
            reply = await session.call_tool(name, arguments or {})
            text = "\n".join(c.text for c in reply.content if getattr(c, "type", None) == "text")
            return {"ok": not bool(getattr(reply, "isError", False)), "text": text[:15000],
                    "non_text_blocks": sum(getattr(c, "type", None) != "text" for c in reply.content)}


def invoke(ctx, a, call=False):
    server = server_config(ctx, a["server"])
    async def bounded():
        task = asyncio.create_task(request(server, a.get("tool") if call else None, a.get("arguments")))
        try:
            async with asyncio.timeout(45):
                while not task.done():
                    if ctx.cancel.is_set():
                        raise ToolError("MCP-Aufruf abgebrochen")
                    await asyncio.sleep(.05)
                return await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    try:
        data = asyncio.run(bounded())
    except ToolError:
        raise
    except Exception as exc:
        # Server-Ausgaben/Tracebacks können Zugangsdaten enthalten: nur Fehlertyp melden.
        raise ToolError(f"MCP-Aufruf fehlgeschlagen ({type(exc).__name__}); Serverkonfiguration und Logs prüfen") from None
    if not call and a.get("filter"):
        data["tools"] = [t for t in data["tools"] if str(a["filter"]).lower() in t["name"].lower()]
    return result(data, data.get("ok", True))


def tools():
    return [
        Tool("mcp_list_tools", "mcp", "Werkzeuge eines konfigurierten stdio-MCP-Servers samt Eingabeschema entdecken.",
             {"server": S, "filter": S}, ("server",), lambda c, a: invoke(c, a), confirm("MCP SERVER STARTEN / WERKZEUGE LESEN"), untrusted=True),
        Tool("mcp_call", "mcp", "Entdecktes Werkzeug auf konfiguriertem MCP-Server aufrufen; Argumente gegen dessen Schema prüfen.",
             {"server": S, "tool": S, "arguments": {"type": "object"}}, ("server", "tool"),
             lambda c, a: invoke(c, a, True), confirm("MCP WERKZEUG AUSFÜHREN"), untrusted=True),
    ]
