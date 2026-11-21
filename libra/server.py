"""OpenAI-compatible HTTP server (standard library only, no API key needed).

Endpoints: GET /health, GET /v1/models, POST /v1/chat/completions,
POST /v1/completions, GET /v1/skills, POST /v1/skills/<name>,
POST /v1/workspace/ask|correct|rule, POST /v1/translate, GET /v1/info, GET /v1/auto/status,
POST /v1/auto/run, and the phone/browser app at ``/``. Streaming uses server-sent events
like the OpenAI API, so any OpenAI-compatible client (including Nexus Agent) can connect.

With a token set, every endpoint except ``/health`` and the app's static files needs
``Authorization: Bearer <token>`` (the token doubles as the "API key" in OpenAI apps).
Repeated wrong tokens from one address are locked out for a while.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import chat
from .generate import SamplingParams, stream_text


WEBUI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
          "/icon.svg": ("icon.svg", "image/svg+xml"), "/icon-192.png": ("icon-192.png", "image/png"),
          "/icon-512.png": ("icon-512.png", "image/png"), "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
          "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"), "/favicon-32.png": ("favicon-32.png", "image/png"),
          "/favicon.ico": ("favicon-32.png", "image/png")}
OPEN_PATHS = {"/health", *STATIC}
MAX_BODY = 2_000_000
MAX_DRAIN = 16_000_000  # bodies bigger than this are cut off without reading
MAX_FAILS, FAIL_WINDOW = 10, 600


class Engine:
    def __init__(self, model, tokenizer, name: str = "libra", skills=None, auto_skills: bool = False,
                 workspace=None, token: str | None = None, auto_config: str | None = None):
        self.token = token or None
        self.auto_config = auto_config
        self.model_dir: str | None = None  # set to reload the model when a new one is trained
        self._model_mtime = 0.0
        self.auto_thread: threading.Thread | None = None
        self.agent_thread: threading.Thread | None = None
        self.agent_current: dict = {}
        self.auto_last: dict = {}
        self._fails: dict[str, list] = {}
        self._fails_lock = threading.Lock()
        self.workspace = workspace
        self.model = model
        self.tokenizer = tokenizer
        self.name = name
        self.skills = skills
        self.auto_skills = auto_skills
        self.lock = threading.Lock()

    def watch_model(self, model_dir: str) -> None:
        """Reload the model whenever ``model_dir/model.npz`` changes (e.g. after a nightly training run)."""
        self.model_dir = model_dir
        path = os.path.join(model_dir, "model.npz")
        self._model_mtime = os.path.getmtime(path) if self.model is not None and os.path.exists(path) else 0.0

    def maybe_reload(self) -> None:
        if not self.model_dir:
            return
        path = os.path.join(self.model_dir, "model.npz")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if mtime == self._model_mtime:
            return
        from . import checkpoint

        with self.lock:
            if mtime != self._model_mtime:
                try:
                    self.model, self.tokenizer, _ = checkpoint.load(self.model_dir)
                    self._model_mtime = mtime
                    print(f"[server] loaded the updated model from {self.model_dir}")
                except Exception as exc:  # half-written or broken: keep the old one, try again next time
                    print(f"[server] could not load {self.model_dir}: {exc}")

    def route(self, messages: list[dict], requested: bool = False):
        """Run a skill if auto-skills is on (or the request asks for it) and the last message wants one."""
        if not ((self.auto_skills or requested) and self.skills and messages and messages[-1].get("role") == "user"):
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
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")

        def _client(self) -> str:
            return self.client_address[0]

        def _authorized(self, path: str) -> bool:
            """True if the request may go on; otherwise an error has been sent."""
            if engine.token is None or path in OPEN_PATHS:
                return True
            ip = self._client()
            now = time.time()
            with engine._fails_lock:
                fails = [t for t in engine._fails.get(ip, []) if now - t < FAIL_WINDOW]
                engine._fails[ip] = fails
                if len(fails) >= MAX_FAILS:
                    self._error(429, "too many wrong tokens from this address; try again in a few minutes")
                    return False
            auth = self.headers.get("Authorization", "")
            given = auth[7:].strip() if auth.lower().startswith("bearer ") else self.headers.get("X-Libra-Token", "")
            if given and hmac.compare_digest(given.encode(), engine.token.encode()):
                return True
            with engine._fails_lock:
                engine._fails.setdefault(ip, []).append(now)
            self._error(401, "missing or wrong token (send Authorization: Bearer <token>)")
            return False

        def _static(self, path: str) -> None:
            name, ctype = STATIC[path]
            with open(os.path.join(WEBUI, name), "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _info(self) -> dict:
            from . import home

            engine.maybe_reload()
            info = {"name": engine.name, "model": engine.model is not None, "workspace": None, "auto": None,
                    "skills": len(engine.skills.list()) if engine.skills else 0,
                    "catalog": engine.skills.catalog.count() if engine.skills and engine.skills.catalog else 0,
                    "translate": os.path.exists(home.path("translate.db")), "auth": engine.token is not None}
            if engine.model is not None:
                info["model_parameters"] = engine.model.num_parameters()
            if engine.workspace is not None:
                info["workspace"] = {"files": len(engine.workspace.sources), "rules": len(engine.workspace.rules),
                                     "corrections": len(engine.workspace.corrections)}
            if engine.auto_config:
                info["auto"] = {"config": os.path.basename(engine.auto_config),
                                "running": bool(engine.auto_thread and engine.auto_thread.is_alive())}
            return info

        def _agent_post(self, action: str, body: dict) -> None:
            from . import agent

            if engine.skills is None:
                self._error(404, "this server has no skills")
                return
            try:
                if action == "plan":
                    self._json(200, agent.plan(str(body.get("goal", "")), engine.skills).to_dict())
                elif action == "goals":
                    p = agent.add_goal(str(body.get("name", "")), str(body.get("goal", "")), body.get("every") or None,
                                       engine.skills, autonomous=bool(body.get("autonomous")))
                    self._json(200, {"saved": True, "plan": p.to_dict()})
                elif action == "remove":
                    self._json(200, {"removed": agent.remove_goal(str(body.get("name", "")))})
                elif action in ("run", "auto"):
                    if engine.agent_thread and engine.agent_thread.is_alive():
                        self._error(409, "the agent is already running a goal")
                        return
                    name = str(body.get("name") or "")
                    goal = str(body.get("goal") or "")
                    autonomous = action == "auto"
                    if name:
                        goals = agent.load_goals()
                        if name not in goals:
                            self._error(404, f"no saved goal {name!r}")
                            return
                        goal = goals[name]["goal"]
                        autonomous = autonomous or bool(goals[name].get("autonomous"))
                    p = agent.plan(goal, engine.skills)
                    if not p.ok and autonomous:
                        from .autonomy import _repair_plan

                        p = _repair_plan(p, engine.skills, [])
                    if not p.ok:
                        self._json(200, {"started": False, "plan": p.to_dict()})
                        return

                    def work():
                        steps = []
                        engine.agent_current = {"goal": goal, "name": name, "steps": steps, "autonomous": autonomous}
                        if autonomous:
                            from .autonomy import pursue

                            r = pursue(goal, engine.skills, name=name, log=steps.append)
                        else:
                            r = agent.execute(p, engine.skills, name=name, log=steps.append)
                        agent.record(r)

                    engine.agent_thread = threading.Thread(target=work, daemon=True)
                    engine.agent_thread.start()
                    self._json(202, {"started": True, "plan": p.to_dict()})
                else:
                    self._error(404, f"unknown agent action {action!r}")
            except ValueError as exc:
                self._error(400, str(exc))

        def _auto_run(self, dry_run: bool) -> None:
            if not engine.auto_config:
                self._error(404, "server was started without --auto-config")
                return
            if engine.auto_thread and engine.auto_thread.is_alive():
                self._error(409, "a training run is already in progress")
                return

            def work():
                from . import auto

                try:
                    report = auto.run(engine.auto_config, dry_run=dry_run, log=None)
                    engine.auto_last = {"ok": True, "decision": report.decision,
                                        "report": report.markdown(auto.load_config(engine.auto_config))}
                except Exception as exc:  # reported to the app instead of killing the server
                    engine.auto_last = {"ok": False, "decision": f"run failed: {exc}", "report": ""}

            engine.auto_last = {"ok": None, "decision": "running…", "report": ""}
            engine.auto_thread = threading.Thread(target=work, daemon=True)
            engine.auto_thread.start()
            self._json(202, {"started": True, "dry_run": dry_run})

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
            path = self.path.split("?")[0].rstrip("/") or "/"
            if not self._authorized(path):
                return
            if path in STATIC:
                self._static(path)
            elif path == "/health":
                self._json(200, {"status": "ok", "model": engine.name})
            elif path == "/v1/info":
                self._json(200, self._info())
            elif path == "/v1/agent":
                from . import agent

                running = bool(engine.agent_thread and engine.agent_thread.is_alive())
                self._json(200, {"goals": agent.load_goals(), "runs": agent.recent_runs(15),
                                 "running": running, "current": engine.agent_current if running else {}})
            elif path == "/v1/pair":
                # a link + QR code for adding another device; the base is the address this device used
                from urllib.parse import parse_qs, urlsplit

                from .pair import link, qr_svg

                base = (parse_qs(urlsplit(self.path).query).get("base") or [""])[0]
                parts = urlsplit(base)
                if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/"):
                    self._error(400, "base must be the app's address, like https://libra.example.ts.net")
                    return
                url = link(f"{parts.scheme}://{parts.netloc}", engine.token)
                self._json(200, {"url": url, "svg": qr_svg(url)})
            elif path == "/v1/auto/status":
                if not engine.auto_config:
                    self._error(404, "server was started without --auto-config")
                    return
                from . import auto

                try:
                    text = auto.status(engine.auto_config)
                except Exception as exc:
                    text = f"(could not read status: {exc})"
                running = bool(engine.auto_thread and engine.auto_thread.is_alive())
                try:
                    prog = auto.progress(engine.auto_config)
                except Exception:
                    prog = {}
                try:
                    running = running or auto.is_running(engine.auto_config)  # e.g. the nightly timer's run
                except Exception:
                    pass
                self._json(200, {"running": running, "status": text, "last": engine.auto_last, "progress": prog})
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
            if not self._authorized(path):
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    # Read and discard the upload first (up to a cap), so the client gets this error
                    # instead of a broken connection while it is still sending.
                    left = min(length, MAX_DRAIN)
                    while left > 0:
                        chunk = self.rfile.read(min(65536, left))
                        if not chunk:
                            break
                        left -= len(chunk)
                    self.close_connection = True
                    self._error(413, f"request body over {MAX_BODY // 1_000_000} MB")
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                params = _params(body)
            except (ValueError, TypeError) as exc:
                self._error(400, f"bad request: {exc}")
                return

            if path == "/v1/translate":
                from .translate import Translator

                try:
                    result = Translator().translate(str(body["text"]), str(body["to"]), str(body.get("source") or "auto"))
                except KeyError as exc:
                    self._error(400, f"missing field {exc}")
                    return
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                self._json(200, dict(result.to_dict(), display=result.describe()))
                return
            if path.startswith("/v1/agent/"):
                self._agent_post(path[len("/v1/agent/"):], body)
                return
            if path == "/v1/auto/run":
                self._auto_run(bool(body.get("dry_run")))
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
                engine.maybe_reload()
                messages = body.get("messages")
                if not isinstance(messages, list) or not messages:
                    self._error(400, "'messages' must be a non-empty list")
                    return
                result = engine.route(messages, bool(body.get("libra_auto_skills")))
                if result is not None:
                    self._respond_text(body, result.text, result.skill)
                    return
                use_model = body.get("libra_mode") == "model" and engine.model is not None
                if engine.workspace is not None and not use_model:
                    answer = engine.workspace.ask_chat(messages)
                    self._respond_text(body, answer.text, "workspace", extra={"libra_grounding": answer.to_dict()})
                    return
                if engine.model is None:
                    self._error(503, "no model loaded")
                    return
                prompt = chat.render(messages, add_generation_prompt=True)
                self._respond(body, params, prompt, chat_mode=True)
            elif path == "/v1/completions":
                engine.maybe_reload()
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


def lan_addresses() -> list[str]:
    """This machine's addresses on the local network (for printing phone URLs)."""
    import socket

    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outgoing interface
            found.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def serve(engine: Engine, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = make_server(engine, host, port)
    port = server.server_address[1]
    hosts = lan_addresses() if host in ("0.0.0.0", "") else [host]
    frag = f"#token={engine.token}" if engine.token else ""
    print(f"libra: serving '{engine.name}' on {host}:{port}")
    for h in hosts or [host]:
        print(f"  app:  http://{h}:{port}/{frag}")
    print(f"  API:  http://{(hosts or [host])[0]}:{port}/v1" + (" (API key: the token)" if engine.token else
                                                             " (no token: only use on this machine)"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
