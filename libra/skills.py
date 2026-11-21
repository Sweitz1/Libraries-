"""Skills (tools) and plugins.

A skill is a Python function with a name, a description and typed
parameters. Skills can be run from the command line (``libra skill``), from
chat (``/scrape https://...``), over HTTP (``POST /v1/skills/<name>``), or
automatically when a message matches the skill's router.

Plugins are ordinary ``.py`` files that register more skills::

    from libra.skills import skill

    @skill("shout", "Upper-case some text", params={"text": "what to shout"})
    def shout(text: str) -> str:
        return text.upper()

Load them with ``--plugins my_plugins/`` (files or folders, repeatable) or by
putting them in ``~/.libra/plugins``. Plugins run with your permissions, so
only load code you trust.
"""
from __future__ import annotations

import ast
import datetime as _dt
import importlib.util
import inspect
import json
import operator
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Callable

DEFAULT_PLUGIN_DIR = os.path.join(os.environ.get("LIBRA_HOME") or os.path.join(os.path.expanduser("~"), ".libra"),
                                  "plugins")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


@dataclass
class Skill:
    name: str
    description: str
    func: Callable
    params: dict = field(default_factory=dict)  # name -> description
    router: Callable | None = None  # message -> kwargs dict, or None if not relevant
    source: str = "builtin"
    risk: str = "read"  # read, write or danger (see libra.trust)
    input_schema: dict | None = None  # JSON schema for catalog skills (MCP tools, API operations)

    def signature(self) -> inspect.Signature:
        return inspect.signature(self.func)

    def param_names(self) -> list[str]:
        """Parameters in the order positional arguments fill them (required first)."""
        if self.input_schema is not None:
            props = list((self.input_schema.get("properties") or {}).keys())
            req = [p for p in self.input_schema.get("required") or [] if p in props]
            return req + [p for p in props if p not in req]
        return [p for p, v in self.signature().parameters.items() if v.kind not in (v.VAR_POSITIONAL, v.VAR_KEYWORD)]

    def usage(self) -> str:
        if self.input_schema is not None:
            req = set(self.input_schema.get("required") or [])
            return " ".join(f"<{p}>" if p in req else f"[{p}]" for p in self.param_names())
        return " ".join(f"[{p}]" if v.default is not v.empty else f"<{p}>"
                        for p, v in self.signature().parameters.items() if v.kind not in (v.VAR_POSITIONAL, v.VAR_KEYWORD))

    def schema(self) -> dict:
        """OpenAI-style function schema, so other agents can call this skill."""
        if self.input_schema is not None:
            return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                     "parameters": self.input_schema}}
        props, required = {}, []
        for name, p in self.signature().parameters.items():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            ann = p.annotation if p.annotation is not inspect.Parameter.empty else str
            ann = {"int": int, "float": float, "bool": bool, "str": str}.get(ann, ann) if isinstance(ann, str) else ann
            kind = {int: "integer", float: "number", bool: "boolean"}.get(ann, "string")
            props[name] = {"type": kind, "description": self.params.get(name, "")}
            if p.default is inspect.Parameter.empty:
                required.append(name)
            else:
                props[name]["default"] = p.default
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": {"type": "object", "properties": props,
                                                                "required": required}}}


@dataclass
class SkillResult:
    skill: str
    ok: bool
    text: str
    data: object = None

    def to_dict(self) -> dict:
        return {"skill": self.skill, "ok": self.ok, "text": self.text, "data": self.data}


def _coerce(value, annotation):
    if isinstance(annotation, str):
        annotation = {"int": int, "float": float, "bool": bool, "str": str}.get(annotation, str)
    if annotation is inspect.Parameter.empty or annotation is str or not isinstance(value, str):
        return value
    if annotation is bool:
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    if annotation in (int, float):
        return annotation(value)
    return value


