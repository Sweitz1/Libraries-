"""Model Context Protocol (MCP) client: use any MCP server's tools as Libra skills.

Supports both transports:

* **stdio**: Libra starts the server as a child process and talks JSON-RPC
  over its stdin/stdout (one JSON message per line).
* **Streamable HTTP**: JSON-RPC over POST to a URL; replies may be plain JSON
  or server-sent events; the ``Mcp-Session-Id`` header is kept.

Servers are configured in ``~/.libra/mcp.json`` in the same shape other MCP
apps use::

    {"mcpServers": {
        "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/docs"]},
        "remote": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${MY_TOKEN}"}}
    }}

``${VAR}`` in env values and headers is filled from your environment, so
tokens don't have to be written into the file. ``sync`` lists each server's
tools into the skill catalog as ``mcp.<server>.<tool>``. A server is only
started when one of its tools is first used.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import re
import subprocess
import threading
import urllib.error
import urllib.request

from . import home
from .catalog import Catalog, Entry, safe_name
from .trust import infer_risk

PROTOCOL_VERSION = "2025-06-18"
CONFIG = "mcp.json"
CLIENT_INFO = {"name": "libra", "version": "0.5.0"}


class MCPError(RuntimeError):
    pass


def _expand(value: str) -> str:
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)


def load_config() -> dict:
    return home.read_json(CONFIG, {"mcpServers": {}}).get("mcpServers", {})


def save_config(servers: dict) -> None:
    home.write_json(CONFIG, {"mcpServers": servers})


# ----------------------------------------------------------------- transports
class StdioTransport:
    def __init__(self, command: str, args=(), env=None, cwd=None, timeout: float = 60):
        full_env = {**os.environ, **{k: _expand(str(v)) for k, v in (env or {}).items()}}
        log = open(home.path("mcp-stderr.log"), "ab")
        try:
            self.proc = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=log, env=full_env, cwd=cwd)
        except FileNotFoundError as exc:
            raise MCPError(f"cannot start MCP server: {command} not found") from exc
        finally:
            log.close()
        self.timeout = timeout
        self._inbox: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self._write_lock = threading.Lock()

    def _read(self) -> None:
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                self._inbox.put(json.loads(line))
            except json.JSONDecodeError:
                continue  # servers sometimes print non-protocol text
        self._inbox.put(None)  # EOF

    def send(self, message: dict) -> None:
        data = (json.dumps(message) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MCPError("MCP server closed its input") from exc

    def request(self, message: dict) -> dict:
        self.send(message)
        while True:
            try:
                msg = self._inbox.get(timeout=self.timeout)
            except queue.Empty:
                raise MCPError(f"MCP server did not answer {message['method']} within {self.timeout:.0f}s") from None
            if msg is None:
                raise MCPError(f"MCP server exited (see {home.path('mcp-stderr.log')})")
            if "method" in msg and "id" in msg:  # a request from the server (e.g. ping)
                self._answer_server(msg)
                continue
            if msg.get("id") == message["id"]:
                return msg

    def _answer_server(self, msg: dict) -> None:
        if msg["method"] == "ping":
            self.send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        elif msg["method"] == "roots/list":
            self.send({"jsonrpc": "2.0", "id": msg["id"], "result": {"roots": []}})
        else:
            self.send({"jsonrpc": "2.0", "id": msg["id"],
                       "error": {"code": -32601, "message": f"Libra does not support {msg['method']}"}})

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


class HTTPTransport:
    def __init__(self, url: str, headers=None, timeout: float = 60):
        if not url.startswith(("http://", "https://")):
            raise MCPError(f"MCP url must be http(s): {url}")
        self.url = url
        self.headers = {k: _expand(str(v)) for k, v in (headers or {}).items()}
        self.timeout = timeout
        self.session_id = None

    def _post(self, message: dict):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION, **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, json.dumps(message).encode("utf-8"), headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read(2000).decode("utf-8", "replace")
            raise MCPError(f"MCP server returned HTTP {exc.code}: {body[:300]}") from None
        except urllib.error.URLError as exc:
            raise MCPError(f"cannot reach MCP server {self.url}: {exc.reason}") from None
        self.session_id = resp.headers.get("Mcp-Session-Id") or self.session_id
        return resp

    def send(self, message: dict) -> None:
        self._post(message).close()

    def request(self, message: dict) -> dict:
        with self._post(message) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                data_lines = []
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        msg = json.loads("\n".join(data_lines))
                        data_lines = []
                        if msg.get("id") == message["id"] and "method" not in msg:
                            return msg
                raise MCPError("MCP stream ended without a reply")
            return json.loads(resp.read().decode("utf-8") or "{}")

    def close(self) -> None:
        if self.session_id:
            try:
                headers = {"Mcp-Session-Id": self.session_id, **self.headers}
                urllib.request.urlopen(urllib.request.Request(self.url, headers=headers, method="DELETE"), timeout=5)
            except Exception:
                pass


# --------------------------------------------------------------------- client
class MCPClient:
    def __init__(self, name: str, config: dict, timeout: float = 60):
        self.name = name
        if config.get("url"):
            self.transport = HTTPTransport(config["url"], config.get("headers"), timeout)
        elif config.get("command"):
            self.transport = StdioTransport(config["command"], config.get("args", []), config.get("env"),
                                            config.get("cwd"), timeout)
        else:
            raise MCPError(f"MCP server {name!r} needs a 'command' or a 'url'")
        self._next = 0
        self._lock = threading.Lock()
        result = self._call("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                           "clientInfo": CLIENT_INFO})
        self.server_info = result.get("serverInfo", {})
        self.transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _call(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self._next += 1
            msg = {"jsonrpc": "2.0", "id": self._next, "method": method}
            if params is not None:
                msg["params"] = params
            reply = self.transport.request(msg)
        if "error" in reply:
            err = reply["error"]
            raise MCPError(f"{self.name}: {err.get('message', err)}")
        return reply.get("result", {})

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            result = self._call("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, tool: str, arguments: dict) -> dict:
        return self._call("tools/call", {"name": tool, "arguments": arguments})

    def close(self) -> None:
        self.transport.close()


_clients: dict[str, MCPClient] = {}
_clients_lock = threading.Lock()


def client(server: str) -> MCPClient:
    """Connected client for a configured server, started on first use and reused."""
    with _clients_lock:
        if server not in _clients:
            servers = load_config()
            if server not in servers:
                raise MCPError(f"MCP server {server!r} is not configured (libra mcp list)")
            _clients[server] = MCPClient(server, servers[server])
        return _clients[server]


@atexit.register
def close_all() -> None:
    with _clients_lock:
        for c in _clients.values():
            try:
                c.close()
            except Exception:
                pass
        _clients.clear()


def result_text(result: dict) -> str:
    parts = []
    for item in result.get("content", []):
        kind = item.get("type")
        if kind == "text":
            parts.append(item.get("text", ""))
        elif kind == "resource":
            res = item.get("resource", {})
            parts.append(res.get("text") or f"[resource {res.get('uri', '')}]")
        elif kind == "resource_link":
            parts.append(f"[{item.get('name') or 'link'}] {item.get('uri', '')}")
        elif kind in ("image", "audio"):
            parts.append(f"[{kind}: {item.get('mimeType', '')}, {len(item.get('data', '')) * 3 // 4:,} bytes]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], indent=2, ensure_ascii=False))
    return "\n".join(parts)


def call_tool(server: str, tool: str, arguments: dict):
    from .skills import SkillResult

    name = f"mcp.{safe_name(server)}.{safe_name(tool)}"
    result = client(server).call_tool(tool, arguments)
    return SkillResult(name, not result.get("isError", False), result_text(result), result)


def tool_risk(tool: dict) -> str:
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True:
        return "danger"
    if ann.get("readOnlyHint") is True:
        return "read"
    return infer_risk(tool.get("name", ""), tool.get("description", ""))


def sync(server: str | None = None, catalog: Catalog | None = None, log=print) -> dict[str, int]:
    """List tools from one or all configured servers into the catalog."""
    catalog = catalog or Catalog()
    servers = load_config()
    names = [server] if server else sorted(servers)
    counts = {}
    for name in names:
        if name not in servers:
            raise MCPError(f"MCP server {name!r} is not configured")
        c = client(name)
        tools = c.list_tools()
        source = f"mcp.{safe_name(name)}"
        entries = [Entry(f"{source}.{safe_name(t['name'])}", t.get("description") or t.get("title") or "", "mcp",
                         source, tool_risk(t),
                         {"server": name, "tool": t["name"], "input_schema": t.get("inputSchema") or {}})
                   for t in tools if t.get("name")]
        catalog.add_many(entries, replace_source=source)
        counts[name] = len(entries)
        if log:
            label = c.server_info.get("name", name)
            log(f"  {name} ({label}): {len(entries)} tool(s)")
    return counts
