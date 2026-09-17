from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import pwd
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping


class RunnerError(Exception):
    """Raised when a configured runner cannot safely start."""


@dataclass(frozen=True, slots=True)
class ProcessResult:
    state: str
    exit_code: int | None
    pid: int
    process_group_id: int
    error: str | None = None


class ProcessSupervisor:
    """Run one argv command in its own process group with timeout/cancel control."""

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        stdout_path: Path,
        stderr_path: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
        cancel_event: threading.Event | None = None,
        on_started: Callable[[int, int], None] | None = None,
    ) -> ProcessResult:
        if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise RunnerError("runner argv must contain non-empty strings")
        if timeout_seconds < 1:
            raise RunnerError("timeout_seconds must be positive")
        cwd = cwd.resolve()
        if not cwd.is_dir():
            raise RunnerError("runner workspace does not exist")
        stdout_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stderr_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe_env = {str(key): str(value) for key, value in env.items()}
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    list(argv),
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=safe_env,
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as exc:
                raise RunnerError("runner process could not start") from exc
            pid = process.pid
            process_group_id = os.getpgid(pid)
            if on_started is not None:
                on_started(pid, process_group_id)
            deadline = time.monotonic() + timeout_seconds
            cancelled = False
            timed_out = False
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    self.terminate_group(process_group_id)
                remaining = deadline - time.monotonic()
                if remaining <= 0 and process.poll() is None:
                    timed_out = True
                    self.terminate_group(process_group_id)
                try:
                    exit_code = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if cancelled:
                return ProcessResult("cancelled", exit_code, pid, process_group_id)
            if timed_out:
                return ProcessResult(
                    "failed", exit_code, pid, process_group_id, "job timeout exceeded"
                )
            if exit_code == 0:
                return ProcessResult("completed", exit_code, pid, process_group_id)
            return ProcessResult("failed", exit_code, pid, process_group_id, "runner exited non-zero")

    @staticmethod
    def terminate_group(process_group_id: int, *, grace_seconds: float = 5.0) -> None:
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group_id, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            return


class OMPRunner:
    """Allowlisted OMP command builder; no shell interpolation or arbitrary binary."""

    def __init__(self, *, omp_binary: Path):
        if not omp_binary.is_absolute():
            raise ValueError("omp_binary must be an absolute path")
        self.omp_binary = omp_binary
        self.supervisor = ProcessSupervisor()

    def command(self, prompt: str, timeout_seconds: int) -> tuple[str, ...]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise RunnerError("prompt is required")
        if timeout_seconds < 1 or timeout_seconds > 86_400:
            raise RunnerError("timeout_seconds is outside the allowed range")
        return (
            str(self.omp_binary),
            "-p",
            "--no-session",
            f"--max-time={timeout_seconds}s",
            prompt,
        )

    def environment(self, owner_user: str, job_id: str) -> dict[str, str]:
        try:
            account = pwd.getpwnam(owner_user)
        except KeyError as exc:
            raise RunnerError("owner Unix account does not exist") from exc
        if os.geteuid() != account.pw_uid:
            raise RunnerError("worker Unix identity does not match job owner")
        return {
            "HOME": account.pw_dir,
            "USER": account.pw_name,
            "LOGNAME": account.pw_name,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "OMP_REMOTE_JOB_ID": job_id,
        }

    def run(
        self,
        job: Mapping[str, object],
        *,
        cancel_event: threading.Event | None = None,
        on_started: Callable[[int, int], None] | None = None,
    ) -> ProcessResult:
        job_id = str(job["id"])
        owner_user = str(job["owner_user"])
        workspace = Path(str(job["workspace_path"])).resolve()
        logs = workspace / "logs"
        timeout_seconds = int(job["timeout_seconds"])
        execution_prompt = job.get("execution_prompt")
        if not isinstance(execution_prompt, str) or not execution_prompt.strip():
            execution_prompt = str(job["prompt"])
        return self.supervisor.run(
            self.command(execution_prompt, timeout_seconds),
            cwd=workspace / "workspace",
            stdout_path=logs / "stdout.log",
            stderr_path=logs / "stderr.log",
            timeout_seconds=timeout_seconds,
            env=self.environment(owner_user, job_id),
            cancel_event=cancel_event,
            on_started=on_started,
        )