class SkillRegistry:
    """Skills in memory (built-ins, plugins) plus, optionally, the on-disk catalog.

    ``policy`` (a :class:`libra.trust.Policy`) gates every run; ``confirm(skill, args)``
    answers "ask" decisions. Without ``confirm``, "ask" means no.
    """

    def __init__(self, catalog=None, policy=None, confirm=None):
        self._skills: dict[str, Skill] = {}
        self._catalog = catalog  # a Catalog, or a callable returning one (opened lazily)
        self._loaded: dict[str, Skill] = {}
        self.policy = policy
        self.confirm = confirm

    @property
    def catalog(self):
        if callable(self._catalog) and not hasattr(self._catalog, "search"):
            self._catalog = self._catalog()
        return self._catalog

    def _from_catalog(self, name: str) -> Skill | None:
        if name in self._loaded:
            return self._loaded[name]
        if self.catalog is None:
            return None
        entry = self.catalog.get(name)
        if entry is None:
            return None
        from .catalog import make_runner

        s = Skill(entry.name, entry.description, make_runner(entry), source=entry.source, risk=entry.risk,
                  input_schema=entry.input_schema)
        self._loaded[name] = s
        return s

    def search(self, query: str, limit: int = 10) -> list[Skill]:
        """Skills matching ``query`` from built-ins, plugins and the catalog, best first.

        Candidates are ranked by how many of the query's words their name and description
        cover; the catalog's BM25 order breaks ties."""
        from .knowledge import terms

        def words(text: str) -> set:
            return set(terms(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).replace("_", " ").replace(".", " ")))

        q = words(query)
        if not q:
            return []
        scored = []
        for s in self.list():
            cover = len(q & words(f"{s.name} {s.description}"))
            if cover:
                scored.append((cover, 0, s))
        if self.catalog is not None:
            for rank, (_, entry) in enumerate(self.catalog.search(query, limit * 3)):
                cover = len(q & words(f"{entry.name} {entry.description}"))
                s = self._from_catalog(entry.name)
                if s is not None:
                    scored.append((cover, rank, s))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [s for _, _, s in scored[:limit]]

    def register(self, s: Skill) -> Skill:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", s.name):
            raise ValueError(f"skill names must be lowercase letters, digits and _: {s.name!r}")
        self._skills[s.name] = s
        return s

    def skill(self, name: str | None = None, description: str = "", params: dict | None = None, router=None,
              risk: str = "write"):
        """Decorator registering a function as a skill in this registry.

        ``risk`` is "read", "write" (the default, so you're asked before it runs) or "danger"."""

        def wrap(func):
            self.register(Skill(name or func.__name__, description or (func.__doc__ or "").strip(), func,
                                params or {}, router, source=getattr(func, "__module__", "plugin"), risk=risk))
            return func

        return wrap

    def get(self, name: str) -> Skill:
        if name in self._skills:
            return self._skills[name]
        s = self._from_catalog(name)
        if s is None:
            hint = "; try /find <words>" if self.catalog is not None else ""
            raise KeyError(f"unknown skill {name!r}{hint}; built-in: {', '.join(sorted(self._skills))}")
        return s

    def __contains__(self, name: str) -> bool:
        return name in self._skills or self._from_catalog(name) is not None

    def check(self, s: Skill, args: dict, approved: bool = False) -> tuple[bool, str]:
        """Apply the trust policy. Returns (allowed, decision or explanation).

        ``approved`` is an approval already given for this one call (e.g. a job step the owner
        approved). It answers "ask"; it never overrides "deny"."""
        if self.policy is None:
            return True, "allow"
        decision, reason = self.policy.decide(s.name, s.risk)
        if decision == "allow":
            return True, "allow"
        if decision == "deny":
            return False, (f"'{s.name}' is blocked ({reason}). To allow it: libra trust allow {s.name}")
        if approved or (self.confirm is not None and self.confirm(s, args)):
            return True, "approved"
        return False, (f"'{s.name}' is a {s.risk} skill and needs your approval ({reason}). "
                       f"Approve it when asked in chat, or allow it: libra trust allow {s.name}")

    def list(self) -> list[Skill]:
        return [self._skills[k] for k in sorted(self._skills)]

    def run(self, name: str, args: dict | None = None, approved: bool = False) -> SkillResult:
        decision, s = "allow", None
        try:
            s = self.get(name)
            if s.input_schema is not None:
                from .catalog import coerce_by_schema

                kwargs = coerce_by_schema(args or {}, s.input_schema)
            else:
                sig = s.signature()
                kwargs = {}
                for key, value in (args or {}).items():
                    if key not in sig.parameters:
                        raise TypeError(f"{name} has no parameter {key!r}; it takes {', '.join(sig.parameters)}")
                    kwargs[key] = _coerce(value, sig.parameters[key].annotation)
                sig.bind(**kwargs)  # report missing arguments clearly
            allowed, decision = self.check(s, kwargs, approved)
            if not allowed:
                self._audit(s, kwargs, "deny", None)
                return SkillResult(name, False, decision)
            out = s.func(**kwargs)
        except Exception as exc:  # skills report failures instead of crashing the chat/server
            if s is not None:
                self._audit(s, args or {}, decision, False)
            return SkillResult(name, False, f"{type(exc).__name__}: {exc}")
        ok = out.ok if isinstance(out, SkillResult) else True
        self._audit(s, kwargs, decision, ok)
        if isinstance(out, SkillResult):
            return out
        if isinstance(out, str):
            return SkillResult(name, True, out)
        return SkillResult(name, True, json.dumps(out, indent=2, ensure_ascii=False, default=str), out)

    def _audit(self, s: Skill, args: dict, decision: str, ok) -> None:
        if self.policy is not None and (s.source != "builtin" or s.risk != "read"):
            from .trust import audit

            audit(s.name, args, decision, ok, s.source)

    def parse_command(self, line: str) -> tuple[str, dict]:
        """``/name first-arg key=value ...`` -> (name, kwargs). Positional args fill parameters in order."""
        tokens = shlex.split(line.lstrip("/"))
        if not tokens:
            raise ValueError("empty command")
        name, rest = tokens[0], tokens[1:]
        params = self.get(name).param_names()
        kwargs, pos = {}, 0
        for tok in rest:
            key, eq, value = tok.partition("=")
            if eq and key in params:
                kwargs[key] = value
            elif pos < len(params):
                while pos < len(params) and params[pos] in kwargs:
                    pos += 1
                if pos >= len(params):
                    raise ValueError(f"too many arguments for {name}")
                kwargs[params[pos]] = tok
                pos += 1
            else:
                raise ValueError(f"too many arguments for {name}")
        return name, kwargs

    def route(self, message: str) -> tuple[str, dict] | None:
        """First skill whose router claims ``message``."""
        for s in self.list():
            if s.router is None:
                continue
            try:
                args = s.router(message)
            except Exception:
                args = None
            if args is not None:
                return s.name, args
        return None

    # --------------------------------------------------------------- plugins
    def load_plugins(self, paths, log=print) -> list[str]:
        loaded = []
        for path in paths:
            path = os.path.expanduser(path)
            files = ([os.path.join(path, f) for f in sorted(os.listdir(path))
                      if f.endswith(".py") and not f.startswith("_")] if os.path.isdir(path) else [path])
            for file in files:
                loaded.extend(self._load_file(file, log))
        return loaded

    def _load_file(self, file: str, log) -> list[str]:
        before = set(self._skills)
        mod_name = "libra_plugin_" + re.sub(r"\W", "_", os.path.splitext(os.path.basename(file))[0])
        spec = importlib.util.spec_from_file_location(mod_name, file)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load plugin {file}")
        module = importlib.util.module_from_spec(spec)
        global _target
        previous, _target = _target, self
        try:
            spec.loader.exec_module(module)
            if callable(getattr(module, "register", None)):
                module.register(self)
        finally:
            _target = previous
        new = sorted(set(self._skills) - before)
        for name in new:
            self._skills[name].source = file
        if log:
            log(f"plugin {os.path.basename(file)}: {', '.join(new) or 'no skills'}")
        return new


