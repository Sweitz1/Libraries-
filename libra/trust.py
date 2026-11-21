"""Trust controls: risk levels, allow/ask/deny rules, confirmation and an audit log.

Every skill has a risk level:

* ``read``   only looks things up (scrape, search, GET requests)
* ``write``  changes something or sends something (create, update, post, send)
* ``danger`` hard to undo (delete, pay, transfer, run commands)

A decision is ``allow``, ``ask`` (confirm first) or ``deny``. Rules are checked
from most to least specific: the exact skill name, then each source prefix
(``mcp.github.create_issue`` -> ``mcp.github`` -> ``mcp``), then the default
for the skill's risk level. Defaults: read = allow, write = ask, danger = ask.

Where nobody can answer a confirmation (the HTTP server, a script), ``ask``
counts as ``deny``, and the reply says how to allow the skill.
"""
from __future__ import annotations

import datetime as _dt
import json
import re

from . import home

RISKS = ("read", "write", "danger")
DECISIONS = ("allow", "ask", "deny")
DEFAULTS = {"read": "allow", "write": "ask", "danger": "ask"}

_DANGER_VERBS = ("delete|remove|destroy|drop|purge|wipe|erase|kill|terminate|rm|pay|payment|transfer|charge|refund|"
                 "withdraw|exec|execute|shell|run_command|reset|revoke|ban|uninstall|truncate")
_WRITE_VERBS = ("create|add|insert|update|edit|modify|set|put|patch|post|send|write|upload|publish|submit|reply|"
                "comment|merge|push|move|rename|close|approve|assign|invite|share|book|order|buy|schedule|enable|"
                "disable|start|stop|restart|deploy|install|save|import|archive|star|follow|like|mark|lock|unlock")
_SUFFIX = r"(s|es|ed|d|ing)?"
_DANGER = re.compile(rf"(^|_)({_DANGER_VERBS}){_SUFFIX}(_|$)")
_WRITE = re.compile(rf"(^|_)({_WRITE_VERBS}){_SUFFIX}(_|$)")
_SECRET_KEYS = re.compile(r"token|secret|password|passwd|api[_-]?key|auth|credential|cookie|session", re.I)


def infer_risk(name: str, description: str = "") -> str:
    """Guess a risk level from a tool's name (verbs like delete, send, create)."""
    base = name.rsplit(".", 1)[-1]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", base)
    words = re.sub(r"[^A-Za-z0-9]+", "_", words).lower()  # createIssue / create-issue -> create_issue
    if _DANGER.search(words) or re.search(r"\b(permanently|irreversibl|cannot be undone)", description, re.I):
        return "danger"
    if _WRITE.search(words):
        return "write"
    return "read"


def redact(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if _SECRET_KEYS.search(str(k)):
            out[k] = "***"
        elif isinstance(v, str) and len(v) > 300:
            out[k] = v[:300] + "…"
        else:
            out[k] = v
    return out


class Policy:
    FILE = "trust.json"

    def __init__(self, data: dict | None = None, save: bool = True):
        self.save_enabled = save
        data = data if data is not None else home.read_json(self.FILE, {})
        self.rules: dict[str, str] = dict(data.get("rules", {}))
        self.defaults: dict[str, str] = {**DEFAULTS, **data.get("defaults", {})}

    def _save(self) -> None:
        if self.save_enabled:
            home.write_json(self.FILE, {"rules": self.rules, "defaults": self.defaults})

    def set(self, target: str, decision: str) -> None:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
        if target in RISKS:
            self.defaults[target] = decision
        else:
            self.rules[target] = decision
        self._save()

    def reset(self, target: str) -> bool:
        if target in RISKS:
            self.defaults[target] = DEFAULTS[target]
            self._save()
            return True
        found = self.rules.pop(target, None) is not None
        self._save()
        return found

    def decide(self, name: str, risk: str) -> tuple[str, str]:
        """``(decision, reason)`` for running skill ``name`` with ``risk``."""
        if name in self.rules:
            return self.rules[name], f"rule for {name}"
        parts = name.split(".")
        for i in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in self.rules:
                return self.rules[prefix], f"rule for {prefix}"
        risk = risk if risk in RISKS else "write"
        return self.defaults[risk], f"default for {risk} skills"


def audit(name: str, args: dict, decision: str, ok: bool | None, source: str = "") -> None:
    """Append one line per skill call to ~/.libra/audit.jsonl (secrets redacted)."""
    entry = {"time": _dt.datetime.now().astimezone().isoformat(timespec="seconds"), "skill": name,
             "source": source, "args": redact(args), "decision": decision, "ok": ok}
    try:
        with open(home.path("audit.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def console_confirm(skill, args: dict) -> bool:
    """Ask on the terminal. Anything but y/yes declines."""
    shown = json.dumps(redact(args), ensure_ascii=False)
    try:
        reply = input(f"\nAllow {skill.risk} skill '{skill.name}' with {shown}? [y/N/always] ").strip().lower()
    except EOFError:
        return False
    if reply == "always":
        Policy().set(skill.name, "allow")
        return True
    return reply in ("y", "yes")
