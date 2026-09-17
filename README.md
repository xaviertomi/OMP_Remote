# OMP Remote

OMP Remote is an authenticated asynchronous API and Android client for controlled OMP jobs.

## Current status

The local tomi-only backend is implemented and tested:

```text
HTTPS API -> SQLite queue -> per-owner worker -> OMP -> logs/files
```

The default local allowlist contains only `tomi` by explicit scope decision. `draz` remains deferred and is not provisioned.

Deployment is host-specific and requires an explicit local-root handoff; the repository does not enable public HTTPS or modify unrelated services.

## Local backend

Requirements: Python 3.12+. The server uses only Python standard-library modules.

```bash
server/.venv/bin/python -m unittest discover -s server/tests -v
PYTHONPATH=server server/.venv/bin/python -m omp_remote serve
```

The development API binds to `127.0.0.1:8099` by default. Configure it with environment variables:

- `OMP_REMOTE_DATA_DIR`
- `OMP_REMOTE_DB_PATH`
- `OMP_REMOTE_STATE_PATH`
- `OMP_REMOTE_WORKSPACE_ROOT`
- `OMP_REMOTE_ALLOWED_USERS` (defaults to `tomi`)
- `OMP_REMOTE_OMP_BINARY` is supplied to the worker command, not the API
- body, prompt, queue, file, job, and total-disk limits

Health is public at `/api/v1/health`. Normal API routes require a server-issued Bearer credential. Credentials are issued and revoked locally; no HTTP enrollment route exists.

## Local administration

The administration commands require local root except `status`:

```bash
sudo scripts/omp-remote issue-device --user tomi --label phone
sudo scripts/omp-remote revoke-device DEVICE_UUID
sudo scripts/omp-remote issue-emergency --label phone
sudo scripts/omp-remote revoke-emergency CREDENTIAL_UUID
sudo scripts/omp-remote disable
sudo scripts/omp-remote enable
scripts/omp-remote status
```

Tokens are displayed once to the local administrator. Never place them in Git, logs, issue reports, or job workspaces.

## Service templates

`systemd/` contains the reviewed API, worker, emergency, and gateway templates. The tomi-only installer/repair scripts install only these OMP Remote units and do not target `draz` or unrelated services.

## Android

The Kotlin Android project is under `android/`. It uses Android Keystore-backed storage for normal and emergency credentials and requires HTTPS. The current versioned debug APK is built at `android/app/build/outputs/apk/debug/omp-remote-v0.5.apk`; after rebuilding, publish it with the local-root repair script. The canonical server version is `server/omp_remote/version.py`, which the Gradle build reads directly for Android `versionCode`/`versionName`.

See:

- `docs/host-audit.md`
- `docs/architecture.md`
- `docs/security.md`
- `docs/administration.md`
- `docs/recovery.md`
- `docs/android-installation.md`
