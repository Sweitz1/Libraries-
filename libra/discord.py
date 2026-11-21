"""Libra on Discord: a bot that answers in your server and in direct messages.

    libra discord setup                    # paste the bot token once (hidden), get the invite link
    libra discord allow <your-user-id>     # who may give goals and change memory
    libra discord run --workspace ~/ws     # connect (the Debian service does this for you)

The bot connects *out* to Discord's gateway, so nothing is opened on your network.
By default it answers direct messages and messages in a server that @mention it, which needs no
privileged intent. Without Discord's *Message Content* intent, Discord sends a bot **empty text** for any
other server message, so the ``!`` prefix only works in a server after you enable that intent on the
bot's page in the developer portal and run ``libra discord set message_content on``. With
``"listen_channels"`` it reads every message in those channels (that needs the intent too).

Commands (after a mention, in a DM, or with the prefix):

    help                       what it can do
    ask <question>             answer from your files (grounded, with sources)
    translate <text> to <lang> translation from your data
    goal <goal>                autonomous agent (allowed users)     plan <goal>   preview only
    goals / run <name>         saved goals (run: allowed users)
    remember <fact> / recall <words> / forget <words or #id>   memory (remember/forget: allowed users)
    skills [words]             find skills                         status   server and training status

Anything else is answered like the app's chat: skills (calculations, scraping, translation),
then your files, then your trained model. Skills that change something are refused
here, because nobody can approve them in Discord; allow them with ``libra trust`` if you
want the bot to use them.

With a bot token set up, Discord is also available as skills to goals and the app (:func:`register_skills`):
``discord_history`` (read a channel; outside content), ``discord_post`` and ``discord_react`` (changes, checked
against Discord afterwards). They act as the bot, never as a person's account.

The token comes from ``DISCORD_TOKEN`` or a token file (never the config); the config
lives in ``~/.libra/discord.json``.
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request

from . import home

API = "https://discord.com/api/v10"
GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
GUILDS, GUILD_MESSAGES, DIRECT_MESSAGES, MESSAGE_CONTENT = 1 << 0, 1 << 9, 1 << 12, 1 << 15
INVITE_PERMISSIONS = (1 << 6) | (1 << 10) | (1 << 11) | (1 << 16)  # add reactions, view channels, send messages, read message history
FATAL_CLOSE = {4004: "the bot token was rejected", 4010: "invalid shard", 4011: "sharding required",
               4012: "invalid API version", 4013: "invalid intents",
               4014: "a privileged intent isn't enabled: turn on 'Message Content Intent' in the Discord developer "
                     "portal (Bot page), or turn off message_content and listen_channels "
                     "(libra discord set message_content off; libra discord set listen_channels '')"}
USER_AGENT = "DiscordBot (libra, 0.10)"
DEFAULT_CONFIG = {"prefix": "!", "dm": True, "allowed_users": [], "channels": [], "listen_channels": [],
                  "notify_channel": ""}
LIMIT = 2000


class DiscordError(RuntimeError):
    pass


def load_config() -> dict:
    return {**DEFAULT_CONFIG, **home.read_json("discord.json", {})}


def save_config(cfg: dict) -> None:
    home.write_json("discord.json", cfg)


def read_token(token_file: str | None = None) -> str:
    if os.environ.get("DISCORD_TOKEN"):
        return os.environ["DISCORD_TOKEN"].strip()
    path = token_file or home.path("discord-token")
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        token = ""
    if not token:
        raise DiscordError(f"no Discord bot token: set DISCORD_TOKEN or run 'libra discord setup' (looked in {path})")
    return token


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Discord's 2000-character limit: split at blank lines, then lines, then spaces."""
    text = text.strip() or "(empty)"
    parts = []
    while len(text) > limit:
        cut = max(text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


def _snowflake(value) -> str:
    """Discord ids are numbers; anything else could change the API path, so it's refused."""
    value = str(value).strip()
    if not re.fullmatch(r"\d{1,25}", value):
        raise DiscordError(f"not a Discord id: {value!r} (copy it with Developer Mode: right-click → Copy ID)")
    return value


def _emoji(value: str) -> str:
    """A Unicode emoji, or a custom one as name:id."""
    value = str(value).strip().strip(":")
    if not value or len(value) > 64 or "/" in value or (":" in value and not re.fullmatch(r"\w{2,32}:\d{1,25}", value)):
        raise DiscordError(f"not an emoji: {value!r} (use the emoji itself, like 👍, or name:id for a custom one)")
    return value


class REST:
    def __init__(self, token: str, api_base: str = API, timeout: float = 20):
        self.token, self.base, self.timeout = token, api_base.rstrip("/"), timeout

    def request(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(4):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "Authorization": f"Bot {self.token}", "User-Agent": USER_AGENT, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    info = json.loads(raw) if raw else {}
                except ValueError:
                    info = {}
                if exc.code == 429 and attempt < 3:  # rate limited: wait as told, then retry
                    time.sleep(min(30.0, float(info.get("retry_after", 1.0))))
                    continue
                if exc.code >= 500 and attempt < 3:
                    time.sleep(1 + attempt)
                    continue
                raise DiscordError(f"Discord said {exc.code}: {info.get('message') or raw[:200]!r}") from None
            except urllib.error.URLError as exc:
                if attempt < 3:
                    time.sleep(1 + attempt)
                    continue
                raise DiscordError(f"can't reach Discord: {exc.reason}") from None
        raise DiscordError("Discord kept rate-limiting the request")

    def me(self) -> dict:
        return self.request("GET", "/users/@me")

    def send(self, channel_id: str, text: str, reply_to: str | None = None) -> list[str]:
        """Post ``text`` (split into Discord-sized parts). Returns the new messages' ids."""
        ids = []
        for i, part in enumerate(split_message(text)):
            body = {"content": part, "allowed_mentions": {"parse": []}}  # never ping @everyone/roles/users
            if reply_to and i == 0:
                body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
            sent = self.request("POST", f"/channels/{_snowflake(channel_id)}/messages", body)
            if isinstance(sent, dict) and sent.get("id"):
                ids.append(str(sent["id"]))
        return ids

    def messages(self, channel_id: str, limit: int = 20) -> list[dict]:
        """The channel's most recent messages, newest first."""
        return self.request("GET", f"/channels/{_snowflake(channel_id)}/messages?limit={max(1, min(int(limit), 100))}")

    def message(self, channel_id: str, message_id: str) -> dict:
        return self.request("GET", f"/channels/{_snowflake(channel_id)}/messages/{_snowflake(message_id)}")

    def react(self, channel_id: str, message_id: str, emoji: str) -> None:
        from urllib.parse import quote

        self.request("PUT", f"/channels/{_snowflake(channel_id)}/messages/{_snowflake(message_id)}/reactions/"
                            f"{quote(_emoji(emoji), safe='')}/@me")

    def typing(self, channel_id: str) -> None:
        try:
            self.request("POST", f"/channels/{channel_id}/typing")
        except DiscordError:
            pass


class Bot:
    def __init__(self, token: str, registry, workspace=None, model=None, tokenizer=None, config: dict | None = None,
                 api_base: str = API, gateway: str = GATEWAY, auto_config: str | None = None, log=print):
        self._watch = config is None  # loaded from ~/.libra/discord.json: pick up later changes (allow, set …)
        self.cfg = {**DEFAULT_CONFIG, **(config if config is not None else load_config())}
        self._cfg_mtime = self._config_mtime()
        self.token, self.registry, self.workspace = token, registry, workspace
        self.model, self.tokenizer, self.auto_config = model, tokenizer, auto_config
        self.rest = REST(token, api_base)
        self.gateway = gateway
        self.log = log or (lambda *a: None)
        self.user_id = None
        self.username = None
        self.session_id = None
        self.resume_url = None
        self.seq = None
        self._acked = True
        self._stop = threading.Event()
        self._ws = None
        self._busy = threading.Semaphore(2)  # at most two answers at a time, so heartbeats never starve
        self._agent_lock = threading.Lock()

    # ---------------------------------------------------------- gateway loop
    @property
    def intents(self) -> int:
        n = GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES
        return n | MESSAGE_CONTENT if self.cfg.get("listen_channels") or self.cfg.get("message_content") else n

    @property
    def reads_server_text(self) -> bool:
        """Whether Discord sends this bot the text of server messages that don't mention it."""
        return bool(self.intents & MESSAGE_CONTENT)

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close(1000)

    def run_forever(self) -> None:
        """Connect and keep reconnecting (with backoff) until stopped or Discord refuses for good."""
        from .websocket import ConnectionClosed

        delay = 1.0
        while not self._stop.is_set():
            try:
                self._session()
                delay = 1.0
            except ConnectionClosed as exc:
                if exc.code in FATAL_CLOSE:
                    raise DiscordError(f"Discord closed the connection: {FATAL_CLOSE[exc.code]}") from None
                if exc.code in (4007, 4009):  # bad sequence / session timed out: start a fresh session
                    self.session_id = self.seq = None
                self.log(f"[discord] connection closed ({exc.code}); reconnecting in {delay:.0f}s")
            except (OSError, ValueError) as exc:
                self.log(f"[discord] connection problem: {exc}; reconnecting in {delay:.0f}s")
            if self._stop.wait(delay):
                break
            delay = min(60.0, delay * 2)

    def _send(self, op: int, d) -> None:
        self._ws.send(json.dumps({"op": op, "d": d}))

    def _heartbeat(self, ws, interval: float) -> None:
        if self._stop.wait(interval * random.random()):
            return
        while not self._stop.is_set() and ws is self._ws and not ws.closed:
            if not self._acked:  # the last heartbeat got no answer: the connection is dead
                self.log("[discord] heartbeat not acknowledged; reconnecting")
                ws.close(4000)
                return
            self._acked = False
            try:
                self._send(1, self.seq)
            except OSError:
                return
            if self._stop.wait(interval):
                return

    def _session(self) -> None:
        from .websocket import WebSocket

        url = self.resume_url if (self.session_id and self.resume_url) else self.gateway
        if url != self.gateway and "?" not in url:
            url += "/?v=10&encoding=json"
        ws = WebSocket(url, timeout=60)
        self._ws = ws
        try:
            kind, raw = ws.recv()
            hello = json.loads(raw)
            if hello.get("op") != 10:
                raise ValueError(f"expected Hello, got op {hello.get('op')}")
            interval = hello["d"]["heartbeat_interval"] / 1000
            ws.settimeout(interval * 2 + 10)
            self._acked = True
            threading.Thread(target=self._heartbeat, args=(ws, interval), daemon=True).start()
            if self.session_id:
                self._send(6, {"token": self.token, "session_id": self.session_id, "seq": self.seq})
            else:
                self._send(2, {"token": self.token, "intents": self.intents,
                               "properties": {"os": "linux", "browser": "libra", "device": "libra"}})
            while not self._stop.is_set():
                kind, raw = ws.recv()
                msg = json.loads(raw)
                op = msg.get("op")
                if msg.get("s") is not None:
                    self.seq = msg["s"]
                if op == 0:
                    self._dispatch(msg.get("t"), msg.get("d") or {})
                elif op == 1:
                    self._send(1, self.seq)
                elif op == 11:
                    self._acked = True
                elif op == 7:  # Discord asks us to reconnect and resume
                    ws.close(4000)
                    return
                elif op == 9:  # invalid session
                    if not msg.get("d"):
                        self.session_id = self.seq = None
                    self._stop.wait(1 + random.random() * 4)
                    ws.close(4000)
                    return
        finally:
            ws.close()

    def _dispatch(self, event: str, d: dict) -> None:
        if event == "READY":
            self.user_id = d["user"]["id"]
            self.username = d["user"].get("username")
            self.session_id = d.get("session_id")
            self.resume_url = d.get("resume_gateway_url")
            self.log(f"[discord] connected as {d['user'].get('username')} ({self.user_id})")
            if not self.reads_server_text:
                self.log("[discord] in servers it answers @mentions only: Discord hides the text of other messages, "
                         "including '" + (self.cfg.get("prefix") or "!") + "' commands, unless the Message Content "
                         "intent is on (enable it in the developer portal, then: libra discord set message_content on)")
        elif event == "MESSAGE_CREATE":
            threading.Thread(target=self._on_message, args=(d,), daemon=True).start()

    # -------------------------------------------------------------- messages
    def addressed(self, msg: dict) -> str | None:
        """The text meant for the bot, or None if the message isn't for it."""
        author = msg.get("author") or {}
        if author.get("bot") or author.get("id") == self.user_id:
            return None
        content = msg.get("content") or ""
        channel = msg.get("channel_id")
        is_dm = not msg.get("guild_id")
        if not is_dm and self.cfg.get("channels") and channel not in self.cfg["channels"] \
                and channel not in self.cfg.get("listen_channels", []):
            return None
        mention = re.compile(rf"<@!?{re.escape(str(self.user_id))}>") if self.user_id else None
        mentioned = any(u.get("id") == self.user_id for u in msg.get("mentions") or []) or \
            bool(mention and mention.search(content))
        prefix = self.cfg.get("prefix") or ""
        text = mention.sub("", content).strip() if mention else content.strip()
        if prefix and text.startswith(prefix):
            return text[len(prefix):].strip()
        if mentioned or (is_dm and self.cfg.get("dm", True)) or channel in self.cfg.get("listen_channels", []):
            return text
        return None

    @staticmethod
    def _config_mtime() -> float:
        try:
            return os.path.getmtime(home.path("discord.json"))
        except OSError:
            return 0.0

    def _reload_config(self) -> None:
        if self._watch and self._config_mtime() != self._cfg_mtime:
            self._cfg_mtime = self._config_mtime()
            self.cfg = load_config()
            self.log("[discord] settings reloaded")

    def _on_message(self, msg: dict) -> None:
        self._reload_config()
        text = self.addressed(msg)
        if text is None:
            return
        channel, user = msg["channel_id"], (msg.get("author") or {}).get("id")
        with self._busy:
            self.rest.typing(channel)
            try:
                reply = self.respond(text, user, lambda m: self.rest.send(channel, m, msg.get("id")))
            except Exception as exc:  # never let one message kill the bot
                reply = f"Something went wrong: {type(exc).__name__}: {exc}"
            if reply:
                try:
                    self.rest.send(channel, reply, msg.get("id"))
                except DiscordError as exc:
                    self.log(f"[discord] couldn't reply: {exc}")

    def allowed(self, user_id) -> bool:
        return str(user_id) in {str(u) for u in self.cfg.get("allowed_users") or []}

    def respond(self, text: str, user_id, say=lambda m: None) -> str:
        """The reply to ``text`` from ``user_id``. ``say`` posts progress messages for long tasks."""
        from . import agent
        from .memory import Memory, describe

        word, _, rest = text.strip().partition(" ")
        cmd, rest = word.lower(), rest.strip()
        need = "Only users on the bot's allow list can do that (the owner runs: libra discord allow <your user ID>)."
        if cmd in ("", "help"):
            # In a server without Message Content, commands only arrive with an @mention: say so.
            p = self.cfg.get("prefix") or ""
            return HELP.format(p=p) if self.reads_server_text else \
                HELP.format(p="@" + (self.username or "Libra") + " ") + SERVER_NOTE.format(p=p)
        if cmd == "ask":
            if self.workspace is None:
                return "No files are connected to this bot (start it with --workspace)."
            return self.workspace.ask(rest).text
        if cmd == "plan":
            return agent.plan(rest, self.registry).describe()
        if cmd in ("goal", "auto"):
            if not self.allowed(user_id):
                return need
            if not rest:
                return "Tell me the goal, e.g. `goal scrape https://example.com/news then summarize it`"
            if not self._agent_lock.acquire(blocking=False):
                return "I'm already working on a goal; try again when it's done."
            try:
                from .autonomy import pursue

                say(f"Working on it… (I'll stop after at most 5 minutes)")
                r = pursue(rest, self.registry, name="discord")
                agent.record(r)
                return r.describe()
            finally:
                self._agent_lock.release()
        if cmd == "goals":
            goals = agent.load_goals()
            return "\n".join(f"**{n}** ({g.get('every') or 'manual'}): {g['goal']}" for n, g in sorted(goals.items())) \
                or "No saved goals."
        if cmd == "run":
            if not self.allowed(user_id):
                return need
            goals = agent.load_goals()
            if rest not in goals:
                return f"No saved goal called {rest!r}."
            say(f"Running {rest}…")
            g = goals[rest]
            return agent.run_goal(g["goal"], self.registry, name=rest, autonomous=bool(g.get("autonomous"))).describe()
        if cmd == "remember" or re.match(r"(?i)^remember\b", text):
            if not self.allowed(user_id):
                return need
            fact = re.sub(r"(?i)^remember\s+(that\s+)?", "", text).strip()
            return f"I'll remember that. (#{Memory().add(fact, source=f'discord:{user_id}')['id']})" if fact \
                else "Remember what?"
        if cmd == "recall":
            return describe(Memory().recall(rest))
        if cmd == "forget":
            if not self.allowed(user_id):
                return need
            gone = Memory().forget(rest.lstrip("#"))
            return f"Forgot {len(gone)}." if gone else "Nothing matched."
        if cmd == "skills":
            found = self.registry.search(rest, 10) if rest else self.registry.list()
            return "\n".join(f"`{s.name}` [{s.risk}] {s.description[:80]}" for s in found[:15]) or "No matching skills."
        if cmd == "status":
            return self._status()
        return self._chat(text)

    def _chat(self, text: str) -> str:
        call = self.registry.route(text)
        if call:
            return self.registry.run(*call).text
        if self.workspace is not None and self.workspace.sources:
            answer = self.workspace.ask(text)
            if answer.status != "not_found" or self.model is None:
                return answer.text
        if self.model is not None:
            from .chat import STOP_TOKENS, render
            from .generate import SamplingParams, generate

            prompt = render([{"role": "user", "content": text}], add_generation_prompt=True)
            out = generate(self.model, self.tokenizer, prompt, SamplingParams(max_tokens=200), STOP_TOKENS).strip()
            return out or "(the model had nothing to say)"
        return ("I couldn't answer that from my skills or files. Try `help`, or ask about something in the files "
                "connected to me.")

    def _status(self) -> str:
        lines = [f"Skills: {len(self.registry.list())} built-in/plugins"
                 + (f", {self.registry.catalog.count():,} in the catalog" if self.registry.catalog else "")]
        if self.workspace is not None:
            lines.append(f"Files: {len(self.workspace.sources)} indexed")
        lines.append("Model: " + (f"{self.model.num_parameters():,} parameters" if self.model else "none loaded"))
        if self.auto_config:
            from . import auto

            try:
                lines.append(auto.status(self.auto_config).split("\n")[0])
            except Exception:
                pass
        return "\n".join(lines)


SERVER_NOTE = """
In direct messages you can leave out the mention. (The `{p}` prefix works in servers once the owner turns on Discord's Message Content intent.)"""

HELP = """**Libra** — your own assistant, running on your machine.
`{p}ask <question>` — answer from your files, with sources
`{p}translate <text> to <language>` — translation from your data
`{p}goal <goal>` — I plan it, run my skills, check the result and adjust (allowed users)
`{p}plan <goal>` — show the plan without running it
`{p}goals` / `{p}run <name>` — saved goals
`{p}remember <fact>` / `{p}recall <words>` / `{p}forget <words>` — my memory
`{p}skills [words]` — what I can do · `{p}status`
Or just talk to me: calculations, "scrape https://…", "what is perro in English?"."""


# ------------------------------------------------------------------- skills
def available() -> bool:
    """A bot token is set up (``libra discord setup`` or ``DISCORD_TOKEN``)."""
    try:
        read_token()
        return True
    except DiscordError:
        return False


def register_skills(reg) -> None:
    """Discord as Libra skills, with the bot's token (never a personal account's): read a channel (outside
    content), and post or react (changes: they ask first, and their effect is checked)."""
    rest = lambda: REST(read_token(), api_base=API)  # noqa: E731  (API is read at call time, for tests)

    def _route_history(message: str):
        m = re.search(r"\b(?:read|check|get)\b.*\bdiscord channel (\d{1,25})\b", message, re.I)
        return {"channel": m.group(1)} if m else None

    def _route_post(message: str):
        m = re.search(r"\b(?:post|send|share)\b.*\b(?:to|in|on) (?:the )?discord channel (\d{1,25})\b", message, re.I)
        return {"channel": m.group(1), "text": "{prev}"} if m else None

    @reg.skill("discord_history", "Read the recent messages in a Discord channel (the bot must be in it).",
               {"channel": "the channel's id", "limit": "how many messages (at most 100)"},
               router=_route_history, risk="read", external=True)
    def discord_history(channel: str, limit: int = 20):
        from .skills import SkillResult

        msgs = rest().messages(channel, limit)
        lines = []
        for m in reversed(msgs):  # oldest first, like reading the channel
            who = (m.get("author") or {}).get("global_name") or (m.get("author") or {}).get("username") or "?"
            text = m.get("content") or ("(attachment)" if m.get("attachments") else
                                        "(no text: turn on the Message Content intent to read it)")
            lines.append(f"[{str(m.get('timestamp', ''))[:16].replace('T', ' ')}] {who}: {text}")
        data = [{"id": m.get("id"), "author": (m.get("author") or {}).get("username"), "text": m.get("content", ""),
                 "time": m.get("timestamp")} for m in msgs]
        return SkillResult("discord_history", True, "\n".join(lines) or "(no messages)", data)

    def _posted(args, result=None):
        if result is not None and isinstance(result.data, dict) and result.data.get("ids"):
            r = rest()  # every part that was sent is there, with the text that was sent
            parts = split_message(str(args.get("text", "")))
            return all(r.message(args["channel"], mid).get("content") == part
                       for mid, part in zip(result.data["ids"], parts))
        r = rest()  # after a crash: did the bot already post this text recently?
        me, first = r.me()["id"], split_message(str(args.get("text", "")))[0]
        return any((m.get("author") or {}).get("id") == me and m.get("content") == first
                   for m in r.messages(args["channel"], 50))

    @reg.skill("discord_post", "Post a message to a Discord channel as the bot (mentions never ping anyone).",
               {"channel": "the channel's id", "text": "what to post"}, router=_route_post, risk="write",
               verify=_posted, reconcile=_posted)
    def discord_post(channel: str, text: str):
        from .skills import SkillResult

        if not str(text).strip():
            raise ValueError("nothing to post")
        ids = rest().send(channel, text)
        return SkillResult("discord_post", True, f"Posted {len(ids)} message(s) to channel {channel}.", {"ids": ids})

    def _reacted(args, result=None):
        want = _emoji(args.get("emoji", ""))
        msg = rest().message(args["channel"], args["message"])
        for r in msg.get("reactions") or []:
            e = r.get("emoji") or {}
            name = f"{e.get('name')}:{e.get('id')}" if e.get("id") else e.get("name")
            if r.get("me") and name == want:
                return True
        return False

    @reg.skill("discord_react", "Add an emoji reaction to a Discord message as the bot.",
               {"channel": "the channel's id", "message": "the message's id", "emoji": "e.g. 👍, or name:id"},
               risk="write", verify=_reacted, reconcile=_reacted)
    def discord_react(channel: str, message: str, emoji: str):
        rest().react(channel, message, emoji)
        return f"Reacted {emoji} to message {message}."


def invite_url(application_id: str) -> str:
    return (f"https://discord.com/oauth2/authorize?client_id={application_id}&scope=bot"
            f"&permissions={INVITE_PERMISSIONS}")
