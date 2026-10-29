from __future__ import annotations

import argparse
import hmac
import http.client
import json
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .camera import run_camera
from .controller import Controller, GuardError
from .desktop import DesktopActions, plain_path

ROOT = Path(__file__).absolute().parent.parent
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml")}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
MAX_CONNECTIONS = 12
HEADER_SECONDS = 4
BODY_SECONDS = 4
MAX_HEADERS = 8192
MAX_BODY = 1024


class Deadline:
    def __init__(self, connection, seconds):
        self.connection = connection
        self.lock = threading.Lock()
        self.finished = False
        self.timer = threading.Timer(seconds, self.expire)
        self.timer.daemon = True
        self.timer.start()

    def expire(self):
        with self.lock:
            if not self.finished:
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.finished = True

    def cancel(self):
        with self.lock:
            self.finished = True
            self.timer.cancel()


class HeaderReader:
    def __init__(self, stream):
        self.stream, self.remaining = stream, MAX_HEADERS

    def readline(self, limit=-1):
        line = self.stream.readline(min(limit, self.remaining + 1) if limit >= 0 else self.remaining + 1)
        self.remaining -= len(line)
        if self.remaining < 0:
            raise http.client.LineTooLong("header")
        return line


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler):
        if address[0] != "127.0.0.1":
            raise ValueError("Only the loopback address is supported")
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        pass


def load_assets(root=ROOT):
    assets = {}
    for route, (name, kind) in ASSETS.items():
        path = plain_path(Path(root) / "web" / name)
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            raise OSError("Dashboard assets are unavailable")
        assets[route] = (path.read_bytes(), kind)
    return assets


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result or key in ("__proto__", "prototype", "constructor"):
                raise ValueError("Invalid keys")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid number")))
        if type(value) is not dict or any(type(item) in (dict, list) for item in value.values()):
            raise ValueError("Expected a flat object")
        return value
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        raise ValueError("Invalid request") from None


