"""Durable goals: plans that survive restarts, pause for approval, and end with a checked result.

    libra jobs start "fetch https://example.com/news then summarize it in 3 sentences"
    libra jobs list | show <id> | resume [<id>] | approve <id> | skip <id> | cancel <id>

A *job* is a goal turned into a plan whose every step is recorded on disk
(``~/.libra/jobs/<id>/job.json``, replaced atomically after each change, plus an append-only
``events.jsonl``). Compared with ``libra agent run``:

* **Explicit dependencies.** Each step lists the steps it needs. A step that uses the previous
  output depends on it. A step that changes something keeps the order you wrote. If a step
  fails, the steps that depend on it are skipped, and independent ones still run.
* **Resumable.** Progress is saved before and after every step, and after every item of an
  "each of them" step. After a crash or restart, ``resume`` continues from where it stopped,
  without redoing finished work.
* **Safe after an interruption.** A read-only step that was cut off is run again. A step that
  *changes* something is never re-run blindly: the job waits, and you check and then ``approve``
  it (to run it again) or ``skip`` it.
* **Approval checkpoints.** A step whose skill needs approval pauses the job (state
  ``waiting``). The pause survives restarts; ``approve`` lets that one step run once.
* **Bounded retries.** Read-only steps are retried after temporary errors (by default up to 3
  attempts with growing delays). If they still fail, up to two alternative read-only skills
  are tried. Steps that change something are not retried automatically.
* **Checks.** Every step must produce something. The final output is checked against the
  goal's wording (sentence count, quoted words, target language). The result records which
  checks passed and a SHA-256 of the output, so it can be verified later.

Only one process runs a job at a time; this is enforced with a lock that the operating system
releases if the process dies. That is also how an interrupted job is recognised.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import shutil
import time

from . import home

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

FINAL = ("done", "failed", "cancelled", "needs_review")
DEFAULT_LIMITS = {"max_attempts": 3, "max_items": 5, "max_seconds": 600, "max_steps": 12}
OUTPUT_CHARS = 20000
LOCK_WAIT = 2.0  # seconds a runner or an owner decision waits for a job's lock before giving up


class JobError(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _root() -> str:
    d = home.path("jobs")
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)  # jobs hold the concrete arguments of changes; older installs may be wider
    except OSError:
        pass
    return d


def _dir(job_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", job_id or ""):
        raise JobError(f"not a job id: {job_id!r}")
    return os.path.join(_root(), job_id)


# ------------------------------------------------------------------ storage
def _save(job: dict) -> None:
    job["updated"] = _now()
    d = _dir(job["id"])
    tmp = os.path.join(d, "job.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1, ensure_ascii=False, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(d, "job.json"))
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _event(job: dict, what: str, **details) -> None:
    rec = {"at": _now(), "event": what, **details}
    with open(os.path.join(_dir(job["id"]), "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())  # the journal is evidence of what ran: it must survive a power cut too


def get(job_id: str) -> dict:
    try:
        with open(os.path.join(_dir(job_id), "job.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise JobError(f"no job {job_id}") from None


def events(job_id: str) -> list[dict]:
    try:
        with open(os.path.join(_dir(job_id), "events.jsonl"), encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
    except FileNotFoundError:
        return []
    out = []
    for i, line in enumerate(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            if i == len(lines) - 1:
                break  # a crash mid-append leaves a torn last line: everything before it still counts
            out.append({"event": "unreadable", "line": i + 1})  # damage elsewhere is shown, not hidden
    return out


def listing(n: int = 20) -> list[dict]:
    out = []
    for name in sorted(os.listdir(_root()), reverse=True):
        try:
            job = get(name)
        except (JobError, ValueError, OSError):
            continue
        out.append({k: job.get(k) for k in ("id", "name", "goal", "status", "created", "updated", "waiting")}
                   | {"running": is_running(name),
                      "steps_done": sum(s["status"] == "done" for s in job["steps"]), "steps": len(job["steps"])})
        if len(out) >= n:
            break
    return out


class _Lock:
    """An exclusive lock on one job; the OS drops it when the holder exits, even by a crash."""

    def __init__(self, job_id: str):
        self.path = os.path.join(_dir(job_id), "lock")
        self.fd = None

    def acquire(self, wait: float = 0.0) -> bool:
        """Take the lock, retrying for up to ``wait`` seconds. A runner holds it for the whole run, so a
        short wait only rides out brief holders (``is_running`` probes it, for example when the jobs are
        listed); it never lets a second runner in."""
        deadline = time.time() + wait
        while not self._try():
            if time.time() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def _try(self) -> bool:
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        if fcntl is None:  # pragma: no cover - no tested lock here: refuse rather than risk two runners
            os.close(self.fd)
            self.fd = None
            raise JobError("durable jobs need file locking (fcntl), which this platform doesn't have")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            return False
        os.ftruncate(self.fd, 0)
        os.write(self.fd, str(os.getpid()).encode())
        return True

    def release(self) -> None:
        if self.fd is not None:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        if not self.acquire(LOCK_WAIT):
            raise JobError("the job is running in another process")
        return self

    def __exit__(self, *exc):
        self.release()


def is_running(job_id: str) -> bool:
    lock = _Lock(job_id)
    if lock.acquire():
        lock.release()
        return False
    return True


# ----------------------------------------------------------------- creating
def _risk(registry, skill: str) -> str:
    try:
        return registry.get(skill).risk
    except KeyError:
        return "danger"


def _uses_prev(step) -> bool:
    return step.foreach and not step.items or any(isinstance(v, str) and ("{prev}" in v or "{item}" in v)
                                                  for v in step.args.values())


def create(goal: str, registry, name: str = "", autonomous: bool = False, permissions: dict | None = None,
           **limits) -> dict:
    """Plan ``goal`` and store it as a new job (not started yet). Raises ValueError if it can't be planned,
    or if the plan needs a skill outside ``permissions["skills"]``.

    ``permissions`` (from a saved goal): ``skills`` is the only skills this job may use (None: any);
    ``preapproved`` are skills that may run without asking in this job, even on outside content.
    Deny rules still win."""
    from . import agent

    p = agent.plan(goal, registry)
    if not p.ok and autonomous:
        from .autonomy import _repair_plan

        p = _repair_plan(p, registry, [])
    if not p.ok:
        raise ValueError("the goal can't be planned:\n" + "\n".join(f"  - {x}" for x in p.problems))
    perms = {"skills": None, "preapproved": []}
    if permissions:
        perms.update({k: (sorted(set(v)) if v is not None else None) for k, v in permissions.items()
                      if k in ("skills", "preapproved")})
    if perms["skills"] is not None:
        outside = sorted({s.skill for s in p.steps} - set(perms["skills"]))
        if outside:
            raise ValueError(f"the plan needs {', '.join(outside)}, which this goal isn't allowed to use "
                             f"(allowed: {', '.join(perms['skills']) or 'nothing'}). Save the goal again to "
                             "allow them.")
    lim = dict(DEFAULT_LIMITS, **{k: v for k, v in limits.items() if k in DEFAULT_LIMITS and v is not None})
    steps, last_change = [], None
    for i, s in enumerate(p.steps[: lim["max_steps"]]):
        risk = _risk(registry, s.skill)
        needs, inputs = set(), []
        if i and _uses_prev(s):
            needs.add(i - 1)  # it reads the previous step's output
            inputs = [i - 1]
        if risk != "read" and i:
            needs.add(i - 1)  # changes keep the order the goal gave them
        if last_change is not None:
            needs.add(last_change)  # nothing runs ahead of an earlier change
        if risk != "read":
            last_change = i
        steps.append({"n": i, "clause": s.clause, "skill": s.skill, "risk": risk, "args": s.args,
                      "foreach": s.foreach, "items": list(s.items), "why": s.why, "needs": sorted(needs), "inputs": inputs, "untrusted": False,
                      "status": "pending", "attempts": 0, "started": None, "finished": None,
                      "output": "", "data": None, "item_results": {}, "error": "", "tried": [s.skill]})
    job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    os.makedirs(_dir(job_id))
    job = {"id": job_id, "goal": goal, "name": name, "created": _now(), "updated": None, "status": "queued",
           "waiting": None, "limits": lim, "permissions": perms, "steps": steps, "approvals": [], "seconds": 0.0, "result": None}
    _save(job)
    _event(job, "created", steps=[f"{s['n'] + 1}. {s['skill']} (needs {[x + 1 for x in s['needs']] or 'nothing'})"
                                  for s in steps])
    return job


# ------------------------------------------------------------------ running
def _calls(job: dict, step: dict) -> list[tuple[str | None, dict]]:
    """The calls a step still has to make: (item, concrete arguments), one per remaining item."""
    from .agent import _items, _subst

    prev_text, prev_data = _prev(job, step)
    if not step["foreach"]:
        return [(None, _subst(step["args"], prev_text))]
    items = (step["items"] or _items(prev_data, prev_text))[: int(job["limits"]["max_items"])]
    return [(it, _subst(step["args"], prev_text, it)) for it in items if it not in step["item_results"]]


def _idem_key(job: dict, step: dict, item: str | None) -> str:
    """Stable for one call of one step of one job: the same after a retry or a restart."""
    raw = json.dumps([job["id"], step["n"], step["skill"], item], ensure_ascii=False)
    return "libra-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _effect(registry, step: dict, args: dict, result, item: str | None) -> None:
    """Check that a change took effect, with the skill's own read-only ``verify``. No verifier: not
    checked. A verifier that fails, can't tell or crashes means a person should look (needs_review)."""
    try:
        verify = registry.get(step["skill"]).verify
    except KeyError:
        verify = None
    if verify is None:
        return
    try:
        ok = verify(args, result)
        status, note = {True: "passed", False: "failed"}.get(ok, "unknown"), ""
    except Exception as exc:  # a verifier that breaks proves nothing
        status, note = "unknown", f"{type(exc).__name__}: {exc}"[:300]
    step.setdefault("evidence", []).append({"item": item, "status": status, "at": _now(),
                                            **({"note": note} if note else {})})


