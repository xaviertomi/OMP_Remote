from __future__ import annotations

from pathlib import Path
import threading

from .db import Database
from .files import FileStore, FileStoreError
from .runner import OMPRunner, ProcessResult, ProcessSupervisor, RunnerError


class Worker:
    """One-owner worker; it never claims another Unix user's jobs."""

    def __init__(
        self,
        *,
        database: Database,
        owner_user: str,
        runner: OMPRunner,
        state_path: Path,
        file_store: FileStore | None = None,
        max_concurrent_per_user: int = 1,
        max_concurrent_global: int = 1,
        poll_seconds: float = 0.5,
    ):
        self.database = database
        self.owner_user = owner_user
        self.runner = runner
        self.state_path = state_path
        self.file_store = file_store
        self.max_concurrent_per_user = max_concurrent_per_user
        self.max_concurrent_global = max_concurrent_global
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self._active_cancel: threading.Event | None = None
        self._active_lock = threading.Lock()

    def run_once(self) -> bool:
        if self.stop_event.is_set() or self._is_disabled():
            return False
        job = self.database.claim_next_job(
            self.owner_user,
            max_concurrent_per_user=self.max_concurrent_per_user,
            max_concurrent_global=self.max_concurrent_global,
        )
        if job is None:
            return False
        cancel_event = threading.Event()
        with self._active_lock:
            self._active_cancel = cancel_event
        try:
            if self._is_disabled():
                result = ProcessResult(
                    "aborted_emergency",
                    None,
                    0,
                    0,
                    "job blocked because OMP Remote is disabled",
                )
            else:
                result = self._run_job(job, cancel_event)
                if result.state == "completed" and self.file_store is not None:
                    try:
                        self.file_store.discover_outputs(job=job, owner_user=self.owner_user)
                    except FileStoreError as exc:
                        result = ProcessResult(
                            "failed", result.exit_code, result.pid, result.process_group_id, str(exc)
                        )
        finally:
            with self._active_lock:
                self._active_cancel = None
        self.database.finish_job(
            str(job["id"]),
            self.owner_user,
            state=result.state,
            exit_code=result.exit_code,
            error=result.error,
        )
        return True

    def run_forever(self) -> None:
        self.reconcile_on_startup()
        while not self.stop_event.is_set():
            if not self.run_once():
                self.stop_event.wait(self.poll_seconds)

    def reconcile_on_startup(self) -> int:
        return self.database.reconcile_running_jobs(
            self.owner_user,
            error="worker restarted before job completion",
        )

    def request_stop(self) -> None:
        self.stop_event.set()
        self.cancel_current()

    def cancel_current(self) -> None:
        with self._active_lock:
            if self._active_cancel is not None:
                self._active_cancel.set()

    def _run_job(self, job: dict[str, object], cancel_event: threading.Event) -> ProcessResult:
        watcher_stop = threading.Event()
        watcher = threading.Thread(
            target=self._watch_cancel_request,
            args=(str(job["id"]), cancel_event, watcher_stop),
            name=f"omp-remote-cancel-{job['id']}",
            daemon=True,
        )

        def on_started(pid: int, pgid: int) -> None:
            self.database.set_process_metadata(
                str(job["id"]),
                self.owner_user,
                pid=pid,
                pgid=pgid,
            )
            if self._is_disabled():
                cancel_event.set()
                ProcessSupervisor.terminate_group(pgid)

        watcher.start()
        try:
            try:
                return self.runner.run(
                    job,
                    cancel_event=cancel_event,
                    on_started=on_started,
                )
            except RunnerError as exc:
                return ProcessResult("failed", None, 0, 0, str(exc))
            except OSError:
                return ProcessResult("failed", None, 0, 0, "runner runtime failure")
        finally:
            watcher_stop.set()
            watcher.join(timeout=1.0)

    def _watch_cancel_request(
        self,
        job_id: str,
        cancel_event: threading.Event,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.wait(0.2):
            if self.database.is_cancel_requested(job_id, self.owner_user):
                cancel_event.set()
                return
    def _is_disabled(self) -> bool:
        try:
            state = self.state_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return state != "ENABLED"
