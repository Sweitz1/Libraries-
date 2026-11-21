"""Autonomous mode: pursue a goal with a plan → act → check → adjust loop (in the spirit of AutoGPT).

    libra agent auto "find the opening hours on https://example.com/contact then translate them to Spanish"

Each decision is logged as a *thought* with its reasoning, the action taken and a self-criticism,
so you can see why it did what it did. Compared with a plain ``libra agent run``, it:

* **recalls** related memories before starting;
* **repairs the plan**: a step with no confident match may use a weaker match, but only a
  read-only skill (never one that changes something);
* **retries** a step once after a temporary network error;
* **tries alternatives**: when a step fails or returns nothing, it tries the next best
  read-only skills for that step (up to two);
* **checks the result** against the goal: requested sentence count, words in quotes that must
  appear, the target language of a translation, and that something came out at all;
* **learns**: a goal that succeeded (especially after a repair) is saved as a procedure,
  so the next goal of the same shape reuses the working plan;
* **remembers** what each goal produced, so you can ask about it later.

There is no large language model behind this (Libra's own model is too small to reason
reliably), so the "thinking" is rule-based. It is good at goals made of your skills; it
won't invent new approaches. It always stops at the step, time and iteration limits, and
any skill that would change something still goes through the trust rules.
"""
from __future__ import annotations

import datetime as _dt
import re
import time
from dataclasses import asdict, dataclass, field

from . import agent
from .agent import AgentRun, Step, StepResult, _items, _subst

TRANSIENT = re.compile(r"timed out|could not fetch|HTTP (429|5\d\d)\b|temporarily|connection (reset|refused)", re.I)
EMPTY = re.compile(r"^\s*(\(no [^)]*\)|nothing (matched|to go through)|i couldn'?t find|no translation)", re.I)


@dataclass
class Thought:
    thought: str
    reasoning: str = ""
    action: str = ""
    criticism: str = ""


@dataclass
class AutoRun(AgentRun):
    thoughts: list = field(default_factory=list)
    iterations: int = 0
    learned: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        lines = [f"{self.status.upper()}: {self.goal}", ""]
        for i, t in enumerate(self.thoughts, 1):
            lines.append(f"{i}. {t.thought}" if isinstance(t, Thought) else f"{i}. {t['thought']}")
            for label in ("reasoning", "action", "criticism"):
                value = getattr(t, label) if isinstance(t, Thought) else t.get(label)
                if value:
                    lines.append(f"   {label}: {value}")
        lines += [f"  ✗ {p}" for p in self.problems]
        if self.learned:
            lines.append(f"\nLearned this as a procedure for goals shaped like: {self.learned!r}")
        if self.output:
            lines += ["", self.output]
        return "\n".join(lines)


def _alternatives(clause: str, registry, tried: set, has_prev: bool, limit: int = 2) -> list[Step]:
    """Other read-only skills that could do ``clause``, best first (weaker matches allowed)."""
    want = agent._content_words(clause)
    out = []
    for cand in registry.search(clause, 12):
        if cand.name in tried or cand.risk != "read":
            continue
        cover = len(want & agent._content_words(f"{cand.name.replace('_', ' ').replace('.', ' ')} {cand.description}"))
        if not want or cover * 3 < len(want):
            continue
        args, items, problem = agent._fill_args(cand, clause, has_prev)
        if problem:
            continue
        out.append(Step(clause, cand.name, args, bool(items), items, f"alternative ({cover}/{len(want)} key words)"))
        if len(out) >= limit:
            break
    return out


def _repair_plan(p: agent.Plan, registry, thoughts: list) -> agent.Plan:
    """Fill clauses the planner refused with weaker, read-only matches (and say so)."""
    if p.ok or not p.steps and not p.problems:
        return p
    fixed = agent.Plan(p.goal)
    for clause in agent.split_goal(p.goal):
        sub = agent.plan(clause, registry, use_procedures=False)
        if sub.ok:
            step = sub.steps[0]
            if fixed.steps and agent.PREV_RE.search(clause):  # re-plan in context so "it" binds
                step = agent.plan(f"x then {clause}", registry, use_procedures=False).steps[-1:] or [step]
                step = step[0]
            fixed.steps.append(step)
            continue
        alts = _alternatives(clause, registry, set(), bool(fixed.steps), limit=1)
        if alts:
            thoughts.append(Thought(f"No skill clearly matches {clause!r}; trying {alts[0].skill}.",
                                    "It is the closest read-only skill, so trying it can't change anything.",
                                    f"use {alts[0].skill}", "This is a weak match; the result may not be what you meant."))
            fixed.steps.append(alts[0])
        else:
            fixed.problems.append(sub.problems[0] if sub.problems else f"no skill for {clause!r}")
    return fixed


