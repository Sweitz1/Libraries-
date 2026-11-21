import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures"))
from ws_server import Server  # noqa: E402

from libra.websocket import ConnectionClosed, WebSocket, WebSocketError  # noqa: E402


PONGS = []


def echo(conn):
    conn.send_frame(9, b"are-you-there")  # a ping first: the client must answer it (when it next reads)
    while True:
        op, data = conn.recv()
        if op == 10:
            PONGS.append(data)
            continue
        if op == 8:
            conn.close(1000)
            return
        if data == b"fragmented":
            conn.send_frame(1, b"hello ", fin=False)
            conn.send_frame(9, b"mid")  # control frame in the middle of a fragmented message
            conn.send_frame(0, "wörld".encode(), fin=False)
            conn.send_frame(0, b"!", fin=True)
        elif data == b"bye":
            conn.close(4004, "Authentication failed.")
            return
        else:
            conn.send_frame(op, data)  # echo, keeping text/binary


class WebSocketTest(unittest.TestCase):
    def setUp(self):
        PONGS.clear()
        self.server = Server(echo)

    def tearDown(self):
        self.server.stop()

    def connect(self):
        return WebSocket(f"ws://127.0.0.1:{self.server.port}/gateway?v=10", timeout=5)

    def test_messages(self):
        ws = self.connect()
        ws.send("hi there")
        self.assertEqual(ws.recv(), ("text", "hi there"))  # the server's ping was answered on the way
        big = "x" * 70000  # 64-bit length frames
        ws.send(big)
        self.assertEqual(ws.recv(), ("text", big))
        ws.send(b"\x00\x01binary")
        self.assertEqual(ws.recv(), ("binary", b"\x00\x01binary"))
        ws.send("fragmented")
        self.assertEqual(ws.recv(), ("text", "hello wörld!"))
        ws.send("sync")  # make sure the server has read everything we sent
        self.assertEqual(ws.recv(), ("text", "sync"))
        self.assertEqual(PONGS, [b"are-you-there", b"mid"])  # both pings answered with their payload
        ws.close()
        self.assertTrue(ws.closed)

    def test_close_code(self):
        ws = self.connect()
        ws.send("bye")
        with self.assertRaises(ConnectionClosed) as cm:
            ws.recv()
        self.assertEqual((cm.exception.code, cm.exception.reason), (4004, "Authentication failed."))

    def test_bad_handshake(self):
        import http.server
        import threading

        httpd = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
        threading.Thread(target=httpd.handle_request, daemon=True).start()
        with self.assertRaises(WebSocketError):
            WebSocket(f"ws://127.0.0.1:{httpd.server_address[1]}/", timeout=5)
        httpd.server_close()
        with self.assertRaises(ValueError):
            WebSocket("http://example.com/")


if __name__ == "__main__":
    unittest.main()
