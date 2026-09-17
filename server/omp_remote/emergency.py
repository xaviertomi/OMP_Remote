from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import tempfile
from .db import Database
from .runner import ProcessSupervisor


class EmergencyError(Exception):
    pass


class EmergencyController:
    """Minimal global kill boundary for recorded OMP Remote jobs."""

    def __init__(
        self,
        database: Database,
        *,
        state_path: Path,
        incident_path: Path,
        workspace_root: Path | None = None,
        quarantine_root: Path | None = None,
        require_root: bool = True,
    ):
        self.database = database
        self.state_path = state_path
        self.incident_path = incident_path
        self.workspace_root = workspace_root.resolve() if workspace_root is not None else None
        self.quarantine_root = quarantine_root.resolve() if quarantine_root is not None else None
        self.require_root = require_root

    def status(self) -> dict[str, object]:
        return {
            "state": self._read_state(),
            "running_jobs": len(self.database.list_running_jobs()),
        }

    def kill(self, *, reason: str = "remote emergency kill", stop_main: Callable[[], None] | None = None) -> dict[str, object]:
        if self.require_root and os.geteuid() != 0:
            raise EmergencyError("emergency controller requires root")
        self._write_state("DISABLED")
        running = self.database.list_running_jobs()
        terminated = 0
        for job in running:
            pid = job.get("process_id")
            pgid = job.get("process_group_id")
            if not isinstance(pid, int) or not isinstance(pgid, int) or pid <= 0 or pgid <= 0:
                continue
            try:
                if os.getpgid(pid) != pgid:
                    continue
                ProcessSupervisor.terminate_group(pgid)
                terminated += 1
            except (OSError, ProcessLookupError):
                continue
        quarantined = self._quarantine(running)
        aborted = self.database.abort_running_jobs(error="aborted by emergency kill")
        self._record_incident(reason, len(running), terminated, aborted, quarantined)
        if stop_main is not None:
            stop_main()
        return {
            "state": "DISABLED",
            "running_jobs": len(running),
            "terminated": terminated,
            "aborted": aborted,
            "quarantined": quarantined,
        }

    def _quarantine(self, running: list[dict[str, object]]) -> int:
        if self.workspace_root is None or self.quarantine_root is None:
            return 0
        self.quarantine_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        moved = 0
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        for job in running:
            raw = job.get("workspace_path")
            if not raw:
                continue
            workspace = Path(str(raw)).resolve()
            try:
                workspace.relative_to(self.workspace_root)
            except ValueError:
                continue
            if workspace.parent != self.workspace_root or workspace.is_symlink() or not workspace.is_dir():
                continue
            target = self.quarantine_root / f"{job['id']}-{stamp}"
            try:
                shutil.move(str(workspace), str(target))
                moved += 1
            except OSError:
                continue
        return moved

    def _read_state(self) -> str:
        try:
            state = self.state_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return "ENABLED"
        except OSError:
            return "DISABLED"
        return state if state in {"ENABLED", "DISABLED"} else "DISABLED"

    def _write_state(self, state: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".state.", dir=self.state_path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(state + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _record_incident(
        self, reason: str, running: int, terminated: int, aborted: int, quarantined: int
    ) -> None:
        self.incident_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "reason": reason,
            "running_jobs": running,
            "terminated": terminated,
            "aborted": aborted,
            "quarantined": quarantined,
        }
        with self.incident_path.open("a", encoding="utf-8") as stream:
            os.chmod(self.incident_path, 0o600)
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