def _checks(goal: str) -> list[tuple[str, callable]]:
    """Things the final output must satisfy, read from the goal's wording."""
    from .knowledge import sentences

    checks = [("something came out", lambda out: bool(out.strip()) and not EMPTY.match(out))]
    m = re.search(r"\bin (\d+) (?:sentences?|lines?)\b", goal, re.I)
    if m:
        n = int(m.group(1))
        checks.append((f"at most {n} sentence(s)", lambda out, n=n: len(sentences(out)) <= n))
    for quoted in re.findall(r"\b(?:find|mention|contain|containing|about|with)\s+[\"“']([^\"”']{2,60})[\"”']", goal, re.I):
        checks.append((f"mentions {quoted!r}", lambda out, q=quoted: q.lower() in out.lower()))
    lang = re.search(r"\btranslat\w*\b.*?\b(?:to|into)\s+([^\W\d_][\w-]*)", goal, re.I)
    if lang:
        from .lang import code_for, detect

        code = code_for(lang.group(1))
        if code:
            checks.append((f"written in {lang.group(1)}",
                           lambda out, c=code: not out.strip() or (detect(out.split("\n[")[0]) or [("", 0)])[0][0] == c))
    return checks


def pursue(goal: str, registry, max_steps: int = 12, max_items: int = 5, max_seconds: float = 300,
           max_iterations: int = 3, remember: bool = True, learn: bool = True, name: str = "",
           retry_delay: float = 2.0, log=None) -> AutoRun:
    now = lambda: _dt.datetime.now().astimezone().isoformat(timespec="seconds")  # noqa: E731
    run = AutoRun(goal, name, now())
    say = run.thoughts.append

    def note(t: Thought):
        say(t)
        if log:
            log(t.thought)

    t0 = time.time()
    if remember:
        from .memory import Memory

        related = Memory().recall(re.sub(agent.URL_RE, " ", goal), 2)  # addresses alone don't make memories related
        if related:
            note(Thought("I remember something related: " + "; ".join(r["text"][:100] for r in related),
                         "Earlier results and your notes can explain or confirm what this run finds."))

    p = agent.plan(goal, registry)
    learned_plan = bool(p.steps) and p.steps[0].why.startswith("learned procedure")
    if learned_plan:
        note(Thought("I've done a goal shaped like this before; reusing the plan that worked.", p.steps[0].why,
                     " → ".join(s.skill for s in p.steps)))
    elif p.ok:
        note(Thought(f"Plan: {' → '.join(s.skill for s in p.steps)}.",
                     "; ".join(f"{s.skill}: {s.why}" for s in p.steps)))
    else:
        note(Thought("Part of the goal has no confident match.", "; ".join(p.problems), "repair the plan"))
        p = _repair_plan(p, registry, run.thoughts)
        if not p.ok:
            run.status, run.problems, run.finished = "invalid", p.problems, now()
            note(Thought("I can't plan this goal.", "; ".join(p.problems), "stop",
                         "Rephrase the step, or add a skill/plugin/MCP server/API that does it."))
            return run

    repaired = False
    steps = list(p.steps)
    checks = _checks(goal)
    for iteration in range(1, max_iterations + 1):
        run.iterations = iteration
        prev_text, prev_data, results = "", None, []
        failed_at = None
        for i, step in enumerate(steps):
            if i >= max_steps or time.time() - t0 > max_seconds:
                run.status = "stopped" if i >= max_steps else "timeout"
                run.problems.append(f"{'step' if i >= max_steps else 'time'} limit reached")
                run.steps, run.finished = results, now()
                return run
            tried = {step.skill}
            candidate = step
            while True:
                res, text, data, blocked = _run_step(candidate, registry, prev_text, prev_data, max_items,
                                                     retry_delay, run, note)
                if res.ok:
                    break
                if blocked:
                    run.status = "blocked"
                    run.problems.append(text)
                    note(Thought(f"{candidate.skill} needs your approval, so I stopped.", text, "stop",
                                 "I won't work around the trust rules."))
                    run.steps, run.finished = results + [res], now()
                    return run
                alts = _alternatives(step.clause, registry, tried, i > 0)
                if not alts:
                    break
                candidate = alts[0]
                tried.add(candidate.skill)
                note(Thought(f"{res.skill} didn't work for {step.clause!r}; trying {candidate.skill} instead.",
                             (res.text or "no output")[:160], f"use {candidate.skill}",
                             "An alternative may be less precise than what the plan chose."))
            if not res.ok:
                failed_at = i
                results.append(res)
                break
            if candidate is not step:
                steps[i] = candidate
                repaired = True
            results.append(res)
            prev_text, prev_data = text, data
        run.steps = results
        if failed_at is not None:
            run.status = "stopped"
            run.problems.append(f"step {failed_at + 1} ({steps[failed_at].clause!r}) failed and no alternative worked")
            note(Thought("I couldn't complete the goal.", run.problems[-1], "stop",
                         "Check the step's input (a URL, a language with data) or add a skill for it."))
            break
        unmet = [label for label, ok in checks if not ok(prev_text)]
        if not unmet:
            run.status, run.output = "done", prev_text[:20000]
            note(Thought("Done: the result meets every check I could read from the goal.",
                         ", ".join(label for label, _ in checks)))
            break
        note(Thought("The result doesn't meet: " + ", ".join(unmet) + ".", "Checked against the goal's wording.",
                     "adjust and retry" if iteration < max_iterations else "stop",
                     "My checks are simple; a human should judge quality."))
        adjusted = _adjust(steps, unmet, goal)
        if not adjusted or iteration == max_iterations:
            run.status = "incomplete"
            run.output = prev_text[:20000]
            run.problems.append("result doesn't meet: " + ", ".join(unmet))
            break
        steps = adjusted
        repaired = True
    run.finished = now()
    if run.status == "done":
        if learn and (repaired or len(steps) >= 2) and not learned_plan:
            run.learned = agent.learn(goal, steps, "repaired" if repaired else "")
        if remember:
            from .memory import Memory

            Memory().add(f"Goal {goal[:120]!r} on {run.started[:10]}: {run.output[:240]}", source="agent", kind="outcome")
    return run


