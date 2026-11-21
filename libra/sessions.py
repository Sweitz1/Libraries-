"""Browser sessions: the web app's sign-in kept in an HttpOnly cookie instead of in page storage.

Signing in with a token (``POST /v1/session``, ``Authorization: Bearer …``) creates a session: a random id
in a cookie that page scripts can't read (``HttpOnly``), that other sites can't make the browser send
(``SameSite=Strict``), sent only over HTTPS when the app is served over HTTPS (``Secure``). The session
acts as whoever signed in: the owner, or the device whose token was used.

A session ends when you sign out, after ``MAX_AGE``, when its device is revoked, or (for an owner
session) when the server's main token changes. Only hashes of session ids are stored.

Bearer tokens keep working for everything that isn't a browser (the Android app's checks, the Java client,
scripts)."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time

from . import home

FILE = "sessions.json"
COOKIE = "libra_session"
MAX_AGE = 30 * 24 * 3600
_LOCK = threading.Lock()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def owner_ref(main_token: str) -> str:
    """Ties owner sessions to the current main token: a new token ends them."""
    return _hash("owner:" + main_token)[:32]


def _load() -> list[dict]:
    try:
        with open(home.path(FILE), encoding="utf-8") as f:
            return list(json.load(f).get("sessions", []))
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    path = home.path(FILE)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"sessions": rows}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def create(principal: str, ref: str, agent: str = "") -> str:
    """A new session for ``principal`` ("owner" or "device:<id>"). Returns the id, which is never stored."""
    sid = "ses_" + secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        rows = [r for r in _load() if r["expires"] > now]  # drop expired ones while here
        rows.append({"hash": _hash(sid), "principal": principal, "ref": ref if principal == "owner" else "",
                     "created": int(now), "expires": int(now + MAX_AGE), "agent": agent[:80]})
        _save(rows)
    return sid


def lookup(sid: str, ref: str, device_ids) -> str | None:
    """The principal a session acts as, or None if it doesn't exist, expired, its device was revoked,
    or (owner) the main token changed."""
    if not sid or not sid.startswith("ses_"):
        return None
    h, now = _hash(sid), time.time()
    for r in _load():
        if not secrets.compare_digest(r["hash"], h) or r["expires"] <= now:
            continue
        p = r["principal"]
        if p == "owner":
            return p if secrets.compare_digest(r.get("ref", ""), ref) else None
        if p.startswith("device:") and p.split(":", 1)[1] in device_ids:
            return p
        return None
    return None


def end(sid: str) -> bool:
    if not sid:
        return False
    h = _hash(sid)
    with _LOCK:
        rows = _load()
        keep = [r for r in rows if r["hash"] != h]
        if len(keep) != len(rows):
            _save(keep)
            return True
    return False