def _taint(job: dict, step: dict, registry) -> None:
    try:
        own = registry.get(step["skill"]).untrusted_output
    except KeyError:
        own = True
    step["untrusted"] = bool(own or any(job["steps"][d].get("untrusted") for d in step.get("inputs", [])))


def _operation(job: dict, step: dict) -> dict:
    """The exact operation a step would perform now: its skill, risk and concrete arguments (after
    ``{prev}``/``{item}`` substitution, one call per remaining item). An approval is bound to its digest,
    so it can't be used for anything else: other arguments, another skill, another risk, another job."""
    from .trust import redact

    calls = [args for _, args in _calls(job, step)]
    record = {"schema": "libra.operation.v1", "job": job["id"], "step": step["n"], "skill": step["skill"],
              "risk": step["risk"], "calls": calls}
    blob = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str)
    return {"digest": hashlib.sha256(blob.encode("utf-8")).hexdigest(), "skill": step["skill"],
            "risk": step["risk"], "calls": redact(calls[:5]), "count": len(calls)}  # what the owner is shown


def _wait(job: dict, step: dict, kind: str, reason: str) -> None:
    job["status"] = "waiting"
    job["waiting"] = {"step": step["n"], "kind": kind, "reason": reason, "since": _now()}
    if kind != "limit":
        op = _operation(job, step)
        job["waiting"]["operation"] = op
        job["waiting"]["reason"] = reason + f" (operation {op['digest'][:12]})"
    _save(job)
    _event(job, "waiting", step=step["n"] + 1, kind=kind, reason=job["waiting"]["reason"],
           **({"operation": job["waiting"]["operation"]["digest"]} if kind != "limit" else {}))


