"""Pairing a phone: the best addresses for this server, as links with the token and as QR codes."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess

from . import qr


def _run(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def tailscale_name() -> str | None:
    """This machine's MagicDNS name (e.g. ``libra.tail1234.ts.net``), if Tailscale is up."""
    out = _run(["tailscale", "status", "--json"])
    try:
        name = (json.loads(out).get("Self") or {}).get("DNSName", "")
    except ValueError:
        return None
    return name.rstrip(".") or None


def tailscale_https() -> bool:
    """True if ``tailscale serve`` is publishing something over HTTPS on this machine."""
    out = _run(["tailscale", "serve", "status", "--json"])
    try:
        data = json.loads(out) if out.strip() else {}
    except ValueError:
        return False
    return bool(data.get("TCP") or data.get("Web"))


def mdns_name() -> str | None:
    """``<hostname>.local`` if an mDNS responder (avahi) is running, so phones on the Wi-Fi can use the name."""
    if not (shutil.which("avahi-daemon") or os.path.exists("/run/avahi-daemon/pid")):
        return None
    host = socket.gethostname().split(".")[0]
    return f"{host}.local" if host else None


def candidate_urls(port: int, local_only: bool = False) -> list[tuple[str, str]]:
    """``(label, base URL)`` pairs, best first: Tailscale HTTPS, Tailscale name, .local name, Wi-Fi addresses."""
    from .server import lan_addresses

    out = []
    ts = tailscale_name()
    if ts and tailscale_https():
        out.append(("anywhere, via Tailscale (HTTPS)", f"https://{ts}"))
    if not local_only:
        if ts:
            out.append(("anywhere, via Tailscale", f"http://{ts}:{port}"))
        name = mdns_name()
        if name:
            out.append(("home Wi-Fi, by name", f"http://{name}:{port}"))
        for ip in lan_addresses():
            out.append(("home Wi-Fi", f"http://{ip}:{port}"))
    return out


def describe(base: str) -> str:
    """A label for an address: Tailscale, the Wi-Fi name, or a Wi-Fi IP."""
    from urllib.parse import urlsplit

    host = urlsplit(base).hostname or ""
    if host.endswith(".ts.net"):
        return "anywhere, via Tailscale" + (" (HTTPS)" if base.startswith("https://") else "")
    if host.endswith(".local"):
        return "home Wi-Fi, by name"
    parts = host.split(".")
    if len(parts) == 4 and all(x.isdigit() for x in parts) and parts[0] == "100" and 64 <= int(parts[1]) <= 127:
        return "anywhere, via Tailscale"
    if host in ("127.0.0.1", "localhost", "::1"):
        return "this computer only"
    return "home Wi-Fi" if len(parts) == 4 and all(x.isdigit() for x in parts) else "your address"


def link(base: str, token: str | None) -> str:
    base = base.rstrip("/")
    return f"{base}/#token={token}" if token else f"{base}/"


def qr_svg(text: str) -> str:
    return qr.to_svg(qr.encode(text))


def qr_terminal(text: str) -> str:
    return qr.to_terminal(qr.encode(text))