_target: SkillRegistry | None = None  # registry receiving @skill while a plugin loads


def skill(name: str | None = None, description: str = "", params: dict | None = None, router=None,
          risk: str = "write"):
    """Register a skill from a plugin file. ``risk``: "read", "write" (default) or "danger"."""

    def wrap(func):
        registry = _target or _default
        return registry.skill(name, description, params, router, risk)(func)

    return wrap


# ------------------------------------------------------------ built-ins
def _first_url(message: str) -> str | None:
    m = URL_RE.search(message)
    return m.group().rstrip(".,;:!?") if m else None


def _route_scrape(message: str):
    url = _first_url(message)
    if url and re.search(r"\b(scrape|read|summari[sz]e|extract|open|fetch|get|what'?s on|what is on|show)\b",
                         message, re.I) and not re.search(r"\bcrawl|\blinks?\b", message, re.I):
        return {"url": url}
    return None


def _route_crawl(message: str):
    url = _first_url(message)
    return {"url": url} if url and re.search(r"\bcrawl", message, re.I) else None


def _route_links(message: str):
    url = _first_url(message)
    return {"url": url} if url and re.search(r"\blinks?\b", message, re.I) and "crawl" not in message.lower() else None


_CALC_RE = re.compile(r"^\s*(?:what(?:'s| is)\s+|calc(?:ulate)?\s+|compute\s+|=\s*)?([-+*/%^().\d\s]+?)\s*[?=]?\s*$", re.I)