def _reconcile(job: dict, s: dict, registry) -> bool | None:
    """After a crash during a change: ask the skill's read-only ``reconcile`` whether the interrupted call
    had already happened. True: record it as done. False: it can run again (trust rules apply as usual).
    None (no reconcile, can't tell, or it crashed): a person decides."""
    try:
        reconcile = registry.get(s["skill"]).reconcile
    except KeyError:
        reconcile = None
    calls = _calls(job, s)
    if reconcile is None or not calls:
        return None
    item, args = calls[0]  # calls run in order, so only the first unfinished one can have been in flight
    try:
        happened = reconcile(args)
    except Exception:
        happened = None
    if happened is None:
        return None
    _event(job, "reconciled", step=s["n"] + 1, item=item, happened=bool(happened))
    if not happened:
        return False
    note = "already done before the restart (confirmed by reconcile)"
    s.setdefault("evidence", []).append({"item": item, "status": "passed", "at": _now(), "note": note})
    if s["foreach"]:
        s["item_results"][item] = {"ok": True, "text": note, "data": None}
        return True
    s["status"], s["finished"], s["output"], s["data"], s["error"] = "done", _now(), note, None, ""
    _taint(job, s, registry)
    return True


def _recover(job: dict, registry=None) -> None:
    """Handle steps that were in progress when the previous runner stopped."""
    for s in job["steps"]:
        if s["status"] != "running":
            continue
        if s["risk"] == "read":
            s["status"] = "pending"
            _event(job, "recovered", step=s["n"] + 1, action="re-run (read-only)")
        else:
            outcome = _reconcile(job, s, registry) if registry is not None else None
            if outcome is not None:
                if s["status"] == "running":
                    s["status"] = "pending"  # the rest of its items (or the call itself) still to do
                _save(job)
                continue
            s["status"] = "pending"
            done = len(s["item_results"])
            _wait(job, s, "interrupted",
                  f"step {s['n'] + 1} ({s['skill']}) changes something and was interrupted"
                  + (f" after {done} item(s)" if done else "") + "; it may or may not have taken effect. "
                  "Check, then approve to run it again or skip it.")
            return


