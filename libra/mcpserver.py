"""Model Context Protocol (MCP) server: expose Libra's skills as MCP tools.

The mirror of :mod:`libra.mcp` (which is an MCP *client*). This lets an MCP host
— Claude Code, the Claude desktop app, or any MCP client — list and call Libra's
skills (``codex_search``, ``codex_get``, ``worldbook``, the fanfiction and card
tools, ``calc``, translation, …) as native tools.

Transport is JSON-RPC 2.0 over **stdio**: one JSON message per line on stdin,
one per line on stdout. Nothing else may go to stdout (it would corrupt the
protocol), so all logging goes to stderr. Start it with::

    libra mcp serve

and point the host at that command. In Claude Code, for example::

    claude mcp add libra -- libra mcp serve

Every ``tools/call`` is run through the skill registry's :meth:`run`, so the
**trust policy is enforced unchanged**: read-only skills run; a write/danger
skill is refused (the reply says to run ``libra trust allow <name>`` first)
unless it is already allowed, because an MCP host can't be prompted mid-call.
Plugin skills appear only once the plugin is trusted (``libra trust
accept-plugin``), exactly as everywhere else.
"""
from __future__ import annotations

import json
import sys

from . import __version__

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "libra", "version": __version__}

# JSON-RPC 2.0 error codes we use.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def _annotations(risk: str) -> dict:
    """MCP tool hints derived from a skill's risk label."""
    if risk == "read":
        return {"readOnlyHint": True}
    if risk == "danger":
        return {"destructiveHint": True}
    return {}


def tool_list(reg) -> list[dict]:
    """Every in-memory skill as an MCP tool descriptor."""
    tools = []
    for s in reg.list():
        fn = s.schema().get("function", {})
        tools.append({
            "name": s.name,
            "description": fn.get("description") or s.description or "",
            "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
            "annotations": _annotations(getattr(s, "risk", "write")),
        })
    return tools


def _call_result(reg, name: str, arguments: dict) -> dict:
    result = reg.run(name, arguments or {})
    out: dict = {"content": [{"type": "text", "text": result.text or ""}], "isError": not result.ok}
    if isinstance(result.data, (dict, list)):
        out["structuredContent"] = json.loads(json.dumps(result.data, default=str))
    return out


def handle(reg, msg: dict) -> dict | None:
    """Answer one JSON-RPC message. Returns a response, or None for a notification."""
    if msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return {"jsonrpc": "2.0", "id": msg.get("id"),
                "error": {"code": INVALID_REQUEST, "message": "not a JSON-RPC 2.0 request"}}
    method, mid = msg["method"], msg.get("id")
    # notifications (no id) get no reply
    if mid is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL_VERSION,
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER_INFO})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_list(reg)})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if not name or name not in reg:
            return err(METHOD_NOT_FOUND, f"no such tool: {name!r}")
        try:
            return ok(_call_result(reg, name, params.get("arguments") or {}))
        except Exception as exc:  # a crash is reported as a tool error, not a transport error
            return ok({"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True})
    return err(METHOD_NOT_FOUND, f"Libra's MCP server does not support {method}")


def serve(reg, inp=None, out=None, log=None) -> None:
    """Read line-delimited JSON-RPC from ``inp`` and write replies to ``out`` (defaults: stdin/stdout)."""
    inp = inp if inp is not None else sys.stdin
    out = out if out is not None else sys.stdout
    log = log if log is not None else (lambda m: print(m, file=sys.stderr, flush=True))
    log(f"libra mcp server {__version__} ready ({len(reg.list())} tools); waiting on stdin")
    for raw in inp:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "invalid JSON"}}
            out.write(json.dumps(resp) + "\n")
            out.flush()
            continue
        # a batch (JSON array) is handled message by message
        messages = msg if isinstance(msg, list) else [msg]
        replies = []
        for one in messages:
            try:
                resp = handle(reg, one) if isinstance(one, dict) else {
                    "jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "not an object"}}
            except Exception as exc:  # never let one bad message kill the server
                resp = {"jsonrpc": "2.0", "id": one.get("id") if isinstance(one, dict) else None,
                        "error": {"code": INTERNAL_ERROR, "message": str(exc)}}
            if resp is not None:
                replies.append(resp)
        for resp in replies:
            out.write(json.dumps(resp, default=str) + "\n")
        out.flush()
    log("stdin closed; libra mcp server stopping")