def _route_calc(message: str):
    m = _CALC_RE.match(message)
    if m and re.search(r"\d", m.group(1)) and re.search(r"\d\s*[-+*/%^]\s*[\d(]", m.group(1)):
        return {"expression": m.group(1)}
    return None


def _route_time(message: str):
    if re.search(r"\b(what time is it|what's the time|current time|today'?s date|what day is it|what is the date)\b",
                 message, re.I):
        return {}
    return None


_TRANSLATE_RES = [
    re.compile(r"^\s*(?:please\s+)?translate\s+(?P<q>.+?)\s+(?:from\s+(?P<src>[^\W\d_][\w-]*)\s+)?(?:to|into)\s+"
               r"(?P<tgt>[^\W\d_][\w -]*?)\s*[.?!]*\s*$", re.I | re.S),
    re.compile(r"^\s*how\s+(?:do|would|can)\s+(?:you|i|we)\s+say\s+(?P<q>.+?)\s+in\s+(?P<tgt>[^\W\d_][\w-]*)\s*\??\s*$",
               re.I | re.S),
    re.compile(r"^\s*what(?:'s| is)\s+(?P<q>.+?)\s+in\s+(?P<tgt>[^\W\d_][\w-]*)\s*\??\s*$", re.I | re.S),
]


def _route_translate(message: str):
    from .lang import code_for

    for rx in _TRANSLATE_RES:
        m = rx.match(message)
        if m and code_for(m.group("tgt")):
            q = m.group("q").strip().strip("\"'“”‘’«»")
            args = {"text": q, "to": m.group("tgt").strip()}
            src = m.groupdict().get("src")
            if src and code_for(src):
                args["source"] = src
            return args
    return None


def _route_remember(message: str):
    from .memory import REMEMBER_RE

    m = REMEMBER_RE.match(message)
    return {"text": m.group("x").strip()} if m else None


def _route_recall(message: str):
    from .memory import RECALL_RE

    m = RECALL_RE.match(message)
    return {"query": m.group("x")} if m else None


def _route_forget(message: str):
    from .memory import FORGET_RE

    m = FORGET_RE.match(message)
    return {"what": m.group("x")} if m else None


def _route_detect(message: str):
    m = re.match(r"^\s*(?:what|which)\s+language\s+is\s+(?:this|that|it)?\s*[:\-]?\s*(?P<q>.+?)\s*\??\s*$",
                 message, re.I | re.S)
    return {"text": m.group("q").strip("\"'“”")} if m and m.group("q") else None


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("only numbers and + - * / // % ** ( ) are allowed")


