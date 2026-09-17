from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import re
import logging
import threading
import sqlite3
from typing import Any
from urllib.parse import parse_qs, urlsplit
import uuid

from .auth import AuthenticationError, CredentialStore, Identity
from .config import Settings
from .db import Database
from .files import FileStore, FileStoreError
from .projects import ProjectRegistry
from .updates import UpdateStore, UpdateStoreError
from .version import VERSION_CODE, VERSION_NAME


RUNNERS: tuple[dict[str, str], ...] = (
    {"id": "omp", "name": "Oh My Pi", "status": "configured"},
)
LOGGER = logging.getLogger(__name__)
_JOB_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_CONVERSATION_ID = _JOB_ID
_ALLOWED_JOB_FIELDS = frozenset({"runner_id", "project_id", "prompt", "timeout_seconds"})
_ALLOWED_CONVERSATION_FIELDS = frozenset({"prompt"})


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    payload: object
    headers: dict[str, str] | None = None


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Application:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_runtime_dirs()
        self.database = Database(settings.db_path)
        self.database.initialize()
        self.credentials = CredentialStore(self.database, settings.allowed_users)
        self.projects = ProjectRegistry.from_environment(
            settings.allowed_users,
            {"OMP_REMOTE_PROJECTS_JSON": settings.projects_json},
        )
        self.files = FileStore(
            self.database,
            workspace_root=settings.workspace_root,
            max_file_bytes=settings.max_file_bytes,
            max_job_bytes=settings.max_job_bytes,
            max_total_bytes=settings.max_total_bytes,
        )
        self.updates = UpdateStore(
            settings.apk_path,
            version_code=settings.apk_version_code,
            version_name=settings.apk_version_name,
        )
        self._conversation_locks: dict[str, threading.Lock] = {}
        self._conversation_locks_guard = threading.Lock()

    def handle(
        self,
        method: str,
        target: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Response:
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        route = urlsplit(target)
        try:
            if method == "GET" and route.path == "/download/omp-remote.apk":
                try:
                    return Response(
                        HTTPStatus.OK,
                        self.updates.read(),
                        {
                            "Content-Type": "application/vnd.android.package-archive",
                            "Content-Disposition": f'attachment; filename="{self.updates.filename}"',
                        },
                    )
                except UpdateStoreError:
                    return Response(HTTPStatus.NOT_FOUND, {"error": "download unavailable"})
            if method == "GET" and route.path == "/api/v1/health":
                return self._health()
            if self._is_disabled():
                return Response(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "service disabled"})
            identity = self.credentials.authenticate_device(headers.get("authorization"))
            return self._dispatch_authenticated(method, route.path, route.query, headers, body, identity)
        except AuthenticationError as exc:
            return Response(
                HTTPStatus.UNAUTHORIZED,
                {"error": str(exc)},
                {"WWW-Authenticate": "Bearer"},
            )
        except RequestError as exc:
            return Response(exc.status, {"error": exc.message})
        except ValueError as exc:
            return Response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except OSError as exc:
            LOGGER.error("runtime operation failed: %s", exc)
            return Response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "runtime unavailable"})
        except sqlite3.Error as exc:
            LOGGER.error("database operation failed: %s", exc)
            return Response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "database unavailable"})

    def _dispatch_authenticated(
        self,
        method: str,
        path: str,
        query: str,
        headers: dict[str, str],
        body: bytes,
        identity: Identity,
    ) -> Response:
        if method == "GET" and path == "/api/v1/app/update":
            try:
                return Response(HTTPStatus.OK, {"update": self.updates.metadata()})
            except UpdateStoreError as exc:
                raise RequestError(HTTPStatus.NOT_FOUND, "update unavailable") from exc
        if method == "GET" and path == "/api/v1/app/update/download":
            try:
                return Response(
                    HTTPStatus.OK,
                    self.updates.read(),
                    {
                        "Content-Type": "application/vnd.android.package-archive",
                        "Content-Disposition": f'attachment; filename="{self.updates.filename}"',
                    },
                )
            except UpdateStoreError as exc:
                raise RequestError(HTTPStatus.NOT_FOUND, "update unavailable") from exc
        if method == "GET" and path == "/api/v1/projects":
            return Response(HTTPStatus.OK, {"projects": self.projects.list_for(identity.owner_user)})
        if method == "GET" and path == "/api/v1/conversations":
            return self._list_conversations(query, identity)
        if method == "POST" and path == "/api/v1/conversations":
            return self._create_conversation(body, identity)
        if method == "GET" and path == "/api/v1/jobs":
            return self._list_jobs(query, identity)
        if method == "POST" and path == "/api/v1/jobs":
            return self._create_job(body, identity)

        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[:3] == ["api", "v1", "conversations"]:
            conversation_id = parts[3]
            if not _CONVERSATION_ID.fullmatch(conversation_id):
                raise RequestError(HTTPStatus.NOT_FOUND, "conversation not found")
            if method == "GET":
                return self._get_conversation(conversation_id, identity)
        if len(parts) == 5 and parts[:3] == ["api", "v1", "conversations"] and parts[4] == "messages":
            conversation_id = parts[3]
            if not _CONVERSATION_ID.fullmatch(conversation_id):
                raise RequestError(HTTPStatus.NOT_FOUND, "conversation not found")
            if method == "POST":
                return self._add_conversation_message(conversation_id, body, identity)

        if len(parts) == 4 and parts[:3] == ["api", "v1", "jobs"]:
            job_id = parts[3]
            if not _JOB_ID.fullmatch(job_id):
                raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
            if method == "GET":
                return self._get_job(job_id, identity)
        if len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "logs":
            job_id = parts[3]
            if not _JOB_ID.fullmatch(job_id):
                raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
            if method == "GET":
                return self._logs(job_id, identity)
        if len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "files":
            job_id = parts[3]
            if not _JOB_ID.fullmatch(job_id):
                raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
            if method == "GET":
                return self._list_files(job_id, identity)
            if method == "POST":
                return self._upload_file(job_id, body, identity)
        if len(parts) == 6 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "files":
            job_id, file_id = parts[3], parts[5]
            if not _JOB_ID.fullmatch(job_id) or not _JOB_ID.fullmatch(file_id):
                raise RequestError(HTTPStatus.NOT_FOUND, "file not found")
            if method == "GET":
                return self._download_file(job_id, file_id, identity)
        if len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "cancel":
            job_id = parts[3]
            if not _JOB_ID.fullmatch(job_id):
                raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
            if method == "POST":
                return self._cancel_job(job_id, identity)
        raise RequestError(HTTPStatus.NOT_FOUND, "not found")

    def _health(self) -> Response:
        return Response(
            HTTPStatus.OK,
            {
                "status": "disabled" if self._is_disabled() else "ok",
                "disabled": self._is_disabled(),
                "version": VERSION_NAME,
                "version_code": VERSION_CODE,
            },
        )

    def _list_jobs(self, query: str, identity: Identity) -> Response:
        values = parse_qs(query, keep_blank_values=False)
        limit = _query_int(values, "limit", 50, minimum=1, maximum=100)
        offset = _query_int(values, "offset", 0, minimum=0, maximum=1_000_000)
        return Response(
            HTTPStatus.OK,
            {"jobs": [self._public_job(job) for job in self.database.list_owned_jobs(identity.owner_user, limit=limit, offset=offset)]},
        )
    def _list_conversations(self, query: str, identity: Identity) -> Response:
        values = parse_qs(query, keep_blank_values=False)
        limit = _query_int(values, "limit", 50, minimum=1, maximum=100)
        offset = _query_int(values, "offset", 0, minimum=0, maximum=1_000_000)
        conversations = self.database.list_owned_conversations(
            identity.owner_user,
            limit=limit,
            offset=offset,
        )
        return Response(
            HTTPStatus.OK,
            {
                "conversations": [
                    self._public_conversation(conversation) for conversation in conversations
                ]
            },
        )

    def _get_conversation(self, conversation_id: str, identity: Identity) -> Response:
        return Response(
            HTTPStatus.OK,
            {"conversation": self._conversation_payload(conversation_id, identity)},
        )

    def _create_conversation(self, body: bytes, identity: Identity) -> Response:
        prompt = self._conversation_prompt(body)
        self._ensure_queue_capacity()
        conversation_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        workspace = self._workspace_for(job_id)
        _create_workspace(workspace)
        try:
            created = self.database.create_conversation_job(
                conversation_id=conversation_id,
                job_id=job_id,
                owner_user=identity.owner_user,
                device_id=identity.device_id,
                runner_id="omp",
                project_id=None,
                prompt=prompt,
                execution_prompt=self._build_execution_prompt([], prompt),
                workspace_path=str(workspace),
                timeout_seconds=3600,
            )
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise
        message = self._public_message(created["message"], identity.owner_user)
        conversation = self._conversation_payload(conversation_id, identity)
        return Response(
            HTTPStatus.ACCEPTED,
            {
                "conversation": conversation,
                "message": message,
                "job": self._public_job(created["job"]),
            },
        )

    def _add_conversation_message(
        self,
        conversation_id: str,
        body: bytes,
        identity: Identity,
    ) -> Response:
        prompt = self._conversation_prompt(body)
        lock = self._conversation_lock(conversation_id)
        with lock:
            if self.database.get_owned_conversation(conversation_id, identity.owner_user) is None:
                raise RequestError(HTTPStatus.NOT_FOUND, "conversation not found")
            history = self._conversation_history(conversation_id, identity.owner_user)
            execution_prompt = self._build_execution_prompt(history, prompt)
            self._ensure_queue_capacity()
            job_id = str(uuid.uuid4())
            workspace = self._workspace_for(job_id)
            _create_workspace(workspace)
            try:
                created = self.database.append_conversation_job(
                    conversation_id=conversation_id,
                    job_id=job_id,
                    owner_user=identity.owner_user,
                    device_id=identity.device_id,
                    runner_id="omp",
                    project_id=None,
                    prompt=prompt,
                    execution_prompt=execution_prompt,
                    workspace_path=str(workspace),
                    timeout_seconds=3600,
                )
            except Exception:
                shutil.rmtree(workspace, ignore_errors=True)
                raise
            if created is None:
                shutil.rmtree(workspace, ignore_errors=True)
                raise RequestError(HTTPStatus.NOT_FOUND, "conversation not found")
            message = self._public_message(created["message"], identity.owner_user)
            conversation = self._conversation_payload(conversation_id, identity)
            return Response(
                HTTPStatus.ACCEPTED,
                {
                    "conversation": conversation,
                    "message": message,
                    "job": self._public_job(created["job"]),
                },
            )

    def _conversation_lock(self, conversation_id: str) -> threading.Lock:
        with self._conversation_locks_guard:
            return self._conversation_locks.setdefault(conversation_id, threading.Lock())

    def _conversation_payload(
        self,
        conversation_id: str,
        identity: Identity,
    ) -> dict[str, object]:
        conversation = self.database.get_owned_conversation(
            conversation_id,
            identity.owner_user,
        )
        if conversation is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "conversation not found")
        visible = self._public_conversation(conversation)
        visible["messages"] = [
            self._public_message(message, identity.owner_user)
            for message in self.database.list_owned_messages(
                conversation_id,
                identity.owner_user,
            )
        ]
        return visible

    @staticmethod
    def _public_conversation(conversation: dict[str, object]) -> dict[str, object]:
        return dict(conversation)

    def _public_message(
        self,
        message: dict[str, object],
        owner_user: str,
    ) -> dict[str, object]:
        logs = self._message_logs(message, owner_user)
        return {
            "id": message["id"],
            "conversation_id": message["conversation_id"],
            "owner_user": message["owner_user"],
            "prompt": message["prompt"],
            "job_id": message["job_id"],
            "state": message["state"],
            "error": message.get("error"),
            "stdout": logs["stdout"],
            "stderr": logs["stderr"],
            "created_at": message["created_at"],
        }

    def _conversation_history(
        self,
        conversation_id: str,
        owner_user: str,
    ) -> list[dict[str, object]]:
        history: list[dict[str, object]] = []
        for message in self.database.list_owned_messages(conversation_id, owner_user):
            history.append(
                {
                    "prompt": message["prompt"],
                    **self._message_logs(message, owner_user),
                }
            )
        return history

    def _message_logs(
        self,
        message: dict[str, object],
        owner_user: str,
    ) -> dict[str, str]:
        workspace_path = message.get("workspace_path")
        if not workspace_path:
            return {"stdout": "", "stderr": ""}
        try:
            return self.files.read_logs(
                job=message,
                owner_user=owner_user,
            )
        except FileStoreError:
            return {"stdout": "", "stderr": ""}

    def _conversation_prompt(self, body: bytes) -> str:
        payload = _json_object(body, self.settings.max_body_bytes)
        if set(payload) != _ALLOWED_CONVERSATION_FIELDS:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "conversation request must contain only prompt",
            )
        prompt = payload["prompt"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise RequestError(HTTPStatus.BAD_REQUEST, "prompt is required")
        if len(prompt.encode("utf-8")) > self.settings.max_prompt_bytes:
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "prompt is too long")
        return prompt

    def _ensure_queue_capacity(self) -> None:
        if self.database.queued_count() >= self.settings.max_queue_length:
            raise RequestError(HTTPStatus.TOO_MANY_REQUESTS, "queue is full")

    def _build_execution_prompt(
        self,
        history: list[dict[str, object]],
        current_prompt: str,
    ) -> str:
        if not history:
            return current_prompt
        maximum = self.settings.max_prompt_bytes
        current = f"Current user prompt:\n{current_prompt}"
        header = (
            "Server-maintained conversation context. Previous OMP sessions are not "
            "persistent; use these prior prompts and available stdout as the logical "
            "conversation history.\n\n"
        )
        available = maximum - len((header + current).encode("utf-8")) - 2
        if available <= 0:
            return current_prompt
        selected: list[str] = []
        for index in range(len(history) - 1, -1, -1):
            item = history[index]
            prompt = str(item.get("prompt", ""))
            stdout = item.get("stdout")
            block = f"Turn {index + 1} user prompt:\n{prompt}"
            if isinstance(stdout, str) and stdout:
                block += f"\nTurn {index + 1} OMP stdout:\n{stdout}"
            block += "\n"
            block_bytes = len(block.encode("utf-8"))
            if block_bytes <= available:
                selected.append(block)
                available -= block_bytes
                continue
            clipped = _clip_utf8(block, available)
            if clipped:
                selected.append(clipped)
            break
        if not selected:
            return current_prompt
        selected.reverse()
        result = header + "\n".join(selected) + "\n" + current
        if len(result.encode("utf-8")) > maximum:
            return current_prompt
        return result


    def _get_job(self, job_id: str, identity: Identity) -> Response:
        job = self.database.get_owned_job(job_id, identity.owner_user)
        if job is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
        return Response(HTTPStatus.OK, {"job": self._public_job(job)})

    def _logs(self, job_id: str, identity: Identity) -> Response:
        job = self.database.get_owned_job(job_id, identity.owner_user)
        if job is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
        try:
            logs = self.files.read_logs(job=job, owner_user=identity.owner_user)
        except FileStoreError as exc:
            raise RequestError(HTTPStatus.NOT_FOUND, "logs not found") from exc
        return Response(HTTPStatus.OK, logs)

    @staticmethod
    def _public_job(job: dict[str, object]) -> dict[str, object]:
        visible = dict(job)
        for field in ("workspace_path", "process_id", "process_group_id", "execution_prompt", "cancel_requested"):
            visible.pop(field, None)
        return visible

    def _list_files(self, job_id: str, identity: Identity) -> Response:
        job = self.database.get_owned_job(job_id, identity.owner_user)
        if job is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
        try:
            self.files.discover_outputs(job=job, owner_user=identity.owner_user)
        except FileStoreError as exc:
            raise RequestError(HTTPStatus.CONFLICT, "outputs unavailable") from exc
        return Response(
            HTTPStatus.OK,
            {"files": self.files.list_files(job_id=job_id, owner_user=identity.owner_user)},
        )

    def _upload_file(self, job_id: str, body: bytes, identity: Identity) -> Response:
        job = self.database.get_owned_job(job_id, identity.owner_user)
        if job is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
        if len(self.files.list_files(job_id=job_id, owner_user=identity.owner_user)) >= self.settings.max_files_per_job:
            raise RequestError(HTTPStatus.TOO_MANY_REQUESTS, "file count limit reached")
        payload = _json_object(body, self.settings.max_body_bytes)
        if set(payload) != {"name", "content_base64"}:
            raise RequestError(HTTPStatus.BAD_REQUEST, "file request must contain name and content_base64")
        name = payload["name"]
        encoded = payload["content_base64"]
        if not isinstance(name, str) or not isinstance(encoded, str):
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid file request")
        if len(encoded) > self.settings.max_file_bytes * 2:
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "file is too large")
        try:
            content = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "content_base64 is invalid") from exc
        try:
            file = self.files.upload_input(
                job=job,
                owner_user=identity.owner_user,
                name=name,
                content=content,
            )
        except FileStoreError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        return Response(HTTPStatus.CREATED, {"file": file})

    def _download_file(self, job_id: str, file_id: str, identity: Identity) -> Response:
        job = self.database.get_owned_job(job_id, identity.owner_user)
        file = self.database.get_owned_file(file_id, job_id, identity.owner_user)
        if job is None or file is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "file not found")
        try:
            content = self.files.download_file(job=job, owner_user=identity.owner_user, file=file)
        except FileStoreError as exc:
            raise RequestError(HTTPStatus.NOT_FOUND, "file not found") from exc
        filename = Path(str(file["relative_path"])).name.replace('"', "")
        return Response(
            HTTPStatus.OK,
            content,
            {
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    def _create_job(self, body: bytes, identity: Identity) -> Response:
        payload = _json_object(body, self.settings.max_body_bytes)
        unknown = set(payload) - _ALLOWED_JOB_FIELDS
        if unknown:
            raise RequestError(HTTPStatus.BAD_REQUEST, "unsupported job field")
        runner_id = payload.get("runner_id")
        if runner_id != "omp":
            raise RequestError(HTTPStatus.BAD_REQUEST, "unknown runner_id")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RequestError(HTTPStatus.BAD_REQUEST, "prompt is required")
        if len(prompt.encode("utf-8")) > self.settings.max_prompt_bytes:
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "prompt is too long")
        project_id = payload.get("project_id")
        if project_id is not None:
            if not isinstance(project_id, str) or not project_id:
                raise RequestError(HTTPStatus.BAD_REQUEST, "invalid project_id")
            if self.projects.get_for(project_id, identity.owner_user) is None:
                raise RequestError(HTTPStatus.BAD_REQUEST, "unknown project_id")
        timeout_seconds = payload.get("timeout_seconds", 3600)
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
            raise RequestError(HTTPStatus.BAD_REQUEST, "timeout_seconds must be an integer")
        if timeout_seconds < 1 or timeout_seconds > 86_400:
            raise RequestError(HTTPStatus.BAD_REQUEST, "timeout_seconds is outside the allowed range")
        if self.database.queued_count() >= self.settings.max_queue_length:
            raise RequestError(HTTPStatus.TOO_MANY_REQUESTS, "queue is full")

        job_id = str(uuid.uuid4())
        workspace = self._workspace_for(job_id)
        _create_workspace(workspace)
        try:
            job = self.database.create_job(
                job_id=job_id,
                owner_user=identity.owner_user,
                device_id=identity.device_id,
                runner_id=runner_id,
                project_id=project_id,
                prompt=prompt,
                workspace_path=str(workspace),
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise
        return Response(HTTPStatus.ACCEPTED, {"job": self._public_job(job)})

    def _cancel_job(self, job_id: str, identity: Identity) -> Response:
        before = self.database.get_owned_job(job_id, identity.owner_user)
        if before is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
        if before["state"] not in {"queued", "running", "cancelled"}:
            raise RequestError(HTTPStatus.CONFLICT, "job is not cancellable in its current state")
        job = self.database.cancel_owned_job(job_id, identity.owner_user)
        return Response(HTTPStatus.OK, {"job": self._public_job(job)})

    def _workspace_for(self, job_id: str) -> Path:
        root = self.settings.workspace_root.resolve()
        workspace = (root / job_id).resolve()
        try:
            workspace.relative_to(root)
        except ValueError as exc:
            raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid workspace root") from exc
        return workspace

    def _is_disabled(self) -> bool:
        try:
            state = self.settings.state_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return state != "ENABLED"


class RequestHandler(BaseHTTPRequestHandler):
    server_version = f"OMP-Remote/{VERSION_NAME}"

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0:
            response = Response(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
        elif length > self.server.application.settings.max_body_bytes:  # type: ignore[attr-defined]
            response = Response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
        else:
            body = self.rfile.read(length) if length else b""
            response = self.server.application.handle(  # type: ignore[attr-defined]
                method,
                self.path,
                {key.lower(): value for key, value in self.headers.items()},
                body,
            )
        response_headers = response.headers or {}
        if isinstance(response.payload, bytes):
            encoded = response.payload
            content_type = response_headers.get("Content-Type", "application/octet-stream")
        else:
            encoded = json.dumps(
                response.payload, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            content_type = response_headers.get("Content-Type", "application/json; charset=utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        for key, value in response_headers.items():
            if key.lower() != "content-type":
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


class ApplicationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, settings: Settings):
        self.application = Application(settings)
        if self.application._is_disabled():
            raise RuntimeError("OMP Remote is disabled")
        super().__init__((settings.bind_host, settings.bind_port), RequestHandler)


def serve(settings: Settings) -> None:
    with ApplicationServer(settings) as server:
        server.serve_forever()


def _json_object(body: bytes, max_bytes: int) -> dict[str, object]:
    if len(body) > max_bytes:
        raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError(HTTPStatus.BAD_REQUEST, "request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise RequestError(HTTPStatus.BAD_REQUEST, "request body must be a JSON object")
    return payload


def _query_int(values: dict[str, list[str]], key: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = values.get(key, [str(default)])
    if len(raw) != 1:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"invalid {key}")
    try:
        value = int(raw[0])
    except ValueError as exc:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"invalid {key}") from exc
    if not minimum <= value <= maximum:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"invalid {key}")
    return value


def _clip_utf8(value: str, maximum_bytes: int) -> str:
    if maximum_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore")

def _create_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=False, mode=0o770)
    for name in ("input", "workspace", "output", "logs", "metadata"):
        child = workspace / name
        child.mkdir(mode=0o770)