def _approved(job: dict, n: int, digest: str) -> bool:
    """An unused approval for exactly this operation. Approvals without a digest (from before 0.16)
    don't count: the owner confirms the concrete action again."""
    return any(a["step"] == n and a.get("operation_digest") == digest and not a.get("used")
               for a in job["approvals"])


def _use_approval(job: dict, n: int, digest: str) -> None:
    for a in job["approvals"]:
        if a["step"] == n and a.get("operation_digest") == digest and not a.get("used"):
            a["used"] = _now()
            return


def _prev(job: dict, step: dict) -> tuple[str, object]:
    if not step["needs"]:
        return "", None
    src = job["steps"][max(step["needs"])]
    return src["output"], src["data"]


def _jsonable(data):
    try:
        text = json.dumps(data, default=str)
    except (TypeError, ValueError):
        return None
    return data if len(text) <= 200_000 else None


def _call(registry, skill: str, args: dict, risk: str, attempts: int, retry_delay: float, job, step,
          approved: bool = False, item: str | None = None):
    from .autonomy import TRANSIENT
    from .skills import _call as current

    r = None
    if risk != "read":
        current.key = _idem_key(job, step, item)
    try:
        for k in range(attempts if risk == "read" else 1):
            if k:
                time.sleep(retry_delay * (2 ** (k - 1)))
                _event(job, "retry", step=step["n"] + 1, attempt=k + 1, after=r.text[:200])
            r = registry.run(skill, args, approved=approved)
            if r.ok or not TRANSIENT.search(r.text or ""):
                break
    finally:
        current.key = None
    if risk != "read" and r.ok:
        _effect(registry, step, args, r, item)
    return r


def _blocked(text: str) -> bool:
    return "needs your approval" in text or "is blocked" in text