def make_handler(controller, token, assets=None):
    assets = load_assets() if assets is None else assets

    class Handler(BaseHTTPRequestHandler):
        def version_string(self):
            return "PresenceGuard"

        def log_message(self, *args):
            pass

        def handle_one_request(self):
            deadline = Deadline(self.connection, HEADER_SECONDS)
            try:
                self.raw_requestline = self.rfile.readline(4097)
                if len(self.raw_requestline) > 4096:
                    self.requestline = self.command = ""
                    self.request_version = "HTTP/1.0"
                    self.send_error(414)
                    return
                if not self.raw_requestline:
                    self.close_connection = True
                    return
                if not self.parse_request():
                    return
                deadline.cancel()
                self.close_connection = True
                if self.command not in ("GET", "POST"):
                    self.send_error(405)
                else:
                    getattr(self, "do_" + self.command)()
                self.wfile.flush()
            except (OSError, TimeoutError):
                self.close_connection = True
            finally:
                deadline.cancel()

        def parse_request(self):
            stream = self.rfile
            self.rfile = HeaderReader(stream)
            try:
                accepted = super().parse_request()
            finally:
                self.rfile = stream
            if accepted and self.request_version not in ("HTTP/1.0", "HTTP/1.1"):
                self.send_error(400)
                return False
            return accepted

        def send_error(self, code, message=None, explain=None):
            status = 405 if code == 501 else code
            public = {400: "Invalid request", 403: "Request rejected", 404: "Not found",
                      405: "Method not allowed", 408: "Request timed out", 414: "Request too large",
                      431: "Request headers too large", 505: "Invalid request"}.get(status, "Request failed")
            if getattr(self, "request_version", "HTTP/0.9") not in ("HTTP/1.0", "HTTP/1.1"):
                self.request_version = "HTTP/1.0"
            self.close_connection = True
            self.send_json(status, {"error": public})

        def _trusted(self, mutation=False):
            for header in ("Host", "Origin", "Sec-Fetch-Site"):
                if len(self.headers.get_all(header, [])) > 1:
                    return False
            expected = "127.0.0.1:" + str(self.server.server_port)
            origin = self.headers.get("Origin")
            site = self.headers.get("Sec-Fetch-Site")
            return (self.headers.get("Host") == expected
                    and (origin == "http://" + expected if mutation else origin in (None, "http://" + expected))
                    and site in (None, "none", "same-origin"))

        def _framing(self, post=False):
            if self.headers.get_all("Transfer-Encoding", []):
                return False
            lengths = self.headers.get_all("Content-Length", [])
            if not post:
                return not lengths or lengths == ["0"]
            if len(lengths) != 1 or self.headers.get_all("Content-Type", []) != ["application/json"]:
                return False
            return lengths[0].isascii() and lengths[0].isdecimal() and len(lengths[0]) <= 4 and 0 < int(lengths[0]) <= MAX_BODY

        def send_data(self, status, body, kind="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.end_headers()
            if getattr(self, "command", "") != "HEAD":
                self.wfile.write(body)

        def send_json(self, status, data):
            self.send_data(status, json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode())

        def do_GET(self):
            if not self._trusted():
                self.send_error(403)
            elif not self._framing():
                self.send_error(400)
            elif self.path == "/api/session":
                self.send_json(200, {"token": token})
            elif self.path == "/api/status":
                self.send_json(200, controller.snapshot())
            elif self.path in assets:
                body, kind = assets[self.path]
                self.send_data(200, body, kind)
            else:
                self.send_error(404)

        def do_POST(self):
            if not self._trusted(mutation=True):
                self.send_error(403)
                return
            if not self._framing(post=True):
                self.send_error(400)
                return
            deadline = Deadline(self.connection, BODY_SECONDS)
            try:
                raw = self.rfile.read(int(self.headers["Content-Length"]))
            finally:
                deadline.cancel()
            try:
                data = parse_json(raw)
                supplied = data.get("token")
                if type(supplied) is not str or not supplied.isascii() or len(supplied) != len(token) or not hmac.compare_digest(supplied, token):
                    self.send_error(403)
                    return
                if self.path == "/api/start" and set(data) == {"token", "mode"}:
                    result = controller.start(data["mode"])
                elif self.path == "/api/stop" and set(data) == {"token"}:
                    result = controller.stop()
                elif self.path == "/api/demo" and set(data) == {"token", "people"}:
                    result = controller.set_demo(data["people"])
                elif self.path not in ("/api/start", "/api/stop", "/api/demo"):
                    self.send_error(404)
                    return
                else:
                    raise ValueError("Unexpected fields")
            except GuardError as error:
                self.send_json(409, {"error": str(error)})
                return
            except (ValueError, TypeError, RecursionError, OverflowError):
                self.send_error(400)
                return
            self.send_json(200, result)

    return Handler


def build_parser():
    parser = argparse.ArgumentParser(description="Presence Guard. Starts inactive in synthetic demo mode.")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--demo", action="store_true", help="Use synthetic people counts (the default)")
    inputs.add_argument("--camera", action="store_true", help="Allow a local camera after Start")
    parser.add_argument("--camera-index", type=int, choices=range(9), default=0, metavar="0..8")
    parser.add_argument("--allow-lock", action="store_true", help="Allow local UI arming of Windows lock")
    parser.add_argument("--allow-spreadsheet", action="store_true", help="Allow local UI arming of the synthetic workbook")
    parser.add_argument("--port", type=int, default=8790, help="Loopback port; 0 selects an available port")
    parser.add_argument("--open", action="store_true", help="Open the local dashboard in the default browser")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535")
    if (args.allow_lock or args.allow_spreadsheet) and (not args.camera or sys.platform != "win32"):
        parser.error("Desktop permissions require --camera on Windows")
    actions = DesktopActions(ROOT, demo=not args.camera, allow_lock=args.allow_lock, allow_sheet=args.allow_spreadsheet)
    runner = lambda controller, session, stop: run_camera(controller, session, stop, device=args.camera_index)
    controller = Controller(camera=args.camera, actions=actions, camera_runner=runner)
    stopped = threading.Event()
    try:
        server = LocalServer(("127.0.0.1", args.port), make_handler(controller, secrets.token_hex(32)))
    except OSError:
        print("Presence Guard could not start. Check the project assets and choose an available local port.", file=sys.stderr)
        return 1

    def pulse():
        while not stopped.wait(0.1):
            controller.pulse()

    threading.Thread(target=pulse, daemon=True).start()
    url = "http://127.0.0.1:" + str(server.server_port)
    print("Presence Guard: " + url, flush=True)
    print("Inactive. Choose a mode and press Start. Close this terminal to exit.", flush=True)
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            print("Open the local address above in your browser.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        controller.close()
        server.server_close()
    return 0
