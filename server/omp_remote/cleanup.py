from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import uuid

from .db import Database


class CleanupError(Exception):
    pass


def cleanup_old_jobs(
    database: Database,
    *,
    workspace_root: Path,
    retention: timedelta,
    limit: int = 100,
    dry_run: bool = False,
) -> list[str]:
    if retention.total_seconds() < 0:
        raise CleanupError("retention must not be negative")
    if limit < 1:
        raise CleanupError("limit must be positive")
    root = workspace_root.resolve()
    cutoff = (datetime.now(UTC) - retention).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    removed: list[str] = []
    for job in database.list_terminal_jobs_before(cutoff, limit=limit):
        raw_workspace = job.get("workspace_path")
        if not raw_workspace:
            continue
        workspace = Path(str(raw_workspace)).resolve()
        try:
            workspace.relative_to(root)
        except ValueError as exc:
            raise CleanupError("job workspace escapes configured root") from exc
        if workspace.parent != root:
            raise CleanupError("job workspace is not a direct generated child")
        try:
            uuid.UUID(workspace.name)
        except ValueError as exc:
            raise CleanupError("job workspace is not a generated UUID") from exc
        if workspace.is_symlink() or not workspace.is_dir():
            raise CleanupError("job workspace is not a regular directory")
        if not dry_run:
            shutil.rmtree(workspace)
            database.purge_job_workspace(str(job["id"]))
        removed.append(str(job["id"]))
    return removed
