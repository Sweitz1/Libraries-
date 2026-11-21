"""Per-device access tokens: each paired phone or app gets its own token, revocable on its own.

Only a SHA-256 hash of each token is stored (``~/.libra/devices.json``), so the file can't be used
to sign in. The server's main token still works (for the owner and for scripts); pairing links and
QR codes carry a *device* token instead, so a lost phone or a leaked link can be cut off with

    libra devices list
    libra devices revoke <id>

Revoking a device also revokes the devices it paired.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import secrets
import threading

from . import home

FILE = "devices.json"
_LOCK = threading.Lock()
_cache: dict = {"mtime": None, "data": None, "path": None}
SEEN_EVERY = 600  # seconds between "last seen" writes per device


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load() -> dict:
    path = home.path(FILE)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"devices": []}
    if _cache["path"] == path and _cache["mtime"] == mtime and _cache["data"] is not None:
        return _cache["data"]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {"devices": []}
    _cache.update(path=path, mtime=mtime, data=data)
    return data


def _save(data: dict) -> None:
    path = home.path(FILE)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    _cache.update(path=path, mtime=os.path.getmtime(path), data=data)


def create(name: str = "", created_by: str = "owner") -> tuple[str, dict]:
    """A new device token (returned once, never stored) and its record."""
    name = re.sub(r"[^\w .@'()-]", "", name or "").strip()[:60] or f"device {_now()[:16].replace('T', ' ')}"
    token = "dev_" + secrets.token_urlsafe(24)
    record = {"id": secrets.token_hex(4), "name": name, "hash": _hash(token), "created": _now(),
              "created_by": created_by, "last_seen": None}
    with _LOCK:
        data = _load()
        data = {"devices": list(data.get("devices", [])) + [record]}
        _save(data)
    return token, record


def verify(token: str) -> dict | None:
    """The device record for ``token`` (constant-time comparison), or None."""
    if not token or not token.startswith("dev_"):
        return None
    h = _hash(token)
    data = _load()
    found = None
    for rec in data.get("devices", []):
        if hmac.compare_digest(rec["hash"], h):
            found = rec
    if found is not None:
        last = found.get("last_seen")
        stale = last is None or (_dt.datetime.now().astimezone() - _dt.datetime.fromisoformat(last)).total_seconds() \
            > SEEN_EVERY
        if stale:
            with _LOCK:
                fresh = _load()
                for rec in fresh.get("devices", []):
                    if rec["id"] == found["id"]:
                        rec["last_seen"] = _now()
                try:
                    _save(fresh)
                except OSError:
                    pass  # a read-only store shouldn't block sign-in
    return found


def listing() -> list[dict]:
    return [{k: v for k, v in rec.items() if k != "hash"} for rec in _load().get("devices", [])]


def revoke(device_id: str) -> list[str]:
    """Revoke a device and every device it paired (and theirs). Returns the revoked ids."""
    with _LOCK:
        data = _load()
        devices = list(data.get("devices", []))
        gone = {device_id} if any(d["id"] == device_id for d in devices) else set()
        changed = bool(gone)
        while changed:
            changed = False
            for d in devices:
                if d["id"] not in gone and d.get("created_by") in {f"device:{g}" for g in gone}:
                    gone.add(d["id"])
                    changed = True
        if gone:
            _save({"devices": [d for d in devices if d["id"] not in gone]})
    return sorted(gone)
