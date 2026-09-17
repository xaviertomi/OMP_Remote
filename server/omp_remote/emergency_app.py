from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import subprocess
from typing import Any
from .auth import AuthenticationError, CredentialStore
from .config import Settings
from .db import Database
from .emergency import EmergencyController, EmergencyError
from .version import VERSION_NAME


class EmergencyApplication:
    def __init__(self, settings: Settings, *, require_root: bool = True):
        settings.ensure_runtime_dirs()
        database = Database(settings.db_path)
        database.initialize()
        self.require_root = require_root
        self.allowed_users = tuple(settings.allowed_users)
        self.credentials = CredentialStore(database, settings.allowed_users)
        self.controller = EmergencyController(
            database,
            state_path=settings.state_path,
            incident_path=settings.data_dir / "incidents.jsonl",
            workspace_root=settings.workspace_root,
            quarantine_root=settings.data_dir / "quarantine",
            require_root=require_root,
        )

    def handle(self, method: str, path: str, headers: dict[str, str]) -> tuple[int, object, dict[str, str]]:
        try:
            self.credentials.authenticate_emergency(headers.get("authorization"))
            if method == "GET" and path == "/emergency/status":
                return HTTPStatus.OK, self.controller.status(), {}
            if method == "POST" and path == "/emergency/kill":
                stop_main = self._stop_omp_remote_services if self.require_root else None
                return HTTPStatus.OK, self.controller.kill(stop_main=stop_main), {}
            return HTTPStatus.NOT_FOUND, {"error": "not found"}, {}
        except AuthenticationError as exc:
            return HTTPStatus.UNAUTHORIZED, {"error": str(exc)}, {"WWW-Authenticate": "Bearer"}
        except EmergencyError as exc:
            return HTTPStatus.FORBIDDEN, {"error": str(exc)}, {}


    def _stop_omp_remote_services(self) -> None:
        units = [f"omp-remote-worker@{owner}.service" for owner in self.allowed_users]
        units.append("omp-remote-api.service")
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "stop", *units],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EmergencyError("OMP Remote services could not be stopped") from exc
        if result.returncode != 0:
            raise EmergencyError("OMP Remote services could not be stopped")

class EmergencyRequestHandler(BaseHTTPRequestHandler):
    server_version = f"OMP-Remote-Emergency/{VERSION_NAME}"

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        response = self.server.application.handle(  # type: ignore[attr-defined]
            method,
            self.path.split("?", 1)[0],
            {key.lower(): value for key, value in self.headers.items()},
        )
        status, payload, headers = response
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


class EmergencyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, settings: Settings, *, require_root: bool = True):
        self.application = EmergencyApplication(settings, require_root=require_root)
        host = settings.bind_host
        port = int(os.environ.get("OMP_REMOTE_EMERGENCY_PORT", "18098"))
        super().__init__((host, port), EmergencyRequestHandler)


def serve_emergency(settings: Settings) -> None:
    with EmergencyServer(settings) as server:
        server.serve_forever()
