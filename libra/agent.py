"""An autonomous goal agent: turn a goal written in plain words into steps, run them with your skills.

    libra agent plan "scrape https://example.com/news then summarize it then translate it to Spanish"
    libra agent run  "…"                      # asks before any skill that isn't read-only
    libra agent add morning "…" --every daily@07:00

**Planning** is done without a language model (Libra's own model is too small to plan
reliably). The goal is split into clauses ("then", "and then", ";", new lines, or "and"
before an action word). Each clause is matched to a skill:

1. phrases with a fixed meaning: *summarize*, *translate … to <language>*, *detect the language*;
2. the skills' own routers (scrape/crawl/links with a URL, calculations, the time);
3. otherwise a search over all skills (built-ins, plugins, the catalog), accepted only when
   the skill's name and description cover at least half of the clause's key words.

Words like *it*, *them*, *the result* pass the previous step's output on. *each of them*
(or several URLs in one clause) runs the step once per item, up to ``max_items``.
A clause that can't be matched makes the plan fail with a reason. It never guesses.

**Running** goes through the trust policy, like every other skill call: read-only skills run,
others ask on the terminal, and in scheduled or server runs (where nobody can be asked) they
are refused with the command that allows them. Runs stop at ``max_steps`` / ``max_seconds``.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field

from . import home

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
QUOTED = re.compile(r"[\"“«']([^\"”»']{1,500})[\"”»']")
PREV_RE = re.compile(r"\b(it|them|those|these|that|this|the (?:result|results|text|page|pages|links?|summary|output|"
                     r"articles?|headlines?))\b", re.I)
EACH_RE = re.compile(r"\b(each|every|all) (?:of )?(?:them|those|these|the )?(?:links?|pages?|results?|items?|urls?|"
                     r"articles?|sites?)?\b", re.I)
ACTION = ("scrape", "crawl", "read", "fetch", "get", "open", "list", "find", "search", "summarize", "summarise",
          "translate", "calculate", "compute", "count", "detect", "save", "send", "show", "extract", "check",
          "collect", "look", "run", "romanize")
SPLIT_RE = re.compile(r"\s*(?:;|\n+|(?<=[.!?])\s+(?=[A-Z])|,?\s+(?:and\s+)?then\s+|,?\s+after that,?\s+|"
                      r",?\s+afterwards,?\s+|,?\s+finally,?\s+)\s*", re.I)
AND_ACTION_RE = re.compile(r",?\s+and\s+(?=(?:" + "|".join(ACTION) + r")\b)", re.I)
STATE_LOCK = threading.Lock()


@dataclass
class Step:
    clause: str
    skill: str = ""
    args: dict = field(default_factory=dict)  # values may contain "{prev}" / "{item}"
    foreach: bool = False
    items: list = field(default_factory=list)  # fixed items (e.g. several URLs in one clause)
    why: str = ""

    def describe(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        each = " for each item" if self.foreach else ""
        return f"{self.skill}({args}){each}  ← {self.why}"


@dataclass
class Plan:
    goal: str
    steps: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and not self.problems

    def describe(self) -> str:
        lines = [f"Goal: {self.goal}"]
        for i, s in enumerate(self.steps, 1):
            lines.append(f"  {i}. {s.clause!r}\n     → {s.describe()}")
        for p in self.problems:
            lines.append(f"  ✗ {p}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"goal": self.goal, "ok": self.ok, "problems": self.problems,
                "steps": [dict(asdict(s), description=s.describe()) for s in self.steps]}


# ------------------------------------------------------------------ planning
def split_goal(goal: str) -> list[str]:
    parts = []
    for piece in SPLIT_RE.split(goal.strip()):
        parts += [p for p in AND_ACTION_RE.split(piece) if p]
    return [p.strip(" .,") for p in parts if p and p.strip(" .,")]


def _lang(clause: str):
    from .lang import code_for

    m = re.search(r"\b(?:to|into|in)\s+([^\W\d_][\w-]*)", clause, re.I)
    return m.group(1) if m and code_for(m.group(1)) else None


def _content_words(text: str) -> set:
    from .knowledge import terms

    stop = {"please", "it", "them", "each", "every", "result", "results", "using", "use", "page", "pages"}
    return {t for t in terms(re.sub(URL_RE, " ", text)) if t not in stop}


def _fill_args(skill, clause: str, has_prev: bool) -> tuple[dict, list, str | None]:
    """Arguments for ``skill`` from the clause: key=value, URLs, quoted text, numbers, languages, {prev}."""
    params = skill.param_names()
    schema = skill.schema()["function"]["parameters"]
    props, required = schema.get("properties", {}), set(schema.get("required", []))
    args, items = {}, []
    for k, v in re.findall(r"\b(\w+)=(\"[^\"]*\"|'[^']*'|\S+)", clause):
        if k in props:
            args[k] = v.strip("\"'")
    urls = [u.rstrip(".,;:!?") for u in URL_RE.findall(clause)]
    quoted = QUOTED.findall(clause)
    numbers = re.findall(r"(?<![\w.])\d+(?![\w.])", re.sub(URL_RE, " ", clause))
    url_param = next((p for p in params if p in ("url", "link", "href", "uri") or p.endswith("_url")), None)
    text_param = next((p for p in params if p in ("text", "query", "q", "message", "content", "input", "body",
                                                  "expression", "prompt")), None)
    if url_param and url_param not in args:
        if len(urls) > 1:
            items, args[url_param] = urls, "{item}"
        elif urls:
            args[url_param] = urls[0]
        elif has_prev and PREV_RE.search(clause):
            args[url_param] = "{prev}"
    if text_param and text_param not in args:
        if quoted:
            args[text_param] = quoted[0]
        elif has_prev:
            args[text_param] = "{prev}"
    lang = _lang(clause)
    for p in params:
        if p in args:
            continue
        kind = (props.get(p) or {}).get("type")
        if lang and p in ("to", "target", "target_lang", "language", "lang"):
            args[p] = lang
        elif kind in ("integer", "number") and numbers:
            args[p] = numbers.pop(0)
    missing = [p for p in required if p not in args]
    if missing:
        return args, items, f"no value for {', '.join(missing)} of {skill.name}"
    return args, items, None


def plan(goal: str, registry) -> Plan:
    result = Plan(goal)
    for clause in split_goal(goal):
        has_prev = bool(result.steps)
        each = bool(EACH_RE.search(clause)) and has_prev
        step = Step(clause, foreach=each)
        low = clause.lower()
        try:
            if re.search(r"\bsummari[sz]e|\bsum up\b|\bshort summary\b", low):
                n = re.search(r"\b(\d+)\s+(?:sentences?|lines?|points?)\b", low)
                text = QUOTED.findall(clause)
                if not (text or has_prev):
                    raise LookupError("summarize what? (no previous step and no quoted text)")
                step.skill, step.why = "summarize", "the word 'summarize'"
                step.args = {"text": text[0] if text else "{prev}", **({"sentences": n.group(1)} if n else {})}
            elif re.search(r"\btranslat", low) and _lang(clause):
                text = QUOTED.findall(clause)
                if not (text or has_prev):
                    raise LookupError("translate what? (no previous step and no quoted text)")
                step.skill, step.why = "translate", "'translate … to <language>'"
                step.args = {"text": text[0] if text else "{prev}", "to": _lang(clause)}
            elif re.search(r"\b(detect|which|what) (the )?language\b", low):
                step.skill, step.why = "detect_language", "'detect the language'"
                step.args = {"text": (QUOTED.findall(clause) or ["{prev}"])[0]}
            else:
                routed = registry.route(clause)
                if routed:
                    step.skill, step.why = routed[0], f"{routed[0]} recognised the request"
                    skill = registry.get(routed[0])
                    args, items, problem = _fill_args(skill, clause, has_prev)
                    step.args, step.items = {**args, **{k: v for k, v in routed[1].items() if k not in args or not items}}, items
                else:
                    want = _content_words(clause)
                    best = None
                    for cand in registry.search(clause, 8):
                        cover = len(want & _content_words(f"{cand.name.replace('_', ' ').replace('.', ' ')} "
                                                          f"{cand.description}"))
                        if want and cover * 2 >= len(want) and (best is None or cover > best[0]):
                            best = (cover, cand)
                    if best is None:
                        raise LookupError(f"no skill clearly matches {clause!r} (try: libra skill find …)")
                    skill = best[1]
                    args, items, problem = _fill_args(skill, clause, has_prev)
                    if problem:
                        raise LookupError(problem)
                    step.skill, step.args, step.items = skill.name, args, items
                    step.why = f"best match ({best[0]}/{len(want)} key words)"
            if step.foreach and not any("{prev}" in str(v) or "{item}" in str(v) for v in step.args.values()):
                url_param = next((p for p in registry.get(step.skill).param_names() if p in ("url", "link")), None)
                if url_param:
                    step.args[url_param] = "{item}"
            step.args = {k: ("{item}" if step.foreach and v == "{prev}" else v) for k, v in step.args.items()}
            if step.items:
                step.foreach = True
        except LookupError as exc:
            result.problems.append(str(exc))
            continue
        except KeyError as exc:
            result.problems.append(str(exc).strip("'\""))
            continue
        result.steps.append(step)
    if not result.steps and not result.problems:
        result.problems.append("the goal is empty")
    return result


# ------------------------------------------------------------------- running
@dataclass
class StepResult:
    clause: str
    skill: str
    ok: bool
    text: str
    seconds: float
    items: int = 0


@dataclass
class AgentRun:
    goal: str
    name: str = ""
    started: str = ""
    finished: str = ""
    status: str = "running"  # done, stopped, blocked, invalid, timeout
    steps: list = field(default_factory=list)
    output: str = ""
    problems: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        lines = [f"{self.status.upper()}: {self.goal}"]
        for i, s in enumerate(self.steps, 1):
            mark = "✓" if s.ok else "✗"
            each = f" ×{s.items}" if s.items else ""
            lines.append(f"  {mark} {i}. {s.skill}{each} ({s.seconds:.1f}s): {s.clause}")
        lines += [f"  ✗ {p}" for p in self.problems]
        if self.output:
            lines += ["", self.output]
        return "\n".join(lines)


def _items(data, text: str) -> list[str]:
    """What 'each of them' iterates over: URLs or entries from the previous step."""
    if isinstance(data, list):
        out = []
        for d in data:
            if isinstance(d, dict):
                out.append(str(d.get("url") or d.get("link") or d.get("text") or json.dumps(d)))
            else:
                out.append(str(d))
        return [o for o in out if o]
    urls = URL_RE.findall(text or "")
    if urls:
        return list(dict.fromkeys(u.rstrip(".,;:!?)") for u in urls))
    return [l.strip("-• ").strip() for l in (text or "").splitlines() if l.strip("-• ").strip()]


def _subst(args: dict, prev: str, item: str | None = None) -> dict:
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            v = v.replace("{item}", item if item is not None else prev).replace("{prev}", prev)
        out[k] = v
    return out


def execute(p: Plan, registry, max_steps: int = 10, max_items: int = 5, max_seconds: float = 300,
            name: str = "", log=None) -> AgentRun:
    now = lambda: _dt.datetime.now().astimezone().isoformat(timespec="seconds")  # noqa: E731
    run = AgentRun(p.goal, name, now())
    if not p.ok:
        run.status, run.problems, run.finished = "invalid", list(p.problems), now()
        return run
    t0 = time.time()
    prev_text, prev_data = "", None
    for i, step in enumerate(p.steps):
        if i >= max_steps:
            run.status = "stopped"
            run.problems.append(f"step limit of {max_steps} reached")
            break
        if time.time() - t0 > max_seconds:
            run.status = "timeout"
            run.problems.append(f"time limit of {max_seconds:.0f}s reached")
            break
        s0 = time.time()
        if log:
            log(f"step {i + 1}: {step.skill} ← {step.clause}")
        if step.foreach:
            items = (step.items or _items(prev_data, prev_text))[:max_items]
            if not items:
                run.steps.append(StepResult(step.clause, step.skill, False, "nothing to go through", 0.0))
                run.status = "stopped"
                break
            texts, datas, ok, blocked = [], [], True, ""
            for item in items:
                r = registry.run(step.skill, _subst(step.args, prev_text, item))
                if not r.ok:
                    ok = False
                    if "approval" in r.text or "blocked" in r.text:
                        blocked = r.text
                        break
                texts.append(f"## {item}\n{r.text}")
                datas.append(r.data)
            result = StepResult(step.clause, step.skill, ok, (blocked or "\n\n".join(texts))[:6000],
                                round(time.time() - s0, 2), len(items))
            prev_text, prev_data = "\n\n".join(texts), datas
        else:
            r = registry.run(step.skill, _subst(step.args, prev_text))
            ok, blocked = r.ok, ("approval" in r.text or "is blocked" in r.text) and not r.ok
            result = StepResult(step.clause, step.skill, r.ok, r.text[:6000], round(time.time() - s0, 2))
            prev_text, prev_data = r.text, r.data
        run.steps.append(result)
        if not result.ok:
            run.status = "blocked" if blocked else "stopped"
            if blocked:
                run.problems.append(blocked if isinstance(blocked, str) else result.text)
            break
    else:
        run.status = "done"
    run.output = prev_text[:20000] if run.status == "done" else ""
    run.finished = now()
    return run


# ------------------------------------------------------------- saved goals
def _path(name: str) -> str:
    d = home.path("agent")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def load_goals() -> dict:
    try:
        with open(_path("goals.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_goals(goals: dict) -> None:
    tmp = _path("goals.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(goals, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path("goals.json"))


SCHEDULE_RE = re.compile(r"^(?:(hourly|daily|weekly)|(\d+)([mh])|(daily|weekdays|weekly)@([01]\d|2[0-3]):([0-5]\d))$")


def check_schedule(spec: str | None) -> str | None:
    if spec in (None, "", "manual"):
        return None
    if not SCHEDULE_RE.match(spec):
        raise ValueError("--every must be hourly, daily, weekly, 30m, 6h, daily@07:00, weekdays@08:30 or weekly@09:00")
    return spec


def is_due(spec: str | None, last: str | None, now: _dt.datetime | None = None) -> bool:
    if not spec:
        return False
    now = now or _dt.datetime.now().astimezone()
    last_t = _dt.datetime.fromisoformat(last) if last else None
    m = SCHEDULE_RE.match(spec)
    if m.group(1) or m.group(2):
        step = {"hourly": 3600, "daily": 86400, "weekly": 7 * 86400}.get(m.group(1) or "", 0)
        if m.group(2):
            step = int(m.group(2)) * (60 if m.group(3) == "m" else 3600)
        return last_t is None or (now - last_t).total_seconds() >= step - 30
    kind, hh, mm = m.group(4), int(m.group(5)), int(m.group(6))
    slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if kind == "weekdays":
        while slot.weekday() >= 5 or slot > now:
            slot -= _dt.timedelta(days=1)
    elif kind == "weekly":
        if slot > now:
            slot -= _dt.timedelta(days=7)
    elif slot > now:
        slot -= _dt.timedelta(days=1)
    if kind == "weekly" and last_t is None:
        return now >= slot
    return last_t is None or last_t < slot


def add_goal(name: str, goal: str, every: str | None = None, registry=None) -> Plan:
    if not re.fullmatch(r"[A-Za-z0-9][\w-]{0,40}", name):
        raise ValueError("goal names: letters, digits, - and _ (up to 40)")
    every = check_schedule(every)
    p = plan(goal, registry) if registry is not None else None
    if p is not None and not p.ok:
        raise ValueError("the goal can't be planned yet:\n" + "\n".join(f"  - {x}" for x in p.problems))
    with STATE_LOCK:
        goals = load_goals()
        goals[name] = {"goal": goal, "every": every, "enabled": True,
                       "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                       "last_run": goals.get(name, {}).get("last_run")}
        save_goals(goals)
    return p


def remove_goal(name: str) -> bool:
    with STATE_LOCK:
        goals = load_goals()
        found = goals.pop(name, None) is not None
        save_goals(goals)
    return found


def record(run: AgentRun) -> None:
    with STATE_LOCK:
        with open(_path("runs.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(run.to_dict(), ensure_ascii=False) + "\n")
        if run.name:
            goals = load_goals()
            if run.name in goals:
                goals[run.name]["last_run"] = run.started
                goals[run.name]["last_status"] = run.status
                save_goals(goals)


def recent_runs(n: int = 20) -> list[dict]:
    try:
        with open(_path("runs.jsonl"), encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    return [json.loads(l) for l in reversed(lines) if l.strip()]


def run_goal(goal: str, registry, name: str = "", log=None, **limits) -> AgentRun:
    r = execute(plan(goal, registry), registry, name=name, log=log, **limits)
    record(r)
    return r


def run_due(registry, log=print) -> list[AgentRun]:
    """Run every saved goal whose schedule is due. Overlapping calls are skipped."""
    lock = _path("run-due.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock) < 3600:
            return []
        os.remove(lock)  # stale (crashed run)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    runs = []
    try:
        os.write(fd, str(os.getpid()).encode())
        for name, g in sorted(load_goals().items()):
            if g.get("enabled", True) and is_due(g.get("every"), g.get("last_run")):
                if log:
                    log(f"running goal {name!r}")
                runs.append(run_goal(g["goal"], registry, name=name))
    finally:
        os.close(fd)
        os.remove(lock)
    return runs
