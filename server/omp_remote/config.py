from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .version import VERSION_CODE, VERSION_NAME


DEFAULT_ALLOWED_USERS = ("tomi",)


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings shared by API, workers, and local tests."""

    data_dir: Path
    db_path: Path
    state_path: Path
    workspace_root: Path
    apk_path: Path
    apk_version_code: int
    apk_version_name: str
    bind_host: str
    bind_port: int
    allowed_users: tuple[str, ...]
    projects_json: str
    max_body_bytes: int
    max_prompt_bytes: int
    max_queue_length: int
    max_file_bytes: int
    max_files_per_job: int
    max_job_bytes: int
    max_total_bytes: int
    max_concurrent_per_user: int
    max_concurrent_global: int

    @classmethod
    def from_env(cls, *, environ: dict[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        project_root = Path(__file__).resolve().parents[1]
        data_dir = Path(env.get("OMP_REMOTE_DATA_DIR", project_root / "data")).expanduser()
        db_path = Path(env.get("OMP_REMOTE_DB_PATH", data_dir / "omp-remote.sqlite3")).expanduser()
        state_path = Path(env.get("OMP_REMOTE_STATE_PATH", data_dir / "state")).expanduser()
        apk_path = Path(env.get("OMP_REMOTE_APK_PATH", data_dir / "client" / "omp-remote.apk")).expanduser()
        # APK metadata is source-controlled with the Android release version.
        apk_version_code = VERSION_CODE
        apk_version_name = VERSION_NAME
        workspace_root = Path(
            env.get("OMP_REMOTE_WORKSPACE_ROOT", data_dir / "jobs")
        ).expanduser()
        allowed_users = tuple(
            user.strip()
            for user in env.get("OMP_REMOTE_ALLOWED_USERS", ",".join(DEFAULT_ALLOWED_USERS)).split(",")
            if user.strip()
        )
        if not allowed_users or len(set(allowed_users)) != len(allowed_users):
            raise ValueError("OMP_REMOTE_ALLOWED_USERS must contain unique non-empty users")
        bind_port = _int_env(env, "OMP_REMOTE_BIND_PORT", 8099, minimum=1, maximum=65535)
        max_body_bytes = _int_env(env, "OMP_REMOTE_MAX_BODY_BYTES", 10_485_760, minimum=1024)
        max_prompt_bytes = _int_env(env, "OMP_REMOTE_MAX_PROMPT_BYTES", 100_000, minimum=1)
        max_queue_length = _int_env(env, "OMP_REMOTE_MAX_QUEUE_LENGTH", 100, minimum=1)
        max_file_bytes = _int_env(env, "OMP_REMOTE_MAX_FILE_BYTES", 5 * 1024 * 1024, minimum=1)
        max_files_per_job = _int_env(env, "OMP_REMOTE_MAX_FILES_PER_JOB", 100, minimum=1)
        max_job_bytes = _int_env(env, "OMP_REMOTE_MAX_JOB_BYTES", 100 * 1024 * 1024, minimum=1)
        max_total_bytes = _int_env(env, "OMP_REMOTE_MAX_TOTAL_BYTES", 10 * 1024 * 1024 * 1024, minimum=1)
        max_concurrent_per_user = _int_env(env, "OMP_REMOTE_MAX_CONCURRENT_PER_USER", 1, minimum=1)
        max_concurrent_global = _int_env(env, "OMP_REMOTE_MAX_CONCURRENT_GLOBAL", 2, minimum=1)
        return cls(
            data_dir=data_dir,
            db_path=db_path,
            state_path=state_path,
            workspace_root=workspace_root,
            apk_path=apk_path,
            apk_version_code=apk_version_code,
            apk_version_name=apk_version_name,
            bind_host=env.get("OMP_REMOTE_BIND_HOST", "127.0.0.1"),
            bind_port=bind_port,
            allowed_users=allowed_users,
            projects_json=env.get("OMP_REMOTE_PROJECTS_JSON", "[]"),
            max_body_bytes=max_body_bytes,
            max_prompt_bytes=max_prompt_bytes,
            max_queue_length=max_queue_length,
            max_file_bytes=max_file_bytes,
            max_files_per_job=max_files_per_job,
            max_job_bytes=max_job_bytes,
            max_total_bytes=max_total_bytes,
            max_concurrent_per_user=max_concurrent_per_user,
            max_concurrent_global=max_concurrent_global,
        )

    def ensure_runtime_dirs(self) -> None:
        """Create only local development/runtime directories; never alter host services."""
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def _int_env(
    env: dict[str, str], name: str, default: int, *, minimum: int, maximum: int | None = None
) -> int:
    raw = env.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside the allowed range")
    return value
