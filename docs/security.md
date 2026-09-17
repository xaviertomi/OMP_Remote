# OMP Remote security model

## Identity

Normal Bearer credentials are random device secrets stored only as SHA-256 hashes in SQLite. Each credential is bound server-side to one configured Unix owner. The current default configuration allows `tomi` only. The client cannot provide `run_as`, UID, GID, HOME, host paths, or another identity.

Emergency credentials use a separate database table and authentication path. They do not authenticate normal API routes. The emergency surface contains only status and kill; there is no HTTP enable endpoint.

## Execution

The worker is configured for one Unix owner and claims only that owner's queued jobs. OMP launches use an argv tuple, no shell, an explicit workspace, a minimal environment, and a new process group. Timeout and cancellation terminate the recorded process group. Systemd templates use `KillMode=control-group` for production descendants.

The API, workers, and emergency service are separate processes. The emergency controller writes `DISABLED` atomically before terminating recorded jobs. API and worker startup fail closed when the persistent state is not `ENABLED`.

## Files

Every job gets a generated workspace with input, writable workspace, output, logs, and metadata directories. Client uploads use generated file IDs. File names are single components; canonical path, regular-file, symlink, size, count, and SHA-256 checks protect upload and download. Public job responses do not expose host workspace paths or process IDs.

Original project directories are never silently modified. Output discovery rejects symlink and non-regular files.

## Secrets

- Provider and OMP profile files remain in the Unix user's home and are never copied to jobs.
- Android normal and emergency credentials are stored separately using Android Keystore.
- Signing keys, API credentials, emergency tokens, databases, outputs, and runtime state are excluded by `.gitignore`.
- Tokens must not be printed except during deliberate local issuance.

## Residual risks and gates

- Both `tomi` and deferred `draz` are members of the existing `docker` group; OMP Remote must not use Docker as a sandbox.
- Root-assisted firewall and project-permission audits remain open.
- `draz` OMP/provider configuration and execution are explicitly deferred.
- No production TLS/reverse-proxy route has been installed.
- Disk-wide cleanup policy and output retention require an operational decision.
- Android APK build and physical-device tests require Android SDK/Gradle/device provisioning.
- Process-group control is the local fallback; production must verify systemd cgroup containment and descendant cleanup.
