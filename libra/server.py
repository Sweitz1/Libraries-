"""OpenAI-compatible HTTP server (standard library only, no API key needed).

Endpoints: GET /health, GET /v1/models, POST /v1/chat/completions,
POST /v1/completions, GET /v1/skills, POST /v1/skills/<name>,
POST /v1/workspace/ask|correct|rule. Streaming uses server-sent events like the OpenAI API,
so any OpenAI-compatible client (including Nexus Agent) can connect.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import chat
from .generate import SamplingParams, stream_text


class Engine:
    def __init__(self, model, tokenizer, name: str = "libra", skills=None, auto_skills: bool = False,
                 workspace=None):
        self.workspace = workspace
        self.model = model
        self.tokenizer = tokenizer
        self.name = name
        self.skills = skills
        self.auto_skills = auto_skills
        self.lock = threading.Lock()

    def route(self, messages: list[dict]):
        """Run a skill if auto-skills is on and the last user message asks for one."""
        if not (self.auto_skills and self.skills and messages and messages[-1].get("role") == "user"):
            return None
        call = self.skills.route(chat._content(messages[-1]))
        return self.skills.run(*call) if call else None

    def run(self, prompt: str, params: SamplingParams, stop_strings: list[str], stop_tokens):
        """Yield ``("text", piece)`` items, then ``("done", finish_reason, usage)``."""
        stops = [s for s in stop_strings if s]
        hold = max((len(s) for s in stops), default=1) - 1
        prompt_tokens = len(self.tokenizer.encode(prompt))
        text, emitted, n_tokens, finish = "", 0, 0, "stop"
        with self.lock:
            for piece in stream_text(self.model, self.tokenizer, prompt, params, stop_tokens):
                n_tokens += 1
                text += piece
                hits = [i for i in (text.find(s) for s in stops) if i >= 0]
                if hits:
                    cut = min(hits)
                    if cut > emitted:
                        yield "text", text[emitted:cut]
                    emitted = len(text)
                    break
                safe = len(text) - hold
                if safe > emitted:
                    yield "text", text[emitted:safe]
                    emitted = safe
            else:
                if n_tokens >= params.max_tokens:
                    finish = "length"
                if len(text) > emitted:
                    yield "text", text[emitted:]
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": n_tokens,
                 "total_tokens": prompt_tokens + n_tokens}
        yield "done", finish, usage


def _params(body: dict) -> SamplingParams:
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens") or 256
    return SamplingParams(
        max_tokens=max(1, min(int(max_tokens), 4096)),
        temperature=float(body.get("temperature", 0.8)),
        top_k=int(body.get("top_k", 0) or 0),
        top_p=float(body.get("top_p", 0.95)),
        repetition_penalty=float(body.get("repetition_penalty", 1.0) or 1.0),
        seed=body.get("seed"),
    )


def _stops(body: dict) -> list[str]:
    stop = body.get("stop")
    if stop is None:
        return []
    return [stop] if isinstance(stop, str) else [str(s) for s in stop]


def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        server_version = "libra"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quieter default logging
            print(f"[server] {self.address_string()} {fmt % args}")

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _json(self, status: int, obj) -> None:
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": {"message": message, "type": "invalid_request_error"}})

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path in ("", "/health"):
                self._json(200, {"status": "ok", "model": engine.name})
            elif path == "/v1/skills":
                from urllib.parse import parse_qs, urlsplit

                qs = parse_qs(urlsplit(self.path).query)
                if engine.skills is None:
                    found = []
                elif qs.get("q"):
                    found = engine.skills.search(qs["q"][0], max(1, min(100, int((qs.get("limit") or ["20"])[0]))))
                else:
                    found = engine.skills.list()
                total = engine.skills.catalog.count() if engine.skills and engine.skills.catalog else 0
                self._json(200, {"object": "list", "catalog_size": total, "data": [
                    dict(s.schema(), source=s.source, risk=s.risk) for s in found]})
            elif path == "/v1/models":
                self._json(200, {"object": "list", "data": [
                    {"id": engine.name, "object": "model", "created": 0, "owned_by": "local"}]})
            else:
                self._error(404, f"unknown path {self.path}")

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                params = _params(body)
            except (ValueError, TypeError) as exc:
                self._error(400, f"bad request: {exc}")
                return

            if path in ("/v1/workspace/ask", "/v1/workspace/correct", "/v1/workspace/rule"):
                ws = engine.workspace
                if ws is None:
                    self._error(404, "server was started without --workspace")
                    return
                try:
                    if path.endswith("ask"):
                        out = ws.ask(str(body["question"]), body.get("clarify")).to_dict()
                    elif path.endswith("correct"):
                        out = ws.correct(str(body["question"]), str(body["answer"]))
                    else:
                        out = ws.add_rule(str(body["rule"]))
                except KeyError as exc:
                    self._error(400, f"missing field {exc}")
                    return
                self._json(200, out)
            elif path.startswith("/v1/skills/"):
                name = path[len("/v1/skills/"):]
                if engine.skills is None or name not in engine.skills:  # built-ins, plugins and the catalog
                    self._error(404, f"unknown skill {name!r}")
                    return
                args = body.get("arguments", body)
                if isinstance(args, str):  # OpenAI tool-call style: arguments as a JSON string
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        self._error(400, "arguments must be JSON")
                        return
                result = engine.skills.run(name, args)
                blocked = not result.ok and ("needs your approval" in result.text or "is blocked" in result.text)
                self._json(403 if blocked else 200, result.to_dict())
            elif path == "/v1/chat/completions":
                messages = body.get("messages")
                if not isinstance(messages, list) or not messages:
                    self._error(400, "'messages' must be a non-empty list")
                    return
                result = engine.route(messages)
                if result is not None:
                    self._respond_text(body, result.text, result.skill)
                    return
                if engine.workspace is not None:
                    answer = engine.workspace.ask_chat(messages)
                    self._respond_text(body, answer.text, "workspace", extra={"libra_grounding": answer.to_dict()})
                    return
                if engine.model is None:
                    self._error(503, "no model loaded")
                    return
                prompt = chat.render(messages, add_generation_prompt=True)
                self._respond(body, params, prompt, chat_mode=True)
            elif path == "/v1/completions":
                if engine.model is None:
                    self._error(503, "no model loaded (grounded workspace mode only answers chat requests)")
                    return
                prompt = body.get("prompt", "")
                if isinstance(prompt, list):
                    prompt = "".join(map(str, prompt))
                self._respond(body, params, str(prompt), chat_mode=False)
            else:
                self._error(404, f"unknown path {self.path}")

        def _respond_text(self, body, text: str, skill_name: str, extra: dict | None = None):
            """Reply with a skill's output in the normal chat-completion format."""
            cid = "chatcmpl-" + uuid.uuid4().hex[:24]
            base = {"id": cid, "created": int(time.time()), "model": engine.name, "libra_skill": skill_name,
                    **(extra or {})}
            if not body.get("stream"):
                self._json(200, dict(base, object="chat.completion", choices=[
                    {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()
            self.close_connection = True
            for delta, finish in (({"role": "assistant", "content": text}, None), ({}, "stop")):
                chunk = dict(base, object="chat.completion.chunk",
                             choices=[{"index": 0, "delta": delta, "finish_reason": finish}])
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def _respond(self, body, params, prompt, chat_mode: bool):
            cid = ("chatcmpl-" if chat_mode else "cmpl-") + uuid.uuid4().hex[:24]
            created = int(time.time())
            obj = "chat.completion" if chat_mode else "text_completion"
            stop_tokens = chat.STOP_TOKENS if chat_mode else (chat.EOT,)
            events = engine.run(prompt, params, _stops(body), stop_tokens)

            if not body.get("stream"):
                parts, finish, usage = [], "stop", {}
                for ev in events:
                    if ev[0] == "text":
                        parts.append(ev[1])
                    else:
                        _, finish, usage = ev
                text = "".join(parts)
                choice = ({"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}
                          if chat_mode else {"index": 0, "text": text, "finish_reason": finish})
                self._json(200, {"id": cid, "object": obj, "created": created, "model": engine.name,
                                 "choices": [choice], "usage": usage})
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()
            self.close_connection = True

            def send(payload) -> None:
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                self.wfile.flush()

            def chunk(delta=None, text=None, finish=None):
                choice = ({"index": 0, "delta": delta or {}, "finish_reason": finish}
                          if chat_mode else {"index": 0, "text": text or "", "finish_reason": finish})
                return {"id": cid, "object": obj + ".chunk" if chat_mode else obj, "created": created,
                        "model": engine.name, "choices": [choice]}

            try:
                if chat_mode:
                    send(chunk(delta={"role": "assistant", "content": ""}))
                for ev in events:
                    if ev[0] == "text":
                        send(chunk(delta={"content": ev[1]}, text=ev[1]))
                    else:
                        send(chunk(finish=ev[1]))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                events.close()

    return Handler


def make_server(engine: Engine, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(engine))
    server.daemon_threads = True
    return server


def serve(engine: Engine, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = make_server(engine, host, port)
    print(f"libra: serving '{engine.name}' on http://{host}:{server.server_address[1]}/v1 (no API key required)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
