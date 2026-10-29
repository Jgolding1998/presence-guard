import http.client
import json
import socket
import threading
import time
import unittest
from unittest import mock

from presence_guard.controller import Controller
from presence_guard import server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller()
        self.token = "a" * 64
        self.server = server.LocalServer(("127.0.0.1", 0), server.make_handler(self.controller, self.token))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.origin = "http://127.0.0.1:" + str(self.port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
        self.controller.close()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, path, data, headers=None):
        return self.request("POST", path, json.dumps(data).encode(),
                            {"Origin": self.origin, "Content-Type": "application/json", **(headers or {})})

    def raw(self, request, half_close=False):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as connection:
            connection.sendall(request)
            if half_close:
                connection.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                try:
                    chunk = connection.recv(65536)
                except ConnectionResetError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)

    def short_post(self, path, data):
        body = json.dumps({"token": self.token, **data}).encode()
        headers = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\n"
                   f"Content-Type: application/json\r\nContent-Length: {len(body) + 1}\r\n\r\n").encode()
        return self.raw(headers + body, half_close=True)

    def test_short_complete_start_json_does_not_arm(self):
        self.assertIn(b" 400 ", self.short_post("/api/start", {"mode": "privacy"}))
        state = self.controller.snapshot()
        self.assertFalse(state["active"])
        self.assertFalse(state["lock_armed"])
        self.assertEqual(state["events"], [])

    def test_short_complete_stop_json_does_not_disarm(self):
        self.controller.start("privacy")
        self.assertIn(b" 400 ", self.short_post("/api/stop", {}))
        state = self.controller.snapshot()
        self.assertTrue(state["active"])
        self.assertTrue(state["lock_armed"])

    def test_short_complete_demo_json_does_not_change_counts(self):
        self.assertIn(b" 400 ", self.short_post("/api/demo", {"people": 2}))
        self.assertEqual(self.controller.snapshot()["demo_people"], 1)

    def test_session_and_assets_are_local_no_store(self):
        for path in ("/", "/app.js", "/style.css", "/favicon.svg", "/api/status", "/api/session"):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertNotIn("Python", headers["Server"])
                self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(json.loads(self.request("GET", "/api/session")[2]), {"token": self.token})

    def test_start_stop_and_mutually_exclusive_mode(self):
        status, _, body = self.post("/api/start", {"token": self.token, "mode": "privacy"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["lock_armed"])
        self.assertFalse(json.loads(body)["sheet_armed"])
        self.assertEqual(self.post("/api/start", {"token": self.token, "mode": "boss"})[0], 409)
        status, _, body = self.post("/api/stop", {"token": self.token})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["active"])
        self.assertFalse(json.loads(body)["lock_armed"])

    def test_demo_allows_only_counts_and_fault_not_actions(self):
        for people in (0, 1, 2, None):
            self.assertEqual(self.post("/api/demo", {"token": self.token, "people": people})[0], 200)
        for people in (True, -1, 3, 1.2, "2", 10 ** 400):
            self.assertEqual(self.post("/api/demo", {"token": self.token, "people": people})[0], 409)

    def test_rebinding_and_cross_origin_rejected(self):
        for headers in ({"Host": "localhost:" + str(self.port)}, {"Host": "evil.example"},
                        {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Sec-Fetch-Site": "same-site"}):
            self.assertEqual(self.request("GET", "/api/session", headers=headers)[0], 403)
        self.assertEqual(self.post("/api/stop", {"token": self.token}, {"Origin": "null"})[0], 403)
        self.assertEqual(self.request("POST", "/api/stop", b'{}', {"Content-Type": "application/json"})[0], 403)

    def test_token_required_in_json_not_query_or_cookie(self):
        for token in (None, "", "x" * 64, "é" * 64, 1, True):
            status, _, body = self.post("/api/stop", {"token": token})
            self.assertEqual(status, 403)
            self.assertNotIn("é".encode(), body)
        self.assertEqual(self.post("/api/stop?token=" + self.token, {})[0], 403)
        self.assertEqual(self.post("/api/stop", {}, {"Cookie": "token=" + self.token})[0], 403)

    def test_duplicate_security_headers_rejected(self):
        host = "127.0.0.1:" + str(self.port)
        for duplicate in ("Host: " + host, "Origin: " + self.origin, "Sec-Fetch-Site: same-origin"):
            request = ("GET /api/session HTTP/1.1\r\nHost: " + host + "\r\nOrigin: " + self.origin
                       + "\r\nSec-Fetch-Site: same-origin\r\n" + duplicate + "\r\n\r\n").encode()
            self.assertIn(b" 403 ", self.raw(request))

    def test_ambiguous_and_oversized_framing_rejected(self):
        host = "127.0.0.1:" + str(self.port)
        prefix = ("POST /api/stop HTTP/1.1\r\nHost: " + host + "\r\nOrigin: " + self.origin + "\r\n").encode()
        for extra in (b"Content-Length: 2\r\nContent-Length: 2\r\nContent-Type: application/json",
                      b"Content-Length: 2\r\nContent-Type: application/json\r\nContent-Type: application/json",
                      b"Content-Length: 2\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked",
                      b"Content-Length: 1025\r\nContent-Type: application/json",
                      b"Content-Length: +2\r\nContent-Type: application/json",
                      b"Content-Length: 2\r\nContent-Type: text/plain",
                      b"Content-Length: 99999999999999999999999\r\nContent-Type: application/json"):
            self.assertIn(b" 400 ", self.raw(prefix + extra + b"\r\n\r\n{}"))

    def test_duplicate_keys_depth_invalid_unicode_and_prototype_keys(self):
        for body in (b'{"token":"a","token":"b"}', b'{"__proto__":{}}', b'{"constructor":1}',
                     b'[' * 500 + b'0' + b']' * 500, b'{"x":"\xff"}',
                     b'{"people":NaN}', b'{"token":[]}', b'{"token":{}}'):
            status, _, reply = self.request("POST", "/api/stop", body,
                                             {"Origin": self.origin, "Content-Type": "application/json"})
            self.assertEqual(status, 400)
            self.assertLess(len(reply), 100)

    def test_extra_fields_and_arbitrary_commands_rejected(self):
        for path, data in (("/api/start", {"mode": "observe", "command": "whoami"}),
                           ("/api/stop", {"url": "http://169.254.169.254/"}),
                           ("/api/demo", {"people": 1, "action": "lock_absence"})):
            self.assertEqual(self.post(path, {"token": self.token, **data})[0], 400)
        self.assertEqual(self.post("/api/execute", {"token": self.token})[0], 404)

    def test_paths_are_exact_and_do_not_reflect_input(self):
        for path in ("/../LICENSE", "/%2e%2e/LICENSE", "/runtime/Boss%20Alert.xlsx", "/api/status?private=PLANTED_PRIVATE", "/shared-source.json"):
            status, _, body = self.request("GET", path)
            self.assertEqual(status, 404)
            self.assertNotIn(b"PLANTED_PRIVATE", body)

    def test_unsupported_methods_versions_and_raw_errors_are_fixed_json(self):
        host = "127.0.0.1:" + str(self.port)
        for start in ("TRACE /PLANTED_PRIVATE HTTP/1.1", "PLANTED_PRIVATE / HTTP/1.1", "GET /PLANTED_PRIVATE HTTP/9.9", "GET /PLANTED_PRIVATE", "GET / HTTP/broken"):
            reply = self.raw((start + "\r\nHost: " + host + "\r\n\r\n").encode())
            self.assertTrue(reply.startswith(b"HTTP/1."), reply)
            self.assertNotIn(b"PLANTED_PRIVATE", reply)
            self.assertNotIn(b"Python", reply)
            self.assertIn(b"Content-Security-Policy:", reply)
            self.assertIn(b"Cache-Control: no-store", reply)
            json.loads(reply.split(b"\r\n\r\n", 1)[1])

    def test_total_header_budget_and_request_line_limit(self):
        host = "127.0.0.1:" + str(self.port)
        request = ("GET / HTTP/1.1\r\nHost: " + host + "\r\n").encode() + b"".join(b"X-Data: " + b"a" * 200 + b"\r\n" for _ in range(60)) + b"\r\n"
        self.assertIn(b" 431 ", self.raw(request))
        self.assertIn(b" 414 ", self.raw(b"GET /" + b"x" * 5000 + b" HTTP/1.1\r\n\r\n"))

    def test_header_deadline_bounds_dripping_client(self):
        with mock.patch.object(server, "HEADER_SECONDS", 0.2):
            start = time.monotonic()
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as connection:
                connection.sendall(b"GET / HTTP/1.1\r\nHost: ")
                for _ in range(12):
                    time.sleep(0.03)
                    try:
                        connection.sendall(b"x")
                    except OSError:
                        break
                try:
                    reply = connection.recv(4096)
                except ConnectionError:
                    reply = b""
                self.assertEqual(reply, b"")
            self.assertLess(time.monotonic() - start, 0.8)

    def test_body_deadline_bounds_dripping_client(self):
        with mock.patch.object(server, "BODY_SECONDS", 0.2):
            start = time.monotonic()
            request = ("POST /api/stop HTTP/1.1\r\nHost: 127.0.0.1:" + str(self.port)
                       + "\r\nOrigin: " + self.origin + "\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{").encode()
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as connection:
                connection.sendall(request)
                self.assertEqual(connection.recv(4096), b"")
            self.assertLess(time.monotonic() - start, 0.8)

    def test_refuses_nonloopback_bind(self):
        with self.assertRaises(ValueError):
            server.LocalServer(("0.0.0.0", 0), server.make_handler(self.controller, self.token))

    def test_admission_cap_closes_extra_connection(self):
        sockets = []
        try:
            for _ in range(server.MAX_CONNECTIONS):
                connection = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                connection.sendall(b"GET / HTTP/1.1\r\n")
                sockets.append(connection)
            time.sleep(0.05)
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as extra:
                try:
                    extra.sendall(b"GET / HTTP/1.1\r\n")
                    reply = extra.recv(4096)
                except ConnectionError:
                    reply = b""
                self.assertEqual(reply, b"")
        finally:
            for connection in sockets:
                connection.close()