def _run_step(job: dict, step: dict, registry, retry_delay: float, cancelled, approved: bool = False) -> str:
    """Run one step. Returns "done", "failed", "waiting" or "cancelled"."""
    from .agent import _items, _subst
    from .autonomy import EMPTY

    prev_text, prev_data = _prev(job, step)
    attempts = int(job["limits"]["max_attempts"])
    step["status"], step["started"] = "running", step["started"] or _now()
    step["attempts"] += 1
    _save(job)  # from here, an interruption is visible and handled by _recover
    _event(job, "step started", step=step["n"] + 1, skill=step["skill"], attempt=step["attempts"])
    if step["foreach"]:
        items = (step["items"] or _items(prev_data, prev_text))[: int(job["limits"]["max_items"])]
        if not items:
            step["status"], step["error"] = "failed", "nothing to go through"
            return "failed"
        for item in items:
            if item in step["item_results"]:
                continue  # finished before an interruption
            if cancelled():
                step["status"] = "pending"
                return "cancelled"
            r = _call(registry, step["skill"], _subst(step["args"], prev_text, item), step["risk"], attempts,
                      retry_delay, job, step, approved, item)
            if not r.ok and _blocked(r.text):
                step["status"], step["error"] = "failed", r.text
                return "failed"
            step["item_results"][item] = {"ok": bool(r.ok and not EMPTY.match(r.text or "")),
                                          "text": (r.text or "")[:OUTPUT_CHARS], "data": _jsonable(r.data)}
            _save(job)  # each finished item is kept
        good = [(k, v) for k, v in step["item_results"].items() if v["ok"]]
        step["output"] = "\n\n".join(f"## {k}\n{v['text']}" for k, v in good)[:OUTPUT_CHARS]
        step["data"] = [v["data"] for _, v in good]
        failed = len(step["item_results"]) - len(good)
        if not good:
            step["status"], step["error"] = "failed", f"all {failed} item(s) failed"
            return "failed"
        step["error"] = f"{failed} of {len(items)} item(s) failed" if failed else ""
    else:
        r = _call(registry, step["skill"], _subst(step["args"], prev_text), step["risk"], attempts, retry_delay,
                  job, step, approved)
        if not r.ok or EMPTY.match(r.text or ""):
            step["status"], step["error"] = "failed", (r.text or "no output")[:2000]
            return "failed"
        step["output"], step["data"] = (r.text or "")[:OUTPUT_CHARS], _jsonable(r.data)
    step["status"], step["finished"] = "done", _now()
    return "done"


def _try_alternatives(job: dict, step: dict, registry, retry_delay: float, cancelled) -> str:
    """A read-only step failed: try other read-only skills for the same clause (up to two)."""
    from .agent import Step
    from .autonomy import _alternatives

    if step["risk"] != "read":
        return "failed"
    scope = (job.get("permissions") or {}).get("skills")
    for alt in _alternatives(step["clause"], registry, set(step["tried"]), bool(step["needs"])):
        if scope is not None and alt.skill not in scope:
            continue  # a saved goal only uses the skills it was allowed
        old = step["skill"]
        step.update(skill=alt.skill, args=alt.args, foreach=alt.foreach, items=list(alt.items),
                    item_results={}, error="", why=f"alternative after {old} failed")
        step["tried"].append(alt.skill)
        _event(job, "alternative", step=step["n"] + 1, instead_of=old, skill=alt.skill)
        outcome = _run_step(job, step, registry, retry_delay, cancelled)
        if outcome != "failed":
            return outcome
    return "failed"


def _finish(job: dict) -> None:
    from .autonomy import _checks

    statuses = {s["status"] for s in job["steps"]}
    done = [s for s in job["steps"] if s["status"] == "done"]
    output = done[-1]["output"] if done else ""
    # What each change actually did, by its own read-only check: the output text alone can't show that.
    effects = []
    for s in done:
        if s["risk"] == "read":
            continue
        seen = {e["status"] for e in s.get("evidence", [])}
        status = ("failed" if "failed" in seen else "unknown" if "unknown" in seen else
                  "passed" if seen else "not_checked")
        effects.append({"step": s["n"] + 1, "skill": s["skill"], "status": status})
    bad = [e for e in effects if e["status"] in ("failed", "unknown")]
    if statuses <= {"done"}:
        checks = [{"check": label, "ok": bool(fn(output))} for label, fn in _checks(job["goal"])]
        verified = all(c["ok"] for c in checks) and not bad
        job["status"] = "done" if verified else "needs_review"
    else:
        checks, verified = [], False
        job["status"] = "failed"
    job["waiting"] = None
    job["result"] = {"output": output, "sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                     "checks": checks, "effects": effects, "verified": verified, "finished": _now(),
                     "problems": [f"step {s['n'] + 1} ({s['skill']}): {s['error'] or s['status']}"
                                  for s in job["steps"] if s["status"] != "done"]
                     + [f"step {e['step']} ({e['skill']}): its effect check {'failed' if e['status'] == 'failed' else 'could not tell'}"
                        for e in bad]}
    _save(job)
    _event(job, "finished", status=job["status"], verified=verified, sha256=job["result"]["sha256"],
           **({"effects": {e["step"]: e["status"] for e in effects}} if effects else {}))