def fetcher(allow_private: bool = False, delay: float = 0.0):
    """A web Fetcher that honours the session's --allow-private setting. Use this in plugins."""
    from .web import Fetcher

    return Fetcher(delay=delay, allow_private=allow_private or os.environ.get("LIBRA_ALLOW_PRIVATE") == "1")


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + f"\n… ({len(text) - max_chars:,} more characters)"


def extractive_summary(text: str, n: int = 3) -> str:
    """The ``n`` sentences that best cover the text's frequent words, in their original order."""
    from collections import Counter

    from .knowledge import sentences, terms

    sents = [s for s in sentences(re.sub(r"(?m)^#+\s*", "", text)) if len(s.split()) >= 5]
    if len(sents) <= n:
        return " ".join(sents) or text.strip()[:500]
    freq = Counter(t for s in sents for t in set(terms(s)))
    top = max(freq.values())
    scored = []
    for i, s in enumerate(sents):
        words = terms(s)
        if not words:
            continue
        score = sum(freq[w] / top for w in set(words)) / (len(set(words)) ** 0.5)
        scored.append((score - 0.02 * i, i))  # slight preference for earlier sentences
    keep = sorted(i for _, i in sorted(scored, reverse=True)[:n])
    return " ".join(sents[i] for i in keep)


