"""A tiny WebSocket server for tests (server side of RFC 6455), plus a scripted handler hook."""
import base64
import hashlib
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Conn:
    def __init__(self, sock):
        self.sock, self.buf = sock, b""

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("client gone")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def handshake(self):
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.sock.recv(4096)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        self.path = lines[0].split(" ")[1]
        headers = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
        accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
        self.sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        self.headers = headers

    def send_frame(self, opcode, payload, fin=True):
        n = len(payload)
        head = bytes([(0x80 if fin else 0) | opcode])
        head += bytes([n]) if n < 126 else (bytes([126]) + struct.pack("!H", n) if n < 65536
                                            else bytes([127]) + struct.pack("!Q", n))
        self.sock.sendall(head + payload)

    def send_text(self, text):
        self.send_frame(1, text.encode())

    def recv(self):
        """Next client message as (opcode, bytes); control frames are returned too."""
        b1, b2 = self._read(2)
        assert b2 & 0x80, "client frames must be masked"
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read(8))[0]
        mask = self._read(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
        return b1 & 0x0F, data

    def close(self, code=1000, reason=""):
        try:
            self.send_frame(8, struct.pack("!H", code) + reason.encode())
        except OSError:
            pass


class Server:
    """Accepts connections and runs ``handler(conn)`` for each in a thread."""

    def __init__(self, handler):
        self.handler = handler
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                s, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._run, args=(s,), daemon=True).start()

    def _run(self, s):
        c = Conn(s)
        try:
            c.handshake()
            self.handler(c)
        except (ConnectionError, OSError, AssertionError):
            pass
        finally:
            s.close()

    def stop(self):
        self.sock.close()