def run(job_id: str, registry, log=None, retry_delay: float = 2.0) -> dict:
    """Run (or resume) a job until it finishes, waits for approval, is cancelled or hits its time limit."""
    lock = _Lock(job_id)
    if not lock.acquire(LOCK_WAIT):
        raise JobError(f"job {job_id} is already running in another process")
    try:
        job = get(job_id)
        if job["status"] in FINAL:
            return job
        if job["status"] == "waiting" and job["waiting"]["kind"] != "limit":
            w = job["waiting"]
            step = job["steps"][w["step"]]
            if not _approved(job, w["step"], _operation(job, step)["digest"]):
                if any(a["step"] == w["step"] and not a.get("used") for a in job["approvals"]):
                    # approved, but not this operation (it changed, or the approval predates digests)
                    _event(job, "approval refused", step=w["step"] + 1, reason="the operation changed")
                    _wait(job, step, w["kind"], "the operation changed since it was approved, so the approval "
                          "doesn't cover it. Check it again: " + w["reason"].split(" (operation ")[0])
                return job  # paused until the owner approves or skips
        if job["status"] != "waiting":
            _recover(job, registry)
            if job["status"] == "waiting":
                return job
        job["status"], job["waiting"] = "running", None
        _save(job)
        _event(job, "resumed" if any(s["attempts"] for s in job["steps"]) else "started", pid=os.getpid())
        cancel_flag = os.path.join(_dir(job_id), "cancel")
        cancelled = lambda: os.path.exists(cancel_flag)  # noqa: E731
        t0, spent = time.time(), float(job.get("seconds", 0.0))  # the time limit applies to each run
        policy = getattr(registry, "policy", None)
        while True:
            if cancelled():
                job["status"] = "cancelled"
                job["seconds"] = round(spent + time.time() - t0, 2)
                _save(job)
                _event(job, "cancelled")
                return job
            for s in job["steps"]:  # a step whose dependency didn't finish can't run
                if s["status"] == "pending" and any(job["steps"][d]["status"] in ("failed", "skipped")
                                                    for d in s["needs"]):
                    s["status"], s["error"] = "skipped", "a step it depends on did not finish"
                    _event(job, "skipped", step=s["n"] + 1)
            ready = [s for s in job["steps"] if s["status"] == "pending"
                     and all(job["steps"][d]["status"] == "done" for d in s["needs"])]
            if not ready:
                job["seconds"] = round(spent + time.time() - t0, 2)
                _finish(job)
                return job
            if time.time() - t0 > float(job["limits"]["max_seconds"]):
                job["seconds"] = round(spent + time.time() - t0, 2)
                _wait(job, ready[0], "limit", f"time limit of {job['limits']['max_seconds']}s reached; "
                      "resume to continue")
                return job
            step = ready[0]
            perms = job.get("permissions") or {}
            if perms.get("skills") is not None and step["skill"] not in perms["skills"]:
                step["status"], step["error"] = "failed", f"{step['skill']} is outside this goal's permissions"
                _save(job)
                continue
            digest = _operation(job, step)["digest"]
            approved = _approved(job, step["n"], digest)  # one approval covers this exact operation, once
            preapproved = step["skill"] in (perms.get("preapproved") or [])
            confirm = getattr(registry, "confirm", None)
            # outside content (a web page, an API, memories) is data: it may not drive a change unasked
            sources = [job["steps"][d] for d in step.get("inputs", []) if job["steps"][d].get("untrusted")]
            if step["risk"] != "read" and sources and not (approved or preapproved):
                if confirm is not None and confirm(registry.get(step["skill"]), step["args"]):
                    job["approvals"].append({"step": step["n"], "by": "terminal", "at": _now(),
                                             "kind": "untrusted_input", "operation_digest": digest})
                    approved = True
                else:
                    _wait(job, step, "untrusted_input",
                          f"step {step['n'] + 1} ({step['skill']}, a {step['risk']} skill) would act on content "
                          f"from outside (the output of step {sources[0]['n'] + 1}, {sources[0]['skill']}). "
                          f"Check it, then approve: {step['clause']}")
                    return job
            if policy is not None and not approved:
                decision, reason = policy.decide(step["skill"], step["risk"])
                if decision == "ask" and preapproved:
                    approved = True
                elif decision == "ask" and confirm is not None and confirm(registry.get(step["skill"]),
                                                                            step["args"]):
                    job["approvals"].append({"step": step["n"], "by": "terminal", "at": _now(), "kind": "approval",
                                             "operation_digest": digest})
                    _event(job, "approved", step=step["n"] + 1, by="terminal")
                    approved = True
                elif decision == "ask":
                    _wait(job, step, "approval", f"step {step['n'] + 1} ({step['skill']}, a {step['risk']} "
                          f"skill) needs your approval ({reason}): {step['clause']}")
                    return job
            if log:
                log(f"step {step['n'] + 1}: {step['skill']} ← {step['clause']}")
            if approved:
                _use_approval(job, step["n"], digest)
            step["operation"] = digest  # what was dispatched; _run_step saves it before the skill is called
            outcome = _run_step(job, step, registry, retry_delay, cancelled, approved)
            if outcome == "failed":
                outcome = _try_alternatives(job, step, registry, retry_delay, cancelled)
            if outcome == "done":  # taint travels with the data
                _taint(job, step, registry)
            _save(job)
            _event(job, "step " + outcome, step=step["n"] + 1, skill=step["skill"],
                   **({"error": step["error"][:300]} if step["error"] else {}))
    finally:
        lock.release()


# --------------------------------------------------------- owner decisions
def approve(job_id: str, by: str = "owner", digest: str | None = None) -> dict:
    """Approve the operation the job is waiting on. ``digest`` (or at least its first 8 characters), when
    given, must match it, so an approval made while looking at an older version of the job is refused."""
    with _Lock(job_id):
        job = get(job_id)
        if job["status"] != "waiting":
            raise JobError(f"job {job_id} is {job['status']}, not waiting")
        w = job["waiting"]
        n = w["step"]
        op = w.get("operation") if w["kind"] != "limit" else None
        if op is None and w["kind"] != "limit":
            op = _operation(job, job["steps"][n])  # waiting since before 0.16: bind to what would run now
        if digest is not None and (op is None or len(digest) < 8 or not op["digest"].startswith(digest.lower())):
            raise JobError(f"job {job_id} is not waiting on operation {digest}: it changed; check it again")
        rec = {"step": n, "by": by, "at": _now(), "kind": w["kind"]}
        if op is not None:
            rec["operation_digest"] = op["digest"]
        job["approvals"].append(rec)
        _save(job)
        _event(job, "approved", step=n + 1, by=by, **({"operation": op["digest"]} if op else {}))
        return job


def skip(job_id: str, by: str = "owner") -> dict:
    with _Lock(job_id):
        job = get(job_id)
        if job["status"] != "waiting":
            raise JobError(f"job {job_id} is {job['status']}, not waiting")
        step = job["steps"][job["waiting"]["step"]]
        step["status"], step["error"] = "skipped", f"skipped by {by}"
        job["status"], job["waiting"] = "queued", None
        _save(job)
        _event(job, "skipped", step=step["n"] + 1, by=by)
        return job


def cancel(job_id: str, by: str = "owner") -> dict:
    flag = os.path.join(_dir(job_id), "cancel")
    get(job_id)
    with open(flag, "w") as f:
        f.write(by)
    lock = _Lock(job_id)
    if lock.acquire():  # not running: cancel now; otherwise the runner stops at its next check
        try:
            job = get(job_id)
            if job["status"] not in FINAL:
                job["status"], job["waiting"] = "cancelled", None
                _save(job)
                _event(job, "cancelled", by=by)
            return job
        finally:
            lock.release()
    return get(job_id)