def _run_step(step: Step, registry, prev_text: str, prev_data, max_items: int, retry_delay: float, run, note):
    """Run one step (once per item for 'each' steps). Returns (StepResult, text, data, blocked)."""
    s0 = time.time()

    def call(args):
        r = registry.run(step.skill, args)
        if not r.ok and TRANSIENT.search(r.text or ""):
            note(Thought(f"{step.skill} hit a temporary error; retrying once.", r.text[:120], "retry"))
            time.sleep(retry_delay)
            r = registry.run(step.skill, args)
        return r

    if step.foreach:
        items = (step.items or _items(prev_data, prev_text))[:max_items]
        if not items:
            return StepResult(step.clause, step.skill, False, "nothing to go through", 0.0), "", None, False
        texts, datas, ok_count = [], [], 0
        for item in items:
            r = call(_subst(step.args, prev_text, item))
            if not r.ok and ("approval" in r.text or "is blocked" in r.text):
                return StepResult(step.clause, step.skill, False, r.text, round(time.time() - s0, 2)), r.text, None, True
            if r.ok and not EMPTY.match(r.text or ""):
                ok_count += 1
                texts.append(f"## {item}\n{r.text}")
                datas.append(r.data)
        if ok_count < len(items):
            note(Thought(f"{len(items) - ok_count} of {len(items)} items failed for {step.skill}; keeping the rest."))
        text = "\n\n".join(texts)
        return (StepResult(step.clause, step.skill, ok_count > 0, text[:6000], round(time.time() - s0, 2), len(items)),
                text, datas, False)
    r = call(_subst(step.args, prev_text))
    blocked = not r.ok and ("approval" in r.text or "is blocked" in r.text)
    ok = r.ok and not EMPTY.match(r.text or "")
    return StepResult(step.clause, step.skill, ok, (r.text or "")[:6000], round(time.time() - s0, 2)), r.text, r.data, blocked


def _adjust(steps: list, unmet: list, goal: str) -> list | None:
    """Concrete fixes for failed checks (e.g. summarise to the requested length)."""
    new = list(steps)
    changed = False
    for label in unmet:
        m = re.match(r"at most (\d+) sentence", label)
        if m:
            n = m.group(1)
            if new and new[-1].skill == "summarize":
                if str(new[-1].args.get("sentences")) != n:
                    new[-1] = Step(new[-1].clause, "summarize", dict(new[-1].args, sentences=n), why="adjusted length")
                    changed = True
            else:
                new.append(Step(f"summarize it in {n} sentences", "summarize", {"text": "{prev}", "sentences": n},
                                why="added to meet the requested length"))
                changed = True
    return new if changed else None
