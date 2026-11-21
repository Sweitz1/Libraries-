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

DEFAULT_PLUGIN_DIR = os.path.join(os.path.expanduser("~"), ".libra", "plugins")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


@dataclass
class Skill:
    name: str
    description: str
    func: Callable
    params: dict = field(default_factory=dict)  # name -> description
    router: Callable | None = None  # message -> kwargs dict, or None if not relevant
    source: str = "builtin"

    def signature(self) -> inspect.Signature:
        return inspect.signature(self.func)

    def schema(self) -> dict:
        """OpenAI-style function schema, so other agents can call this skill."""
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
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, s: Skill) -> Skill:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", s.name):
            raise ValueError(f"skill names must be lowercase letters, digits and _: {s.name!r}")
        self._skills[s.name] = s
        return s

    def skill(self, name: str | None = None, description: str = "", params: dict | None = None, router=None):
        """Decorator registering a function as a skill in this registry."""

        def wrap(func):
            self.register(Skill(name or func.__name__, description or (func.__doc__ or "").strip(), func,
                                params or {}, router, source=getattr(func, "__module__", "plugin")))
            return func

        return wrap

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"unknown skill {name!r}; available: {', '.join(sorted(self._skills))}")
        return self._skills[name]

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def list(self) -> list[Skill]:
        return [self._skills[k] for k in sorted(self._skills)]

    def run(self, name: str, args: dict | None = None) -> SkillResult:
        try:
            s = self.get(name)
            sig = s.signature()
            kwargs = {}
            for key, value in (args or {}).items():
                if key not in sig.parameters:
                    raise TypeError(f"{name} has no parameter {key!r}; it takes {', '.join(sig.parameters)}")
                kwargs[key] = _coerce(value, sig.parameters[key].annotation)
            sig.bind(**kwargs)  # report missing arguments clearly
            out = s.func(**kwargs)
        except Exception as exc:  # skills report failures instead of crashing the chat/server
            return SkillResult(name, False, f"{type(exc).__name__}: {exc}")
        if isinstance(out, SkillResult):
            return out
        if isinstance(out, str):
            return SkillResult(name, True, out)
        return SkillResult(name, True, json.dumps(out, indent=2, ensure_ascii=False, default=str), out)

    def parse_command(self, line: str) -> tuple[str, dict]:
        """``/name first-arg key=value ...`` -> (name, kwargs). Positional args fill parameters in order."""
        tokens = shlex.split(line.lstrip("/"))
        if not tokens:
            raise ValueError("empty command")
        name, rest = tokens[0], tokens[1:]
        params = [p for p, v in self.get(name).signature().parameters.items()
                  if v.kind not in (v.VAR_POSITIONAL, v.VAR_KEYWORD)]
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


def skill(name: str | None = None, description: str = "", params: dict | None = None, router=None):
    """Register a skill from a plugin file (or into the default registry)."""

    def wrap(func):
        registry = _target or _default
        return registry.skill(name, description, params, router)(func)

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


def _register_builtins(reg: SkillRegistry) -> None:
    @reg.skill("scrape", "Download a web page and extract its title, text, tables or CSS-selected elements.",
               {"url": "page to read", "select": "optional CSS selector(s), comma separated",
                "format": "text, markdown or json", "max_chars": "limit on returned text"}, router=_route_scrape)
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
               router=_route_crawl)
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
               router=_route_links)
    def links(url: str, filter: str = ""):
        from .web import scrape as do_scrape

        found = [l for l in do_scrape(url, fetcher=fetcher())["links"]
                 if not filter or filter.lower() in (l["url"] + " " + l["text"]).lower()]
        text = "\n".join(f"- {l['text'] or '(no text)'}: {l['url']}" for l in found[:200]) or "(no links)"
        return SkillResult("links", True, text, found)

    @reg.skill("calc", "Evaluate an arithmetic expression exactly (no code is executed).",
               {"expression": "for example (2+3)*4/7"}, router=_route_calc)
    def calc(expression: str):
        value = _eval(ast.parse(expression.replace("^", "**"), mode="eval"))
        if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
            value = int(value)
        return f"{expression.strip()} = {value}"

    @reg.skill("time", "Current local date and time.", router=_route_time)
    def time_skill():
        now = _dt.datetime.now().astimezone()
        return now.strftime("%A %d %B %Y, %H:%M:%S %Z")


def default_registry(plugin_paths=(), log=print, load_default_dir: bool = True) -> SkillRegistry:
    """Built-in skills plus plugins from ``plugin_paths`` and ``~/.libra/plugins``."""
    reg = SkillRegistry()
    _register_builtins(reg)
    for s in _default.list():  # skills registered with @skill outside a plugin load
        reg.register(s)
    paths = list(plugin_paths)
    if load_default_dir and os.path.isdir(DEFAULT_PLUGIN_DIR):
        paths.insert(0, DEFAULT_PLUGIN_DIR)
    reg.load_plugins(paths, log)
    return reg


_default = SkillRegistry()