def _register_builtins(reg: SkillRegistry) -> None:
    @reg.skill("scrape", "Download a web page and extract its title, text, tables or CSS-selected elements.",
               {"url": "page to read", "select": "optional CSS selector(s), comma separated",
                "format": "text, markdown or json", "max_chars": "limit on returned text"}, router=_route_scrape, risk="read")
    def scrape(url: str, select: str = "", format: str = "markdown", max_chars: int = 4000):
        from .web import scrape as do_scrape
        from .web import to_markdown

        selectors = [select] if select else None
        result = do_scrape(url, selectors, fetcher())
        if format == "json":
            data = dict(result, text=_truncate(result["text"], max_chars))
            return SkillResult("scrape", True, json.dumps(data, indent=2, ensure_ascii=False), result)
        if select:
            values = result["selected"][select]
            text = "\n".join(f"- {v}" for v in values) or "(no elements matched)"
        else:
            text = to_markdown(result) if format == "markdown" else result["text"]
        return SkillResult("scrape", True, _truncate(text, max_chars), result)

    @reg.skill("crawl", "Politely crawl a website (same site, robots.txt respected) and summarise or save the pages.",
               {"url": "start page", "max_pages": "pages to fetch", "depth": "how many links deep",
                "delay": "seconds between requests", "out": "optional .jsonl file to save pages to",
                "include": "only follow URLs matching this regex", "exclude": "skip URLs matching this regex"},
               router=_route_crawl, risk="read")
    def crawl(url: str, max_pages: int = 10, depth: int = 1, delay: float = 1.0, out: str = "",
              include: str = "", exclude: str = ""):
        from .web import crawl as do_crawl

        pages, errors = [], []
        for page in do_crawl(url, max_pages, depth, delay, include=include or None, exclude=exclude or None,
                             fetcher=fetcher(delay=delay), on_error=lambda u, e: errors.append(f"{u}: {e}")):
            pages.append(page)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                for p in pages:
                    f.write(json.dumps({"url": p.url, "title": p.title, "text": p.text}, ensure_ascii=False) + "\n")
        lines = [f"Crawled {len(pages)} page(s) from {url}" + (f", saved to {out}" if out else "") + ":"]
        lines += [f"- [{p.depth}] {p.title or '(untitled)'} — {p.url} ({len(p.text):,} chars)" for p in pages]
        if errors:
            lines.append(f"{len(errors)} error(s): " + "; ".join(errors[:5]))
        data = [{"url": p.url, "title": p.title, "depth": p.depth, "chars": len(p.text)} for p in pages]
        return SkillResult("crawl", True, "\n".join(lines), data)

    @reg.skill("links", "List the links on a web page.", {"url": "page to read", "filter": "only links containing this text"},
               router=_route_links, risk="read")
    def links(url: str, filter: str = ""):
        from .web import scrape as do_scrape

        found = [l for l in do_scrape(url, fetcher=fetcher())["links"]
                 if not filter or filter.lower() in (l["url"] + " " + l["text"]).lower()]
        text = "\n".join(f"- {l['text'] or '(no text)'}: {l['url']}" for l in found[:200]) or "(no links)"
        return SkillResult("links", True, text, found)

    @reg.skill("calc", "Evaluate an arithmetic expression exactly (no code is executed).",
               {"expression": "for example (2+3)*4/7"}, router=_route_calc, risk="read")
    def calc(expression: str):
        value = _eval(ast.parse(expression.replace("^", "**"), mode="eval"))
        if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
            value = int(value)
        return f"{expression.strip()} = {value}"

    @reg.skill("translate", "Translate text between languages using your translation memory, dictionaries and "
               "translation models (libra translate add …).",
               {"text": "what to translate", "to": "target language (name or code)",
                "source": "source language, or auto to detect"}, router=_route_translate, risk="read")
    def translate(text: str, to: str, source: str = "auto"):
        from .translate import Translator

        result = Translator().translate(text, to, source)
        return SkillResult("translate", result.method != "none", result.describe(), result.to_dict())

    @reg.skill("detect_language", "Say which language a text is written in.", {"text": "the text"},
               router=_route_detect, risk="read")
    def detect_language(text: str):
        from .lang import name
        from .translate import Translator

        guesses = Translator().detect(text)
        if not guesses:
            return "I can't tell: the text has no letters."
        return "\n".join(f"{name(code)} ({code}): {round(conf * 100)}%" for code, conf in guesses[:4])

    @reg.skill("romanize", "Write Cyrillic or Greek text in Latin letters.", {"text": "the text"}, risk="read")
    def romanize(text: str):
        from .lang import romanize as rz

        return rz(text)

    @reg.skill("remember", "Keep a fact or preference in long-term memory.", {"text": "what to remember"},
               router=_route_remember, risk="write")
    def remember(text: str):
        from .memory import Memory

        it = Memory().add(text, source="chat")
        return f"I'll remember that. (#{it['id']})"

    @reg.skill("recall", "Look something up in long-term memory.", {"query": "what to look for"},
               router=_route_recall, risk="read")
    def recall(query: str):
        from .memory import Memory, describe

        return describe(Memory().recall(query))

    @reg.skill("forget", "Remove memories (by #id, or ones that match closely).", {"what": "#id or words"},
               router=_route_forget, risk="write")
    def forget(what: str):
        from .memory import Memory

        gone = Memory().forget(what.lstrip("#"))
        return f"Forgot {len(gone)} memor{'y' if len(gone) == 1 else 'ies'}." if gone else "Nothing matched."

    @reg.skill("summarize", "Shorten a text to its most important sentences (taken word for word from it).",
               {"text": "the text to summarize", "sentences": "how many sentences to keep"}, risk="read")
    def summarize(text: str, sentences: int = 3):
        return extractive_summary(text, sentences)

    @reg.skill("time", "Current local date and time.", router=_route_time, risk="read")
    def time_skill():
        now = _dt.datetime.now().astimezone()
        return now.strftime("%A %d %B %Y, %H:%M:%S %Z")


def _open_catalog():
    from . import home
    from .catalog import Catalog

    return Catalog() if os.path.exists(home.path("catalog.db")) else None


def default_registry(plugin_paths=(), log=print, load_default_dir: bool = True, policy=None, confirm=None,
                     catalog=_open_catalog) -> SkillRegistry:
    """Built-in skills, plugins from ``plugin_paths`` and ``~/.libra/plugins``, and the skill catalog."""
    reg = SkillRegistry(catalog=catalog, policy=policy, confirm=confirm)
    _register_builtins(reg)
    for s in _default.list():  # skills registered with @skill outside a plugin load
        reg.register(s)
    paths = list(plugin_paths)
    if load_default_dir and os.path.isdir(DEFAULT_PLUGIN_DIR):
        paths.insert(0, DEFAULT_PLUGIN_DIR)
    reg.load_plugins(paths, log)
    return reg


_default = SkillRegistry()
