"""A tiny MCP server over stdio, for tests. Tools: echo, add, delete_everything (destructive)."""
import json
import sys

TOOLS = [
    {"name": "echo", "description": "Echo text back", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "add", "description": "Add two integers",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                     "required": ["a", "b"]}},
    {"name": "delete_everything", "description": "Deletes all records", "annotations": {"destructiveHint": True},
     "inputSchema": {"type": "object", "properties": {}}},
]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


print("this line is not JSON and must be ignored", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        continue  # notification
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg["params"]["protocolVersion"],
              "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}})
    elif method == "tools/list":
        cursor = (msg.get("params") or {}).get("cursor")
        page = TOOLS[:2] if not cursor else TOOLS[2:]  # two pages, to test pagination
        result = {"tools": page}
        if not cursor:
            result["nextCursor"] = "page2"
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments", {})
        send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "working"}})
        if name == "echo":
            result = {"content": [{"type": "text", "text": args["text"]}]}
        elif name == "add":
            result = {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
        else:
            result = {"content": [{"type": "text", "text": "refusing in tests"}], "isError": True}
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no such method"}})
