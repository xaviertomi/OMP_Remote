from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import os
import sqlite3
from typing import Iterator
import uuid


JOB_STATES = frozenset({"queued", "running", "completed", "failed", "cancelled", "aborted_emergency"})


class Database:
    """Small SQLite persistence boundary with one connection per operation."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    owner_user TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_devices_active_hash
                    ON devices(token_hash, revoked_at);
                CREATE TABLE IF NOT EXISTS emergency_credentials (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_emergency_active_hash
                    ON emergency_credentials(token_hash, revoked_at);
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    owner_user TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated
                    ON conversations(owner_user, updated_at DESC);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    owner_user TEXT NOT NULL,
                    device_id TEXT NOT NULL REFERENCES devices(id),
                    runner_id TEXT NOT NULL,
                    project_id TEXT,
                    prompt TEXT NOT NULL,
                    execution_prompt TEXT,
                    conversation_id TEXT REFERENCES conversations(id),
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL CHECK (state IN (
                        'queued', 'running', 'completed', 'failed',
                        'cancelled', 'aborted_emergency'
                    )),
                    workspace_path TEXT,
                    timeout_seconds INTEGER,
                    process_id INTEGER,
                    process_group_id INTEGER,
                    exit_code INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_owner_created
                    ON jobs(owner_user, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_queue
                    ON jobs(owner_user, state, created_at);
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                    VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                CREATE TABLE IF NOT EXISTS job_files (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    owner_user TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('input', 'output')),
                    relative_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, relative_path)
                );
                CREATE INDEX IF NOT EXISTS idx_job_files_owner
                    ON job_files(owner_user, job_id, created_at);
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                    VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    owner_user TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                    ON messages(conversation_id, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_messages_owner
                    ON messages(owner_user, conversation_id, created_at ASC);
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                    VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                """
            )
            self._ensure_column(connection, "jobs", "execution_prompt", "TEXT")
            self._ensure_column(
                connection,
                "jobs",
                "conversation_id",
                "TEXT REFERENCES conversations(id)",
            )
            self._ensure_column(connection, "messages", "sequence", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "jobs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                "UPDATE jobs SET execution_prompt = prompt WHERE execution_prompt IS NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_conversation "
                "ON jobs(conversation_id, created_at)"
            )
            connection.commit()
        if os.geteuid() == 0 or os.stat(self.path).st_uid == os.geteuid():
            os.chmod(self.path, 0o660)

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def insert_device(self, *, owner_user: str, token_hash: str, label: str = "") -> str:
        device_id = str(uuid.uuid4())
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO devices(id, owner_user, token_hash, label, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (device_id, owner_user, token_hash, label, now),
            )
            connection.commit()
        return device_id

    def find_active_device(self, token_hash: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT id, owner_user, label, created_at
                FROM devices
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (token_hash,),
            ).fetchone()

    def revoke_device(self, device_id: str) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE devices
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE id = ?
                """,
                (utc_now(), device_id),
            )
            connection.commit()
            return result.rowcount == 1

    def insert_emergency_credential(self, *, token_hash: str, label: str = "") -> str:
        credential_id = str(uuid.uuid4())
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO emergency_credentials(id, token_hash, label, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (credential_id, token_hash, label, utc_now()),
            )
            connection.commit()
        return credential_id

    def find_active_emergency(self, token_hash: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT id, label, created_at
                FROM emergency_credentials
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (token_hash,),
            ).fetchone()

    def revoke_emergency(self, credential_id: str) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE emergency_credentials
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE id = ?
                """,
                (utc_now(), credential_id),
            )
            connection.commit()
            return result.rowcount == 1
    def queued_count(self) -> int:
        with self.connection() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM jobs WHERE state = 'queued'").fetchone()[0]
            )

    def create_conversation(
        self,
        *,
        owner_user: str,
        conversation_id: str | None = None,
    ) -> dict[str, object]:
        conversation_id = conversation_id or str(uuid.uuid4())
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO conversations(id, owner_user, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, owner_user, now, now),
            )
            connection.commit()
        return self.get_owned_conversation(conversation_id, owner_user)  # type: ignore[return-value]

    def list_owned_conversations(
        self,
        owner_user: str,
        *,
        limit: int,
        offset: int,
    ) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.owner_user, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM conversations AS c
                LEFT JOIN messages AS m
                    ON m.conversation_id = c.id AND m.owner_user = c.owner_user
                WHERE c.owner_user = ?
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (owner_user, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_owned_conversation(
        self,
        conversation_id: str,
        owner_user: str,
    ) -> dict[str, object] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT c.id, c.owner_user, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM conversations AS c
                LEFT JOIN messages AS m
                    ON m.conversation_id = c.id AND m.owner_user = c.owner_user
                WHERE c.id = ? AND c.owner_user = ?
                GROUP BY c.id
                """,
                (conversation_id, owner_user),
            ).fetchone()
        return None if row is None else dict(row)

    def list_owned_messages(
        self,
        conversation_id: str,
        owner_user: str,
    ) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT m.id, m.conversation_id, m.owner_user, m.sequence, m.prompt, m.job_id,
                       m.created_at, j.state, j.error, j.workspace_path
                FROM messages AS m
                JOIN jobs AS j ON j.id = m.job_id
                WHERE m.conversation_id = ? AND m.owner_user = ?
                  AND j.owner_user = ?
                ORDER BY m.sequence ASC, m.created_at ASC, m.id ASC
                """,
                (conversation_id, owner_user, owner_user),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_owned_message(
        self,
        message_id: str,
        conversation_id: str,
        owner_user: str,
    ) -> dict[str, object] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT m.id, m.conversation_id, m.owner_user, m.sequence, m.prompt, m.job_id,
                       m.created_at, j.state, j.error, j.workspace_path
                FROM messages AS m
                JOIN jobs AS j ON j.id = m.job_id
                WHERE m.id = ? AND m.conversation_id = ? AND m.owner_user = ?
                  AND j.owner_user = ?
                """,
                (message_id, conversation_id, owner_user, owner_user),
            ).fetchone()
        return None if row is None else dict(row)

    def create_message(
        self,
        *,
        conversation_id: str,
        owner_user: str,
        prompt: str,
        job_id: str,
        message_id: str | None = None,
    ) -> dict[str, object]:
        message_id = message_id or str(uuid.uuid4())
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            conversation = connection.execute(
                "SELECT id FROM conversations WHERE id = ? AND owner_user = ?",
                (conversation_id, owner_user),
            ).fetchone()
            if conversation is None:
                raise ValueError("conversation not found")
            job = connection.execute(
                """
                SELECT id FROM jobs
                WHERE id = ? AND owner_user = ? AND conversation_id = ?
                """,
                (job_id, owner_user, conversation_id),
            ).fetchone()
            if job is None:
                raise ValueError("job not found")
            sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM messages WHERE conversation_id = ? AND owner_user = ?
                    """,
                    (conversation_id, owner_user),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO messages(
                    id, conversation_id, owner_user, sequence, prompt, job_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, conversation_id, owner_user, sequence, prompt, job_id, now),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ? AND owner_user = ?",
                (now, conversation_id, owner_user),
            )
            connection.commit()
        return self.get_owned_message(message_id, conversation_id, owner_user)  # type: ignore[return-value]

    def create_conversation_job(
        self,
        *,
        owner_user: str,
        device_id: str,
        runner_id: str,
        project_id: str | None,
        prompt: str,
        execution_prompt: str,
        workspace_path: str,
        timeout_seconds: int,
        conversation_id: str | None = None,
        job_id: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, dict[str, object]]:
        conversation_id = conversation_id or str(uuid.uuid4())
        job_id = job_id or str(uuid.uuid4())
        message_id = message_id or str(uuid.uuid4())
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO conversations(id, owner_user, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, owner_user, now, now),
            )
            self._insert_job(
                connection,
                job_id=job_id,
                owner_user=owner_user,
                device_id=device_id,
                runner_id=runner_id,
                project_id=project_id,
                prompt=prompt,
                execution_prompt=execution_prompt,
                conversation_id=conversation_id,
                workspace_path=workspace_path,
                timeout_seconds=timeout_seconds,
                created_at=now,
            )
            connection.execute(
                """
                INSERT INTO messages(
                    id, conversation_id, owner_user, sequence, prompt, job_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, conversation_id, owner_user, 1, prompt, job_id, now),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            connection.commit()
        return {
            "conversation": self.get_owned_conversation(conversation_id, owner_user),  # type: ignore[dict-item]
            "job": self.get_owned_job(job_id, owner_user),  # type: ignore[dict-item]
            "message": self.get_owned_message(message_id, conversation_id, owner_user),  # type: ignore[dict-item]
        }

    def append_conversation_job(
        self,
        *,
        conversation_id: str,
        owner_user: str,
        device_id: str,
        runner_id: str,
        project_id: str | None,
        prompt: str,
        execution_prompt: str,
        workspace_path: str,
        timeout_seconds: int,
        job_id: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, dict[str, object]] | None:
        job_id = job_id or str(uuid.uuid4())
        message_id = message_id or str(uuid.uuid4())
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            conversation = connection.execute(
                "SELECT id FROM conversations WHERE id = ? AND owner_user = ?",
                (conversation_id, owner_user),
            ).fetchone()
            if conversation is None:
                connection.rollback()
                return None
            self._insert_job(
                connection,
                job_id=job_id,
                owner_user=owner_user,
                device_id=device_id,
                runner_id=runner_id,
                project_id=project_id,
                prompt=prompt,
                execution_prompt=execution_prompt,
                conversation_id=conversation_id,
                workspace_path=workspace_path,
                timeout_seconds=timeout_seconds,
                created_at=now,
            )
            sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM messages WHERE conversation_id = ? AND owner_user = ?
                    """,
                    (conversation_id, owner_user),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO messages(
                    id, conversation_id, owner_user, sequence, prompt, job_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, conversation_id, owner_user, sequence, prompt, job_id, now),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ? AND owner_user = ?",
                (now, conversation_id, owner_user),
            )
            connection.commit()
        return {
            "conversation": self.get_owned_conversation(conversation_id, owner_user),  # type: ignore[dict-item]
            "job": self.get_owned_job(job_id, owner_user),  # type: ignore[dict-item]
            "message": self.get_owned_message(message_id, conversation_id, owner_user),  # type: ignore[dict-item]
        }

    @staticmethod
    def _insert_job(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        owner_user: str,
        device_id: str,
        runner_id: str,
        project_id: str | None,
        prompt: str,
        execution_prompt: str,
        conversation_id: str | None,
        workspace_path: str,
        timeout_seconds: int,
        created_at: str,
    ) -> None:
        if conversation_id is not None:
            conversation = connection.execute(
                "SELECT id FROM conversations WHERE id = ? AND owner_user = ?",
                (conversation_id, owner_user),
            ).fetchone()
            if conversation is None:
                raise ValueError("conversation not found")
        connection.execute(
            """
            INSERT INTO jobs(
                id, owner_user, device_id, runner_id, project_id, prompt,
                execution_prompt, conversation_id, state, workspace_path,
                timeout_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                job_id,
                owner_user,
                device_id,
                runner_id,
                project_id,
                prompt,
                execution_prompt,
                conversation_id,
                workspace_path,
                timeout_seconds,
                created_at,
                created_at,
            ),
        )

    def create_job(
        self,
        *,
        owner_user: str,
        device_id: str,
        runner_id: str,
        project_id: str | None,
        prompt: str,
        workspace_path: str,
        timeout_seconds: int,
        execution_prompt: str | None = None,
        conversation_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, object]:
        job_id = job_id or str(uuid.uuid4())
        now = utc_now()
        with self.connection() as connection:
            self._insert_job(
                connection,
                job_id=job_id,
                owner_user=owner_user,
                device_id=device_id,
                runner_id=runner_id,
                project_id=project_id,
                prompt=prompt,
                execution_prompt=prompt if execution_prompt is None else execution_prompt,
                conversation_id=conversation_id,
                workspace_path=workspace_path,
                timeout_seconds=timeout_seconds,
                created_at=now,
            )
            connection.commit()
        return self.get_owned_job(job_id, owner_user)  # type: ignore[return-value]

    def claim_next_job(
        self,
        owner_user: str,
        *,
        max_concurrent_per_user: int = 1,
        max_concurrent_global: int = 1,
    ) -> dict[str, object] | None:
        """Atomically claim the oldest queued job within both concurrency limits."""
        if max_concurrent_per_user < 1 or max_concurrent_global < 1:
            raise ValueError("concurrency limits must be positive")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id
                FROM jobs
                WHERE owner_user = ? AND state = 'queued'
                  AND (
                      SELECT COUNT(*) FROM jobs AS owner_running
                      WHERE owner_running.owner_user = ? AND owner_running.state = 'running'
                  ) < ?
                  AND (
                      SELECT COUNT(*) FROM jobs AS all_running
                      WHERE all_running.state = 'running'
                  ) < ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (owner_user, owner_user, max_concurrent_per_user, max_concurrent_global),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            job_id = str(row["id"])
            now = utc_now()
            connection.execute(
                """
                UPDATE jobs
                SET state = 'running', started_at = ?, updated_at = ?
                WHERE id = ? AND owner_user = ? AND state = 'queued'
                """,
                (now, now, job_id, owner_user),
            )
            claimed = connection.execute(
                """
                SELECT id, owner_user, device_id, runner_id, project_id, prompt,
                       execution_prompt, conversation_id, state, workspace_path,
                       timeout_seconds, process_id, process_group_id,
                       exit_code, error, created_at, updated_at, started_at, finished_at
                FROM jobs WHERE id = ? AND owner_user = ?
                """,
                (job_id, owner_user),
            ).fetchone()
            connection.commit()
        return None if claimed is None else dict(claimed)

    def set_process_metadata(self, job_id: str, owner_user: str, *, pid: int, pgid: int) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE jobs
                SET process_id = ?, process_group_id = ?, updated_at = ?
                WHERE id = ? AND owner_user = ? AND state = 'running'
                """,
                (pid, pgid, utc_now(), job_id, owner_user),
            )
            connection.commit()
            return result.rowcount == 1

    def finish_job(
        self,
        job_id: str,
        owner_user: str,
        *,
        state: str,
        exit_code: int | None,
        error: str | None = None,
    ) -> bool:
        if state not in {"completed", "failed", "cancelled", "aborted_emergency"}:
            raise ValueError("invalid terminal job state")
        now = utc_now()
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE jobs
                SET state = ?, exit_code = ?, error = ?, updated_at = ?, finished_at = ?
                WHERE id = ? AND owner_user = ? AND state = 'running'
                """,
                (state, exit_code, error, now, now, job_id, owner_user),
            )
            connection.commit()
            return result.rowcount == 1

    def register_file(
        self,
        *,
        job_id: str,
        owner_user: str,
        kind: str,
        relative_path: str,
        size_bytes: int,
        sha256: str,
    ) -> dict[str, object]:
        if kind not in {"input", "output"}:
            raise ValueError("invalid file kind")
        file_id = str(uuid.uuid4())
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO job_files(
                    id, job_id, owner_user, kind, relative_path, size_bytes, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    job_id,
                    owner_user,
                    kind,
                    relative_path,
                    size_bytes,
                    sha256,
                    utc_now(),
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT id, job_id, owner_user, kind, relative_path, size_bytes, sha256, created_at
                FROM job_files WHERE id = ?
                """,
                (file_id,),
            ).fetchone()
        return dict(row)

    def list_owned_files(self, job_id: str, owner_user: str) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, job_id, owner_user, kind, relative_path, size_bytes, sha256, created_at
                FROM job_files
                WHERE job_id = ? AND owner_user = ?
                ORDER BY created_at ASC
                """,
                (job_id, owner_user),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_owned_file(self, file_id: str, job_id: str, owner_user: str) -> dict[str, object] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT id, job_id, owner_user, kind, relative_path, size_bytes, sha256, created_at
                FROM job_files
                WHERE id = ? AND job_id = ? AND owner_user = ?
                """,
                (file_id, job_id, owner_user),
            ).fetchone()
        return None if row is None else dict(row)

    def total_registered_bytes(self) -> int:
        with self.connection() as connection:
            return int(connection.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM job_files").fetchone()[0])

    def reconcile_running_jobs(self, owner_user: str, *, error: str) -> int:
        now = utc_now()
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE jobs
                SET state = 'failed', error = ?, updated_at = ?, finished_at = ?
                WHERE owner_user = ? AND state = 'running'
                """,
                (error, now, now, owner_user),
            )
            connection.commit()
            return result.rowcount

    def list_running_jobs(self) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, owner_user, process_id, process_group_id, workspace_path
                FROM jobs WHERE state = 'running'
                ORDER BY started_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def abort_running_jobs(self, *, error: str) -> int:
        now = utc_now()
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE jobs
                SET state = 'aborted_emergency', error = ?, updated_at = ?, finished_at = ?
                WHERE state = 'running'
                """,
                (error, now, now),
            )
            connection.commit()
            return result.rowcount

    def list_terminal_jobs_before(self, cutoff: str, *, limit: int = 100) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, owner_user, state, workspace_path, finished_at
                FROM jobs
                WHERE state IN ('completed', 'failed', 'cancelled', 'aborted_emergency')
                  AND finished_at IS NOT NULL AND finished_at < ?
                ORDER BY finished_at ASC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def purge_job_workspace(self, job_id: str) -> bool:
        with self.connection() as connection:
            connection.execute("DELETE FROM job_files WHERE job_id = ?", (job_id,))
            result = connection.execute(
                "UPDATE jobs SET workspace_path = NULL, updated_at = ? WHERE id = ?",
                (utc_now(), job_id),
            )
            connection.commit()
            return result.rowcount == 1

    def list_owned_jobs(self, owner_user: str, *, limit: int, offset: int) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, owner_user, device_id, runner_id, project_id,
                       conversation_id, execution_prompt, state,
                       workspace_path, timeout_seconds, process_id, process_group_id,
                       exit_code, error, created_at, updated_at, started_at, finished_at
                FROM jobs
                WHERE owner_user = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (owner_user, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_owned_job(self, job_id: str, owner_user: str) -> dict[str, object] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT id, owner_user, device_id, runner_id, project_id, prompt,
                       execution_prompt, conversation_id, state, workspace_path,
                       timeout_seconds, process_id, process_group_id,
                       exit_code, error, created_at, updated_at, started_at, finished_at
                FROM jobs
                WHERE id = ? AND owner_user = ?
                """,
                (job_id, owner_user),
            ).fetchone()
        return None if row is None else dict(row)
    def cancel_owned_job(self, job_id: str, owner_user: str) -> dict[str, object] | None:
        now = utc_now()
        with self.connection() as connection:
            queued = connection.execute(
                """
                UPDATE jobs
                SET state = 'cancelled', updated_at = ?, finished_at = ?
                WHERE id = ? AND owner_user = ? AND state = 'queued'
                """,
                (now, now, job_id, owner_user),
            )
            if queued.rowcount == 0:
                connection.execute(
                    """
                    UPDATE jobs
                    SET cancel_requested = 1, updated_at = ?
                    WHERE id = ? AND owner_user = ? AND state = 'running'
                    """,
                    (now, job_id, owner_user),
                )
            connection.commit()
        return self.get_owned_job(job_id, owner_user)

    def is_cancel_requested(self, job_id: str, owner_user: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ? AND owner_user = ? AND state = 'running'",
                (job_id, owner_user),
            ).fetchone()
        return row is not None and bool(row["cancel_requested"])


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
