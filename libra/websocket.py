"""A small WebSocket client (RFC 6455), standard library only. Used for Discord's gateway.

    ws = WebSocket("wss://gateway.example/?v=10")
    ws.send('{"op": 1}')
    kind, data = ws.recv()       # kind is "text" or "binary"
    ws.close()

Ping frames are answered automatically. A close frame from the server raises
:class:`ConnectionClosed` with its code and reason. ``HTTPS_PROXY`` is honoured.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT, BINARY, CLOSE, PING, PONG, CONT = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0
MAX_MESSAGE = 16 * 1024 * 1024


class WebSocketError(OSError):
    pass


class ConnectionClosed(WebSocketError):
    def __init__(self, code: int | None, reason: str = ""):
        super().__init__(f"connection closed ({code}{': ' + reason if reason else ''})")
        self.code, self.reason = code, reason


def _connect_via_proxy(proxy: str, host: str, port: int, timeout: float) -> socket.socket:
    p = urlsplit(proxy if "://" in proxy else "http://" + proxy)
    sock = socket.create_connection((p.hostname, p.port or 80), timeout=timeout)
    req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    if p.username:
        cred = base64.b64encode(f"{p.username}:{p.password or ''}".encode()).decode()
        req += f"Proxy-Authorization: Basic {cred}\r\n"
    sock.sendall((req + "\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketError("proxy closed the connection")
        head += chunk
    status = head.split(b"\r\n", 1)[0]
    if b" 200" not in status:
        raise WebSocketError(f"proxy refused: {status.decode(errors='replace')}")
    return sock


class WebSocket:
    def __init__(self, url: str, headers: dict | None = None, timeout: float = 30,
                 ssl_context: ssl.SSLContext | None = None):
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss"):
            raise ValueError(f"not a WebSocket URL: {url}")
        secure = parts.scheme == "wss"
        host = parts.hostname or ""
        port = parts.port or (443 if secure else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")) if secure else None
        no_proxy = {h.strip() for h in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(",")}
        if proxy and host not in no_proxy and host not in ("127.0.0.1", "localhost"):
            raw = _connect_via_proxy(proxy, host, port, timeout)
        else:
            raw = socket.create_connection((host, port), timeout=timeout)
        if secure:
            ctx = ssl_context or ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        self.sock = raw
        self._send_lock = threading.Lock()
        self._buf = b""
        self.closed = False
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [f"GET {path} HTTP/1.1", f"Host: {host}" + (f":{port}" if parts.port else ""), "Upgrade: websocket",
                 "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = self._read_until(b"\r\n\r\n")
        status, *header_lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in status + " ":
            raise WebSocketError(f"handshake refused: {status}")
        got = {}
        for line in header_lines:
            if ":" in line:
                k, v = line.split(":", 1)
                got[k.strip().lower()] = v.strip()
        expect = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if got.get("sec-websocket-accept") != expect:
            raise WebSocketError("handshake failed: bad Sec-WebSocket-Accept")

    # -------------------------------------------------------------- low level
    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionClosed(None, "socket closed during handshake")
            self._buf += chunk
            if len(self._buf) > 65536:
                raise WebSocketError("handshake response too large")
        head, self._buf = self._buf.split(marker, 1)
        return head

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                self.closed = True
                raise ConnectionClosed(None, "socket closed")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            if self.closed and opcode != CLOSE:
                raise ConnectionClosed(None, "already closed")
            self.sock.sendall(header + mask + masked)

    def _read_frame(self) -> tuple[bool, int, bytes]:
        b1, b2 = self._read(2)
        fin, opcode = bool(b1 & 0x80), b1 & 0x0F
        if b1 & 0x70:
            raise WebSocketError("unexpected reserved bits (extensions are not supported)")
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read(8))[0]
        if n > MAX_MESSAGE:
            raise WebSocketError("frame too large")
        mask = self._read(4) if b2 & 0x80 else None
        payload = self._read(n)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    # --------------------------------------------------------------- public
    def send(self, message) -> None:
        if isinstance(message, str):
            self._send_frame(TEXT, message.encode("utf-8"))
        else:
            self._send_frame(BINARY, bytes(message))

    def ping(self, payload: bytes = b"") -> None:
        self._send_frame(PING, payload)

    def recv(self) -> tuple[str, object]:
        """Next whole message: ``("text", str)`` or ``("binary", bytes)``."""
        parts, kind = [], None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == PING:
                self._send_frame(PONG, payload)
                continue
            if opcode == PONG:
                continue
            if opcode == CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else None
                reason = payload[2:].decode("utf-8", "replace")
                if not self.closed:
                    try:
                        self._send_frame(CLOSE, payload[:2])
                    except OSError:
                        pass
                self.closed = True
                raise ConnectionClosed(code, reason)
            if opcode in (TEXT, BINARY):
                kind, parts = opcode, [payload]
            elif opcode == CONT and kind is not None:
                parts.append(payload)
            else:
                raise WebSocketError(f"unexpected opcode {opcode}")
            if sum(map(len, parts)) > MAX_MESSAGE:
                raise WebSocketError("message too large")
            if fin:
                data = b"".join(parts)
                return ("text", data.decode("utf-8")) if kind == TEXT else ("binary", data)

    def settimeout(self, seconds: float | None) -> None:
        self.sock.settimeout(seconds)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        try:
            self._send_frame(CLOSE, struct.pack("!H", code) + reason.encode()[:120])
        except OSError:
            pass
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass
