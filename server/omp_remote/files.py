from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from .db import Database


class FileStoreError(Exception):
    """Raised when a file request violates the workspace boundary."""


class FileStore:
    def __init__(
        self,
        database: Database,
        *,
        workspace_root: Path,
        max_file_bytes: int = 5 * 1024 * 1024,
        max_job_bytes: int = 100 * 1024 * 1024,
        max_total_bytes: int = 10 * 1024 * 1024 * 1024,
    ):
        self.database = database
        self.workspace_root = workspace_root.resolve()
        self.max_file_bytes = max_file_bytes
        self.max_job_bytes = max_job_bytes
        self.max_total_bytes = max_total_bytes

    def upload_input(
        self,
        *,
        job: dict[str, object],
        owner_user: str,
        name: str,
        content: bytes,
    ) -> dict[str, object]:
        self._assert_job_owner(job, owner_user)
        safe_name = _safe_name(name)
        self._check_capacity(job, len(content))
        if len(content) > self.max_file_bytes:
            raise FileStoreError("file is too large")
        input_dir = self._job_subdir(job, "input")
        target = input_dir / safe_name
        self._assert_under(input_dir, target)
        if target.exists() or target.is_symlink():
            raise FileStoreError("file already exists")
        digest = hashlib.sha256(content).hexdigest()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags, 0o600)
        except OSError as exc:
            raise FileStoreError("file could not be created") from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise FileStoreError("file could not be written") from exc
        try:
            return self.database.register_file(
                job_id=str(job["id"]),
                owner_user=owner_user,
                kind="input",
                relative_path=f"input/{safe_name}",
                size_bytes=len(content),
                sha256=digest,
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def list_files(self, *, job_id: str, owner_user: str) -> list[dict[str, object]]:
        return self.database.list_owned_files(job_id, owner_user)

    def discover_outputs(
        self, *, job: dict[str, object], owner_user: str
    ) -> list[dict[str, object]]:
        self._assert_job_owner(job, owner_user)
        workspace = Path(str(job["workspace_path"])).resolve()
        output_dir = self._job_subdir(job, "output")
        writable_dir = self._job_subdir(job, "workspace")
        existing = {
            str(file["relative_path"])
            for file in self.database.list_owned_files(str(job["id"]), owner_user)
        }
        job_bytes = sum(
            int(file["size_bytes"])
            for file in self.database.list_owned_files(str(job["id"]), owner_user)
        )
        discovered: list[dict[str, object]] = []
        for scan_root in (output_dir, writable_dir):
            for root, directories, filenames in os.walk(scan_root, followlinks=False):
                root_path = Path(root)
                for directory in directories:
                    if (root_path / directory).is_symlink():
                        raise FileStoreError("output contains a symlink directory")
                for filename in filenames:
                    target = root_path / filename
                    if target.is_symlink():
                        raise FileStoreError("output contains a symlink")
                    try:
                        details = target.lstat()
                    except OSError as exc:
                        raise FileStoreError("output could not be inspected") from exc
                    if not stat.S_ISREG(details.st_mode):
                        raise FileStoreError("output contains a non-regular file")
                    if details.st_size > self.max_file_bytes:
                        raise FileStoreError("output file is too large")
                    relative = _safe_relative_path(workspace, target)
                    if relative in existing:
                        continue
                    job_bytes += details.st_size
                    if job_bytes > self.max_job_bytes:
                        raise FileStoreError("job disk quota exceeded")
                    if self.database.total_registered_bytes() + details.st_size > self.max_total_bytes:
                        raise FileStoreError("global disk quota exceeded")
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    record = self.database.register_file(
                        job_id=str(job["id"]),
                        owner_user=owner_user,
                        kind="output",
                        relative_path=relative,
                        size_bytes=details.st_size,
                        sha256=digest,
                    )
                    existing.add(relative)
                    discovered.append(record)
        return discovered

    def read_logs(self, *, job: dict[str, object], owner_user: str, max_bytes: int = 1_048_576) -> dict[str, str]:
        self._assert_job_owner(job, owner_user)
        logs_dir = self._job_subdir(job, "logs")
        result: dict[str, str] = {}
        for stream in ("stdout", "stderr"):
            target = logs_dir / f"{stream}.log"
            self._assert_under(logs_dir, target.resolve())
            if target.is_symlink() or not target.is_file():
                result[stream] = ""
                continue
            try:
                data = target.read_bytes()
            except OSError as exc:
                raise FileStoreError("logs could not be read") from exc
            result[stream] = data[:max_bytes].decode("utf-8", errors="replace")
        return result

    def download_file(self, *, job: dict[str, object], owner_user: str, file: dict[str, object]) -> bytes:
        self._assert_job_owner(job, owner_user)
        if file["job_id"] != job["id"] or file["owner_user"] != owner_user:
            raise FileStoreError("file not found")
        relative_path = str(file["relative_path"])
        target = (Path(str(job["workspace_path"])) / relative_path).resolve()
        workspace = Path(str(job["workspace_path"])).resolve()
        self._assert_under(workspace, target)
        try:
            details = target.lstat()
        except OSError as exc:
            raise FileStoreError("file not found") from exc
        if not stat.S_ISREG(details.st_mode) or target.is_symlink():
            raise FileStoreError("file not found")
        if details.st_size > self.max_file_bytes or details.st_size != int(file["size_bytes"]):
            raise FileStoreError("file metadata mismatch")
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise FileStoreError("file could not be read") from exc
        if hashlib.sha256(content).hexdigest() != file["sha256"]:
            raise FileStoreError("file integrity check failed")
        return content

    def _job_subdir(self, job: dict[str, object], name: str) -> Path:
        workspace = Path(str(job["workspace_path"])).resolve()
        self._assert_under(self.workspace_root, workspace)
        target = workspace / name
        if not target.is_dir() or target.is_symlink():
            raise FileStoreError("job workspace is unavailable")
        self._assert_under(workspace, target)
        return target

    def _assert_job_owner(self, job: dict[str, object], owner_user: str) -> None:
        if job.get("owner_user") != owner_user:
            raise FileStoreError("job not found")

    @staticmethod
    def _assert_under(root: Path, target: Path) -> None:
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise FileStoreError("path escapes workspace") from exc

    def _check_capacity(self, job: dict[str, object], incoming_bytes: int) -> None:
        current_job = sum(
            int(file["size_bytes"])
            for file in self.database.list_owned_files(str(job["id"]), str(job["owner_user"]))
        )
        if current_job + incoming_bytes > self.max_job_bytes:
            raise FileStoreError("job disk quota exceeded")
        if self.database.total_registered_bytes() + incoming_bytes > self.max_total_bytes:
            raise FileStoreError("global disk quota exceeded")


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 255:
        raise FileStoreError("invalid file name")
    if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise FileStoreError("invalid file name")
    if Path(name).name != name:
        raise FileStoreError("invalid file name")
    return name

def _safe_relative_path(workspace: Path, target: Path) -> str:
    target = target.resolve()
    try:
        relative = target.relative_to(workspace)
    except ValueError as exc:
        raise FileStoreError("output path escapes workspace") from exc
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise FileStoreError("invalid output path")
    return relative.as_posix()
