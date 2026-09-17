from __future__ import annotations

from http import client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from typing import Any
from .version import VERSION_NAME


class GatewayRequestHandler(BaseHTTPRequestHandler):
    server_version = f"OMP-Remote-Gateway/{VERSION_NAME}"

    def do_GET(self) -> None:  # noqa: N802
        self._proxy("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._proxy("POST")

    def _proxy(self, method: str) -> None:
        target = self._target()
        if target is None:
            self._json_error(404, "not found")
            return
        host, port = target
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_error(400, "invalid content length")
            return
        if length < 0 or length > self.server.max_body_bytes:  # type: ignore[attr-defined]
            self._json_error(413, "request too large")
            return
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        headers["Host"] = f"127.0.0.1:{port}"
        if body is not None:
            headers["Content-Length"] = str(len(body))
        try:
            upstream = client.HTTPConnection("127.0.0.1", port, timeout=30)
            upstream.request(method, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            payload = response.read()
        except OSError:
            self._json_error(502, "upstream unavailable")
            return
        finally:
            try:
                upstream.close()
            except UnboundLocalError:
                pass
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _target(self) -> tuple[str, int] | None:
        path = self.path.split("?", 1)[0]
        if path == "/api" or path.startswith("/api/") or path == "/download/omp-remote.apk":
            return "127.0.0.1", self.server.api_port  # type: ignore[attr-defined]
        if path == "/emergency" or path.startswith("/emergency/"):
            return "127.0.0.1", self.server.emergency_port  # type: ignore[attr-defined]
        return None

    def _json_error(self, status: int, message: str) -> None:
        payload = (f'{{"error":"{message}"}}').encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.api_port = int(os.environ.get("OMP_REMOTE_API_PORT", "8099"))
        self.emergency_port = int(os.environ.get("OMP_REMOTE_EMERGENCY_PORT", "18098"))
        self.max_body_bytes = int(os.environ.get("OMP_REMOTE_MAX_BODY_BYTES", str(10 * 1024 * 1024)))
        port = int(os.environ.get("OMP_REMOTE_GATEWAY_PORT", "18080"))
        super().__init__(("127.0.0.1", port), GatewayRequestHandler)


def serve_gateway() -> None:
    with GatewayServer() as server:
        server.serve_forever()