def resume_all(registry, log=None, retry_delay: float = 2.0) -> list[dict]:
    """Continue every job that was interrupted (or queued) and isn't running. Waiting jobs stay waiting
    unless they were approved."""
    out = []
    for item in reversed(listing(1000)):
        if item["running"] or item["status"] in FINAL:
            continue
        job = get(item["id"])
        if job["status"] == "waiting" and not any(a["step"] == job["waiting"]["step"] and not a.get("used")
                                                  for a in job["approvals"]):
            continue  # needs the owner (approval, an interrupted change, or a time limit to lift)
        if log:
            log(f"resuming job {job['id']}: {job['goal']}")
        try:
            out.append(run(job["id"], registry, log=log, retry_delay=retry_delay))
        except JobError:
            continue
    return out


def prune(keep: int = 200) -> None:
    names = sorted(os.listdir(_root()))
    for name in names[: max(0, len(names) - keep)]:
        try:
            if get(name)["status"] in FINAL:
                shutil.rmtree(_dir(name), ignore_errors=True)
        except (JobError, ValueError, OSError):
            pass


# --------------------------------------------------------------- reporting
def to_run(job: dict):
    """The job as an :class:`agent.AgentRun`, for the run history and notifications."""
    from .agent import AgentRun, StepResult

    status = {"waiting": "blocked", "needs_review": "stopped", "failed": "stopped", "queued": "running"}.get(
        job["status"], job["status"])
    r = AgentRun(job["goal"], job.get("name", ""), job["created"], job.get("updated") or "", status)
    for s in job["steps"]:
        if s["status"] in ("done", "failed"):
            r.steps.append(StepResult(s["clause"], s["skill"], s["status"] == "done",
                                      (s["output"] or s["error"])[:6000], 0.0, len(s["item_results"])))
    if job["status"] == "waiting":
        r.problems.append(job["waiting"]["reason"])
    if job.get("result"):
        r.problems += job["result"]["problems"]
        r.problems += [f"check failed: {c['check']}" for c in job["result"]["checks"] if not c["ok"]]
        if job["status"] == "done":
            r.output = job["result"]["output"]
    return r


def describe(job: dict) -> str:
    mark = {"done": "✓", "failed": "✗", "skipped": "–", "running": "…", "pending": " "}
    lines = [f"{job['status'].upper()}  {job['id']}{'  ' + job['name'] if job.get('name') else ''}",
             f"Goal: {job['goal']}"]
    for s in job["steps"]:
        needs = f" (after {', '.join(str(d + 1) for d in s['needs'])})" if s["needs"] else ""
        extra = f" ×{len(s['item_results'])}" if s["item_results"] else ""
        lines.append(f"  [{mark.get(s['status'], '?')}] {s['n'] + 1}. {s['skill']}{extra}{needs}: {s['clause']}")
        if s["error"]:
            lines.append(f"        {s['error'][:200]}")
    if job.get("waiting"):
        lines.append(f"\nWaiting: {job['waiting']['reason']}")
        op = job["waiting"].get("operation")
        if op:
            lines.append(f"  would run {op['skill']} ({op['risk']}), {op['count']} call(s): "
                         + json.dumps(op["calls"], ensure_ascii=False)[:600])
            lines.append(f"  operation {op['digest']}  (libra jobs approve {job['id']} --digest {op['digest'][:12]})")
        lines.append(f"  libra jobs approve {job['id']}   or   libra jobs skip {job['id']}")
    res = job.get("result")
    if res:
        for c in res["checks"]:
            lines.append(f"  check {'✓' if c['ok'] else '✗'} {c['check']}")
        for e in res.get("effects", []):
            mark = {"passed": "✓", "failed": "✗", "unknown": "?"}.get(e["status"], "·")
            lines.append(f"  effect {mark} step {e['step']} ({e['skill']}): "
                         + {"passed": "confirmed", "failed": "check failed", "unknown": "could not tell",
                            "not_checked": "not checked (the skill has no verify)"}[e["status"]])
        lines.append(f"  result sha256 {res['sha256'][:16]}… ({'verified' if res['verified'] else 'not verified'})")
        if res["output"]:
            lines += ["", res["output"][:4000]]
    return "\n".join(lines)
