from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from server.omp_remote.app import Application, ApplicationServer
from server.omp_remote.auth import AuthenticationError
from server.omp_remote.config import Settings
from server.omp_remote.db import Database
from server.omp_remote.files import FileStore, FileStoreError
from server.omp_remote.emergency_app import EmergencyApplication
from server.omp_remote.cleanup import cleanup_old_jobs
from server.omp_remote.runner import OMPRunner, ProcessResult, ProcessSupervisor
from server.omp_remote.worker import Worker

class BackendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.settings = Settings.from_env(
            environ={
                "OMP_REMOTE_DATA_DIR": str(root / "data"),
                "OMP_REMOTE_WORKSPACE_ROOT": str(root / "jobs"),
                "OMP_REMOTE_ALLOWED_USERS": "tomi,draz",
            }
        )
        self.app = Application(self.settings)
        self.tomi_id, self.tomi_token = self.app.credentials.issue_device(
            owner_user="tomi", label="test-tomi"
        )
        self.draz_id, self.draz_token = self.app.credentials.issue_device(
            owner_user="draz", label="test-draz"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_health_is_public_and_reports_ready(self) -> None:
        response = self.app.handle("GET", "/api/v1/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["status"], "ok")
        self.assertFalse(response.payload["disabled"])

    def test_authenticated_app_update_metadata_and_download(self) -> None:
        root = Path(self.tempdir.name) / "update"
        root.mkdir()
        apk = root / "omp-remote.apk"
        apk.write_bytes(b"apk-test")
        settings = Settings.from_env(
            environ={
                "OMP_REMOTE_DATA_DIR": str(root / "data"),
                "OMP_REMOTE_APK_PATH": str(apk),
            }
        )
        app = Application(settings)
        _, token = app.credentials.issue_device(owner_user="tomi")
        metadata = app.handle("GET", "/api/v1/app/update", self._headers(token))
        self.assertEqual(metadata.status, 200)
        self.assertEqual(metadata.payload["update"]["size_bytes"], len(b"apk-test"))
        self.assertEqual(metadata.payload["update"]["filename"], "omp-remote-v0.5.apk")
        download = app.handle("GET", "/api/v1/app/update/download", self._headers(token))
        self.assertEqual(download.status, 200)
        self.assertEqual(download.payload, b"apk-test")
        self.assertEqual(download.headers["Content-Disposition"], 'attachment; filename="omp-remote-v0.5.apk"')
        bootstrap = app.handle("GET", "/download/omp-remote.apk")
        self.assertEqual(bootstrap.status, 200)
        self.assertEqual(bootstrap.payload, b"apk-test")
        self.assertEqual(bootstrap.headers["Content-Disposition"], 'attachment; filename="omp-remote-v0.5.apk"')
        self.assertEqual(
            app.handle("GET", "/api/v1/app/update", {}).status,
            401,
        )

    def test_normal_routes_require_bearer_credentials(self) -> None:
        response = self.app.handle("GET", "/api/v1/jobs")
        self.assertEqual(response.status, 401)
        self.assertEqual(response.headers, {"WWW-Authenticate": "Bearer"})

    def test_job_owner_is_derived_from_credential_and_cross_user_isolated(self) -> None:
        create = self.app.handle(
            "POST",
            "/api/v1/jobs",
            self._headers(self.tomi_token),
            self._json({"runner_id": "omp", "prompt": "hello"}),
        )
        self.assertEqual(create.status, 202)
        job = create.payload["job"]
        self.assertEqual(job["owner_user"], "tomi")
        self.assertEqual(job["device_id"], self.tomi_id)
        self.assertNotIn("workspace_path", job)
        internal_job = self.app.database.get_owned_job(job["id"], "tomi")
        self.assertTrue((Path(internal_job["workspace_path"]) / "output").is_dir())

        tomi_jobs = self.app.handle("GET", "/api/v1/jobs", self._headers(self.tomi_token))
        draz_jobs = self.app.handle("GET", "/api/v1/jobs", self._headers(self.draz_token))
        self.assertEqual(len(tomi_jobs.payload["jobs"]), 1)
        self.assertEqual(draz_jobs.payload["jobs"], [])

        job_id = job["id"]
        denied = self.app.handle(
            "GET", f"/api/v1/jobs/{job_id}", self._headers(self.draz_token)
        )
        self.assertEqual(denied.status, 404)

        cancelled = self.app.handle(
            "POST", f"/api/v1/jobs/{job_id}/cancel", self._headers(self.tomi_token)
        )
        self.assertEqual(cancelled.status, 200)
        self.assertEqual(cancelled.payload["job"]["state"], "cancelled")

    def test_running_job_cancel_sets_worker_request(self) -> None:
        create = self.app.handle(
            "POST",
            "/api/v1/jobs",
            self._headers(self.tomi_token),
            self._json({"runner_id": "omp", "prompt": "running"}),
        )
        self.assertEqual(create.status, 202)
        job_id = create.payload["job"]["id"]
        self.assertEqual(self.app.database.claim_next_job("tomi")["id"], job_id)
        cancelled = self.app.handle(
            "POST", f"/api/v1/jobs/{job_id}/cancel", self._headers(self.tomi_token)
        )
        self.assertEqual(cancelled.status, 200)
        self.assertEqual(cancelled.payload["job"]["state"], "running")
        self.assertTrue(self.app.database.is_cancel_requested(job_id, "tomi"))

    def test_conversation_first_and_second_message_keep_context_and_logs(self) -> None:
        first = self.app.handle(
            "POST",
            "/api/v1/conversations",
            self._headers(self.tomi_token),
            self._json({"prompt": "first question"}),
        )
        self.assertEqual(first.status, 202)
        conversation_id = first.payload["conversation"]["id"]
        first_job_id = first.payload["job"]["id"]
        first_job = self.app.database.get_owned_job(first_job_id, "tomi")
        Path(first_job["workspace_path"], "logs", "stdout.log").write_text(
            "first answer", encoding="utf-8"
        )
        Path(first_job["workspace_path"], "logs", "stderr.log").write_text(
            "", encoding="utf-8"
        )

        second = self.app.handle(
            "POST",
            f"/api/v1/conversations/{conversation_id}/messages",
            self._headers(self.tomi_token),
            self._json({"prompt": "follow-up question"}),
        )
        self.assertEqual(second.status, 202)
        self.assertEqual(second.payload["conversation"]["id"], conversation_id)
        self.assertNotEqual(second.payload["job"]["id"], first_job_id)
        second_job = self.app.database.get_owned_job(second.payload["job"]["id"], "tomi")
        self.assertEqual(second_job["prompt"], "follow-up question")
        execution_prompt = second_job["execution_prompt"]
        self.assertIn("first question", execution_prompt)
        self.assertIn("first answer", execution_prompt)
        self.assertIn("follow-up question", execution_prompt)
        self.assertLessEqual(
            len(execution_prompt.encode("utf-8")),
            self.settings.max_prompt_bytes,
        )

        detail = self.app.handle(
            "GET",
            f"/api/v1/conversations/{conversation_id}",
            self._headers(self.tomi_token),
        )
        self.assertEqual(detail.status, 200)
        messages = detail.payload["conversation"]["messages"]
        self.assertEqual([message["prompt"] for message in messages], ["first question", "follow-up question"])
        self.assertEqual(messages[0]["job_id"], first_job_id)
        self.assertEqual(messages[0]["state"], "queued")
        self.assertEqual(messages[0]["stdout"], "first answer")
        for message in messages:
            self.assertIn("stderr", message)
            self.assertIn("state", message)

        listing = self.app.handle(
            "GET",
            "/api/v1/conversations",
            self._headers(self.tomi_token),
        )
        self.assertEqual(listing.status, 200)
        self.assertEqual(listing.payload["conversations"][0]["id"], conversation_id)
        self.assertEqual(listing.payload["conversations"][0]["message_count"], 2)

    def test_conversation_owner_isolation_and_unknown_ids(self) -> None:
        created = self.app.handle(
            "POST",
            "/api/v1/conversations",
            self._headers(self.tomi_token),
            self._json({"prompt": "private"}),
        )
        conversation_id = created.payload["conversation"]["id"]
        self.assertEqual(
            self.app.handle(
                "GET",
                f"/api/v1/conversations/{conversation_id}",
                self._headers(self.draz_token),
            ).status,
            404,
        )
        self.assertEqual(
            self.app.handle(
                "POST",
                f"/api/v1/conversations/{conversation_id}/messages",
                self._headers(self.draz_token),
                self._json({"prompt": "cross-user"}),
            ).status,
            404,
        )
        self.assertEqual(
            self.app.handle(
                "GET",
                "/api/v1/conversations/not-a-uuid",
                self._headers(self.tomi_token),
            ).status,
            404,
        )
        unknown = "00000000-0000-4000-8000-000000000000"
        self.assertEqual(
            self.app.handle(
                "GET",
                f"/api/v1/conversations/{unknown}",
                self._headers(self.tomi_token),
            ).status,
            404,
        )
    def test_identity_injection_is_rejected(self) -> None:
        response = self.app.handle(
            "POST",
            "/api/v1/jobs",
            self._headers(self.tomi_token),
            self._json({"runner_id": "omp", "prompt": "hello", "run_as": "draz"}),
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(self.app.handle("GET", "/api/v1/jobs", self._headers(self.tomi_token)).payload["jobs"], [])

    def test_revocation_and_emergency_separation(self) -> None:
        self.assertTrue(self.app.credentials.revoke_device(self.tomi_id))
        response = self.app.handle("GET", "/api/v1/jobs", self._headers(self.tomi_token))
        self.assertEqual(response.status, 401)

        emergency_id, emergency_token = self.app.credentials.issue_emergency(label="test")
        self.assertNotEqual(emergency_id, self.tomi_id)
        normal_with_emergency = self.app.handle("GET", "/api/v1/jobs", self._headers(emergency_token))
        self.assertEqual(normal_with_emergency.status, 401)
        with self.assertRaises(AuthenticationError):
            self.app.credentials.authenticate_emergency(f"Bearer {self.draz_token}")
        self.assertTrue(self.app.credentials.revoke_emergency(emergency_id))

    def test_disabled_state_fails_closed_except_health(self) -> None:
        self.settings.state_path.write_text("DISABLED\n", encoding="utf-8")
        health = self.app.handle("GET", "/api/v1/health")
        jobs = self.app.handle("GET", "/api/v1/jobs", self._headers(self.draz_token))
        self.assertEqual(health.payload["status"], "disabled")
        self.assertEqual(jobs.status, 503)
        self.settings.state_path.write_text("ENABLED\n", encoding="utf-8")

    def test_http_server_smoke_and_startup_fail_closed(self) -> None:

        server = ApplicationServer(replace(self.settings, bind_port=0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            with urllib.request.urlopen(f"{base_url}/api/v1/health", timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)["status"], "ok")
            request = urllib.request.Request(
                f"{base_url}/api/v1/jobs",
                headers=self._headers(self.tomi_token),
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)["jobs"], [])
            create_request = urllib.request.Request(
                f"{base_url}/api/v1/jobs",
                data=self._json({"runner_id": "omp", "prompt": "http file"}),
                headers={**self._headers(self.tomi_token), "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(create_request, timeout=3) as response:
                job_id = json.load(response)["job"]["id"]
            upload_request = urllib.request.Request(
                f"{base_url}/api/v1/jobs/{job_id}/files",
                data=self._json(
                    {
                        "name": "http.txt",
                        "content_base64": base64.b64encode(b"http-ok").decode("ascii"),
                    }
                ),
                headers={**self._headers(self.tomi_token), "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(upload_request, timeout=3) as response:
                file_id = json.load(response)["file"]["id"]
            with urllib.request.urlopen(
                urllib.request.Request(
                    f"{base_url}/api/v1/jobs/{job_id}/files/{file_id}",
                    headers=self._headers(self.tomi_token),
                ),
                timeout=3,
            ) as response:
                self.assertEqual(response.read(), b"http-ok")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.settings.state_path.write_text("DISABLED\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            ApplicationServer(replace(self.settings, bind_port=0))
        self.settings.state_path.write_text("ENABLED\n", encoding="utf-8")

    def test_file_upload_download_and_traversal_denial(self) -> None:
        created = self.app.handle(
            "POST",
            "/api/v1/jobs",
            self._headers(self.tomi_token),
            self._json({"runner_id": "omp", "prompt": "file test"}),
        )
        job_id = created.payload["job"]["id"]
        internal_job = self.app.database.get_owned_job(job_id, "tomi")
        upload = self.app.handle(
            "POST",
            f"/api/v1/jobs/{job_id}/files",
            self._headers(self.tomi_token),
            self._json(
                {
                    "name": "input.txt",
                    "content_base64": base64.b64encode(b"hello").decode("ascii"),
                }
            ),
        )
        self.assertEqual(upload.status, 201)
        file_id = upload.payload["file"]["id"]
        listing = self.app.handle(
            "GET", f"/api/v1/jobs/{job_id}/files", self._headers(self.tomi_token)
        )
        self.assertEqual(len(listing.payload["files"]), 1)
        downloaded = self.app.handle(
            "GET",
            f"/api/v1/jobs/{job_id}/files/{file_id}",
            self._headers(self.tomi_token),
        )
        self.assertEqual(downloaded.status, 200)
        self.assertEqual(downloaded.payload, b"hello")
        Path(internal_job["workspace_path"], "logs", "stdout.log").write_text("out", encoding="utf-8")
        Path(internal_job["workspace_path"], "logs", "stderr.log").write_text("err", encoding="utf-8")
        logs = self.app.handle(
            "GET", f"/api/v1/jobs/{job_id}/logs", self._headers(self.tomi_token)
        )
        self.assertEqual(logs.payload, {"stdout": "out", "stderr": "err"})
        denied = self.app.handle(
            "GET", f"/api/v1/jobs/{job_id}/files", self._headers(self.draz_token)
        )
        self.assertEqual(denied.status, 404)
        traversal = self.app.handle(
            "POST",
            f"/api/v1/jobs/{job_id}/files",
            self._headers(self.tomi_token),
            self._json(
                {
                    "name": "../escape",
                    "content_base64": base64.b64encode(b"no").decode("ascii"),
                }
            ),
        )
        self.assertEqual(traversal.status, 400)
        symlink = Path(internal_job["workspace_path"]) / "input/link"
        symlink.symlink_to("/tmp")
        symlink_upload = self.app.handle(
            "POST",
            f"/api/v1/jobs/{job_id}/files",
            self._headers(self.tomi_token),
            self._json(
                {
                    "name": "link",
                    "content_base64": base64.b64encode(b"no").decode("ascii"),
                }
            ),
        )
        self.assertEqual(symlink_upload.status, 400)

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _json(self, value: object) -> bytes:
        return json.dumps(value).encode("utf-8")


class DatabaseTests(unittest.TestCase):
    def test_schema_is_idempotent_and_device_owner_is_stored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "remote.sqlite3")
            database.initialize()
            database.initialize()
            device_id = database.insert_device(owner_user="tomi", token_hash="hash")
            row = database.find_active_device("hash")
            self.assertEqual(row["id"], device_id)
            self.assertEqual(row["owner_user"], "tomi")
            self.assertTrue(database.revoke_device(device_id))
            self.assertIsNone(database.find_active_device("hash"))

    def test_initialize_migrates_existing_jobs_without_losing_prompt(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE devices(
                    id TEXT PRIMARY KEY,
                    owner_user TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE TABLE jobs(
                    id TEXT PRIMARY KEY,
                    owner_user TEXT NOT NULL,
                    device_id TEXT NOT NULL REFERENCES devices(id),
                    runner_id TEXT NOT NULL,
                    project_id TEXT,
                    prompt TEXT NOT NULL,
                    state TEXT NOT NULL,
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
                INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'now');
                INSERT INTO devices(id, owner_user, token_hash, created_at)
                    VALUES ('device', 'tomi', 'hash', 'now');
                INSERT INTO jobs(
                    id, owner_user, device_id, runner_id, prompt, state,
                    workspace_path, timeout_seconds, created_at, updated_at
                ) VALUES (
                    'job', 'tomi', 'device', 'omp', 'legacy prompt', 'queued',
                    '/tmp/legacy', 10, 'now', 'now'
                );
                """
            )
            connection.commit()
            connection.close()

            database = Database(path)
            database.initialize()
            migrated = database.get_owned_job("job", "tomi")
            self.assertEqual(migrated["prompt"], "legacy prompt")
            self.assertEqual(migrated["execution_prompt"], "legacy prompt")
            with database.connection() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
                }
                self.assertIn("conversation_id", columns)
                self.assertIn("execution_prompt", columns)
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                self.assertIn("conversations", tables)
                self.assertIn("messages", tables)
    def test_claim_is_atomic_per_owner_and_terminal_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "remote.sqlite3")
            database.initialize()
            tomi_device = database.insert_device(owner_user="tomi", token_hash="tomi-hash")
            draz_device = database.insert_device(owner_user="draz", token_hash="draz-hash")
            first = database.create_job(
                owner_user="tomi",
                device_id=tomi_device,
                runner_id="omp",
                project_id=None,
                prompt="first",
                workspace_path=str(root / "first"),
                timeout_seconds=10,
            )
            second = database.create_job(
                owner_user="tomi",
                device_id=tomi_device,
                runner_id="omp",
                project_id=None,
                prompt="second",
                workspace_path=str(root / "second"),
                timeout_seconds=10,
            )
            database.create_job(
                owner_user="draz",
                device_id=draz_device,
                runner_id="omp",
                project_id=None,
                prompt="draz",
                workspace_path=str(root / "draz"),
                timeout_seconds=10,
            )
            claimed = database.claim_next_job("tomi")
            self.assertEqual(claimed["id"], first["id"])
            self.assertEqual(claimed["state"], "running")
            self.assertIsNone(database.claim_next_job("tomi"))
            self.assertTrue(database.set_process_metadata(first["id"], "tomi", pid=10, pgid=10))
            self.assertTrue(
                database.finish_job(first["id"], "tomi", state="completed", exit_code=0)
            )
            next_job = database.claim_next_job("tomi")
            self.assertEqual(next_job["id"], second["id"])


class ProjectTests(unittest.TestCase):
    def test_projects_are_owner_filtered_and_client_cannot_select_other_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = json.dumps(
                [
                    {"id": "tomi-project", "name": "Tomi project", "owner_users": ["tomi"], "source_path": str(root / "tomi")},
                    {"id": "draz-project", "name": "Draz project", "owner_users": ["draz"], "source_path": str(root / "draz")},
                ]
            )
            settings = Settings.from_env(
                environ={
                    "OMP_REMOTE_DATA_DIR": str(root / "data"),
                    "OMP_REMOTE_WORKSPACE_ROOT": str(root / "jobs"),
                    "OMP_REMOTE_ALLOWED_USERS": "tomi,draz",
                    "OMP_REMOTE_PROJECTS_JSON": projects,
                }
            )
            app = Application(settings)
            tomi_id, tomi_token = app.credentials.issue_device(owner_user="tomi")
            _, draz_token = app.credentials.issue_device(owner_user="draz")
            tomi_projects = app.handle("GET", "/api/v1/projects", self._auth(tomi_token))
            draz_projects = app.handle("GET", "/api/v1/projects", self._auth(draz_token))
            self.assertEqual([project["id"] for project in tomi_projects.payload["projects"]], ["tomi-project"])
            self.assertEqual([project["id"] for project in draz_projects.payload["projects"]], ["draz-project"])
            accepted = app.handle(
                "POST",
                "/api/v1/jobs",
                self._auth(tomi_token),
                json.dumps({"runner_id": "omp", "project_id": "tomi-project", "prompt": "ok"}).encode(),
            )
            self.assertEqual(accepted.status, 202)
            rejected = app.handle(
                "POST",
                "/api/v1/jobs",
                self._auth(tomi_token),
                json.dumps({"runner_id": "omp", "project_id": "draz-project", "prompt": "no"}).encode(),
            )
            self.assertEqual(rejected.status, 400)

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


class FileStoreTests(unittest.TestCase):
    def test_output_discovery_and_quotas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "remote.sqlite3")
            database.initialize()
            device_id = database.insert_device(owner_user="tomi", token_hash="hash")
            workspace = root / "job"
            for name in ("input", "workspace", "output", "logs", "metadata"):
                (workspace / name).mkdir(parents=True)
            job = database.create_job(
                owner_user="tomi",
                device_id=device_id,
                runner_id="omp",
                project_id=None,
                prompt="output",
                workspace_path=str(workspace),
                timeout_seconds=10,
            )
            store = FileStore(
                database,
                workspace_root=root,
                max_file_bytes=10,
                max_job_bytes=20,
                max_total_bytes=30,
            )
            (workspace / "output" / "result.txt").write_bytes(b"result")
            records = store.discover_outputs(job=job, owner_user="tomi")
            self.assertEqual(records[0]["kind"], "output")
            self.assertEqual(store.download_file(job=job, owner_user="tomi", file=records[0]), b"result")
            (workspace / "workspace" / "generated.txt").write_bytes(b"file")
            workspace_records = store.discover_outputs(job=job, owner_user="tomi")
            self.assertEqual(workspace_records[0]["relative_path"], "workspace/generated.txt")
            self.assertEqual(
                store.download_file(job=job, owner_user="tomi", file=workspace_records[0]),
                b"file",
            )
            (workspace / "output" / "escape").symlink_to("/tmp")
            with self.assertRaises(FileStoreError):
                store.discover_outputs(job=job, owner_user="tomi")
            with self.assertRaises(FileStoreError):
                store.upload_input(job=job, owner_user="tomi", name="large", content=b"01234567890")

class CleanupTests(unittest.TestCase):
    def test_cleanup_removes_only_old_generated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "remote.sqlite3")
            database.initialize()
            device_id = database.insert_device(owner_user="tomi", token_hash="hash")
            import uuid

            workspace = root / str(uuid.uuid4())
            workspace.mkdir()
            job = database.create_job(
                owner_user="tomi",
                device_id=device_id,
                runner_id="omp",
                project_id=None,
                prompt="cleanup",
                workspace_path=str(workspace),
                timeout_seconds=10,
            )
            database.claim_next_job("tomi")
            database.finish_job(job["id"], "tomi", state="completed", exit_code=0)
            with database.connection() as connection:
                connection.execute(
                    "UPDATE jobs SET finished_at = ?, updated_at = ? WHERE id = ?",
                    ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z", job["id"]),
                )
                connection.commit()
            removed = cleanup_old_jobs(
                database,
                workspace_root=root,
                retention=__import__("datetime").timedelta(days=1),
            )
            self.assertEqual(removed, [job["id"]])
            self.assertFalse(workspace.exists())
            self.assertIsNone(database.get_owned_job(job["id"], "tomi")["workspace_path"])


class EmergencyTests(unittest.TestCase):
    def test_emergency_surface_is_separate_and_has_no_enable_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings.from_env(
                environ={
                    "OMP_REMOTE_DATA_DIR": str(root / "data"),
                    "OMP_REMOTE_ALLOWED_USERS": "tomi",
                }
            )
            app = Application(settings)
            _, emergency_token = app.credentials.issue_emergency(label="test")
            emergency = EmergencyApplication(settings, require_root=False)
            auth = {"authorization": f"Bearer {emergency_token}"}
            status, payload, _ = emergency.handle("GET", "/emergency/status", auth)
            self.assertEqual(status, 200)
            self.assertEqual(payload["state"], "ENABLED")
            denied, _, _ = emergency.handle("POST", "/emergency/enable", auth)
            self.assertEqual(denied, 404)
            _, normal_token = app.credentials.issue_device(owner_user="tomi")
            created = app.handle(
                "POST",
                "/api/v1/jobs",
                {"Authorization": f"Bearer {normal_token}"},
                json.dumps({"runner_id": "omp", "prompt": "running"}).encode()
            )
            job_id = created.payload["job"]["id"]
            self.assertEqual(app.database.claim_next_job("tomi")["id"], job_id)
            killed, payload, _ = emergency.handle("POST", "/emergency/kill", auth)
            self.assertEqual(killed, 200)
            self.assertEqual(payload["quarantined"], 1)
            self.assertEqual(app.database.get_owned_job(job_id, "tomi")["state"], "aborted_emergency")
            normal = app.handle("GET", "/api/v1/jobs", {})
            self.assertEqual(normal.status, 503)



class ScopeTests(unittest.TestCase):
    def test_default_scope_is_tomi_only_until_draz_is_explicitly_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.from_env(environ={"OMP_REMOTE_DATA_DIR": directory})
            self.assertEqual(settings.allowed_users, ("tomi",))

class RunnerTests(unittest.TestCase):
    def test_omp_command_is_argv_only(self) -> None:
        runner = OMPRunner(omp_binary=Path("/absolute/omp"))
        self.assertEqual(
            runner.command("prompt; no shell", 12),
            ("/absolute/omp", "-p", "--no-session", "--max-time=12s", "prompt; no shell"),
        )
    def test_runner_executes_server_built_execution_prompt(self) -> None:
        import os
        import pwd

        class CaptureSupervisor:
            def __init__(self) -> None:
                self.argv = None

            def run(self, argv, **kwargs):
                self.argv = tuple(argv)
                return ProcessResult("completed", 0, 1, 1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            (root / "logs").mkdir()
            owner_user = pwd.getpwuid(os.geteuid()).pw_name
            runner = OMPRunner(omp_binary=Path("/absolute/omp"))
            capture = CaptureSupervisor()
            runner.supervisor = capture
            runner.run(
                {
                    "id": "job-id",
                    "owner_user": owner_user,
                    "workspace_path": str(root),
                    "timeout_seconds": 10,
                    "prompt": "user prompt",
                    "execution_prompt": "history plus user prompt",
                }
            )
            self.assertEqual(capture.argv[-1], "history plus user prompt")


    def test_timeout_terminates_process_group_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = (
                "import pathlib,subprocess,sys,time;"
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
                "pathlib.Path('child.pid').write_text(str(p.pid));"
                "time.sleep(30)"
            )
            supervisor = ProcessSupervisor()
            result = supervisor.run(
                [sys.executable, "-c", code],
                cwd=root,
                stdout_path=root / "logs/stdout.log",
                stderr_path=root / "logs/stderr.log",
                timeout_seconds=1,
                env={"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"},
            )
            self.assertEqual(result.state, "failed")
            self.assertEqual(result.error, "job timeout exceeded")
            child_pid = int((root / "child.pid").read_text(encoding="utf-8"))
            for _ in range(40):
                if not Path(f"/proc/{child_pid}").exists():
                    break
                time.sleep(0.05)
            self.assertFalse(Path(f"/proc/{child_pid}").exists())


class WorkerTests(unittest.TestCase):
    def test_worker_claims_only_configured_owner_and_finishes_job(self) -> None:
        class FakeRunner:
            def run(self, job, *, cancel_event, on_started):
                on_started(123, 123)
                return ProcessResult("completed", 0, 123, 123)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "remote.sqlite3")
            database.initialize()
            device_id = database.insert_device(owner_user="tomi", token_hash="hash")
            job = database.create_job(
                owner_user="tomi",
                device_id=device_id,
                runner_id="omp",
                project_id=None,
                prompt="worker",
                workspace_path=str(root),
                timeout_seconds=10,
            )
            worker = Worker(
                database=database,
                owner_user="tomi",
                runner=FakeRunner(),
                state_path=root / "state",
            )
            self.assertTrue(worker.run_once())
            finished = database.get_owned_job(job["id"], "tomi")
            self.assertEqual(finished["state"], "completed")
            self.assertEqual(finished["process_group_id"], 123)
            self.assertFalse(worker.run_once())


    def test_worker_stops_running_job_after_cancel_request(self) -> None:
        started = threading.Event()

        class CancellableRunner:
            def run(self, job, *, cancel_event, on_started):
                started.set()
                while not cancel_event.wait(0.05):
                    pass
                return ProcessResult("cancelled", -15, 1, 1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "remote.sqlite3")
            database.initialize()
            device_id = database.insert_device(owner_user="tomi", token_hash="hash")
            job = database.create_job(
                owner_user="tomi",
                device_id=device_id,
                runner_id="omp",
                project_id=None,
                prompt="cancel running",
                workspace_path=str(root),
                timeout_seconds=10,
            )
            worker = Worker(
                database=database,
                owner_user="tomi",
                runner=CancellableRunner(),
                state_path=root / "state",
            )
            thread = threading.Thread(target=worker.run_once)
            thread.start()
            self.assertTrue(started.wait(2.0))
            database.cancel_owned_job(job["id"], "tomi")
            thread.join(3.0)
            self.assertFalse(thread.is_alive())
            finished = database.get_owned_job(job["id"], "tomi")
            self.assertEqual(finished["state"], "cancelled")
    def test_worker_reconciles_interrupted_running_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "remote.sqlite3")
            database.initialize()
            device_id = database.insert_device(owner_user="tomi", token_hash="hash")
            job = database.create_job(
                owner_user="tomi",
                device_id=device_id,
                runner_id="omp",
                project_id=None,
                prompt="interrupted",
                workspace_path=str(root),
                timeout_seconds=10,
            )
            self.assertEqual(database.claim_next_job("tomi")["id"], job["id"])
            worker = Worker(
                database=database,
                owner_user="tomi",
                runner=object(),
                state_path=root / "state",
            )
            self.assertEqual(worker.reconcile_on_startup(), 1)
            reconciled = database.get_owned_job(job["id"], "tomi")
            self.assertEqual(reconciled["state"], "failed")
            self.assertEqual(reconciled["error"], "worker restarted before job completion")
if __name__ == "__main__":
    unittest.main()
