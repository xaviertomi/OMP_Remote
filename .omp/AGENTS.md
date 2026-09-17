# OMP Remote — Project Context

## Mission

Build, install, test, and document **OMP Remote** on this Linux cluster.

OMP Remote lets authenticated Android clients submit controlled asynchronous jobs over HTTPS, initially to Oh My Pi (OMP), monitor them, cancel them, inspect logs, and download generated files.

The initial Unix users are `tomi` and `draz`. Every Android profile/device credential is bound server-side to exactly one allowed Unix account. Jobs execute under the Unix identity of the authenticated profile owner. OMP Remote may further restrict that identity for sandboxing, but must never grant a job more privileges than the corresponding Unix user already has.

The cluster already has unrelated services that must remain independent from this project: hosted websites, a reverse proxy, VPN, SSH, networking, and potentially other workloads. Preserve them.

## Product scope

V1 must provide:

- authenticated HTTPS API;
- per-user device identity and authorization;
- Unix-user-scoped execution for `tomi` and `draz`;
- persistent job queue and history;
- one concurrent OMP job per user by default;
- isolated per-job workspace;
- stdout/stderr/status tracking;
- cancellation and timeout;
- secure upload/download;
- native Android client;
- independent remote emergency kill with revocable device-level authorization;
- local-root-only re-enable;
- install/admin/security documentation.

Email notification is optional and comes only after the core system is validated.

## Architecture

Target shape; adapt details after auditing the real host:

```text
Android app
   |
 HTTPS
   |
existing DDNS + existing reverse proxy
   |                         |
main API                 emergency API
   |                         |
 SQLite                  minimal kill path
   |
user-scoped dispatch
   |             |
worker@tomi   worker@draz
   |             |
UID tomi      UID draz
   \             /
    runner abstraction
          |
    OMP runner (V1)
          |
 isolated job workspace
```

Keep OMP Remote components separate from existing services. Prefer explicit units such as:

```text
omp-remote-api.service
omp-remote-worker@tomi.service
omp-remote-worker@draz.service
omp-remote-emergency.service
```

Reuse the existing reverse proxy and TLS setup when safe instead of installing competing infrastructure.

## Execution model

The API must never wait for OMP to finish. Job submission returns a server-generated UUID immediately. Every job has an immutable server-side owner derived from the authenticated device credential; store at least `owner_user` and preferably `device_id`. Never trust a client-supplied username/UID/GID as the execution identity.

Minimum job states:

```text
queued
running
completed
failed
cancelled
aborted_emergency
```

Use SQLite for V1 unless the observed workload proves it inadequate. Do not add Redis, RabbitMQ, Celery, Kubernetes, or similar infrastructure without a demonstrated need.

Start with one concurrent remote job per Unix user. Make per-user and global concurrency limits configurable later.

## Runner abstraction

Do not couple queue/API/Android code directly to `omp -p`.

Implement a small server-side runner interface. V1 provides `omp`; future allowlisted runners may include Snakemake, bcftools, samtools, PLINK/PLINK2, R, Python, or local bioinformatics workflows.

All runners reuse the same job lifecycle, queue, logs, process ownership, timeout/cancellation, output handling, quotas, and emergency kill boundary.

The client selects a configured runner/workflow ID and validated parameters. Never expose an arbitrary-shell runner to the Android client.

## Job workspaces

Use a server-generated path such as:

```text
/srv/omp-remote/jobs/<UUID>/
  input/
  workspace/
  output/
  logs/
  metadata/
```

Original projects are server-side allowlisted by stable project ID. The phone never supplies host paths. Project visibility/use is filtered by authenticated owner and remains subject to the real Unix permissions of that user's worker process.

Prefer original sources/data read-only for the remote worker. Create an isolated writable workspace using the simplest safe method supported by the audited host: Git clone/worktree, reflink/copy, filesystem snapshot, or another justified mechanism.

Changes produced by OMP remain in the job workspace/output by default. Do not silently apply them to original projects.

Workspace isolation is the rollback strategy: after emergency termination, quarantine or discard the compromised workspace instead of trying to reverse arbitrary shell actions retrospectively.

## OMP execution

Audit the installed OMP version, its supported non-interactive invocation, authentication storage, and process behavior before implementation. Audit OMP authentication/configuration separately for `tomi` and `draz`; never assume one user's provider credentials may be copied or shared with the other.

Invoke OMP through an argv-based process API with no shell interpolation and an explicit job workspace as cwd. Capture exit code, timestamps, stdout/stderr, PID/process-group information, and timeout state.

Cancellation must terminate the job process tree, not only the parent PID. Prefer process groups/cgroups/systemd ownership rather than process-name matching.

Assume a compromised OMP process can access anything its Unix account can access. A `tomi` job therefore runs as `tomi`, and a `draz` job runs as `draz`. Security comes primarily from Unix permissions plus OMP Remote workspace/process isolation, not from trusting prompts or model behavior.

## Unix identity and host privileges

Do not execute all users' jobs through one shared Unix execution account. Prefer a dedicated OMP Remote worker service instance per allowed Unix user, configured to run directly as that user:

```text
omp-remote-worker@tomi.service -> User=tomi
omp-remote-worker@draz.service -> User=draz
```

This avoids giving a shared worker privilege to switch UID dynamically. The API/dispatcher must not need root merely to choose the execution identity.

The API only queues jobs with server-side ownership. Each per-user worker may claim only jobs whose `owner_user` matches its configured identity. Preserve only the intended user's required UID/GID/supplementary groups and environment. Never leak another user's `$HOME`, SSH agent, credentials, OMP sessions, configuration, or secrets into a job.

Remote jobs may install dependencies inside locations their Unix user can legitimately write and that OMP Remote policy allows. OMP Remote must not grant `tomi` or `draz` new sudo, root, systemd, Docker, or host-administration capabilities.

## Main API

Use a versioned REST API. Suggested V1 contract:

```text
GET    /api/v1/health
GET    /api/v1/projects
GET    /api/v1/runners
POST   /api/v1/jobs
GET    /api/v1/jobs
GET    /api/v1/jobs/{job_id}
POST   /api/v1/jobs/{job_id}/cancel
GET    /api/v1/jobs/{job_id}/logs
GET    /api/v1/jobs/{job_id}/files
GET    /api/v1/jobs/{job_id}/files/{file_id}
```

Use server-side IDs for projects, runners, jobs, and output files. Validate request schemas and impose explicit limits on prompt length, upload count/size, queue length, job runtime, concurrency, and disk use.

## Authentication, identity, and secrets

V1 is a small multi-user system: initially `tomi` and `draz`. Prefer simple strong revocable per-device/per-profile credentials over unnecessary OAuth complexity.

Each normal device credential is bound server-side to exactly one allowed Unix user. Authentication determines identity; Android does not. Never trust `user`, `uid`, `gid`, `run_as`, `$HOME`, or equivalent identity fields supplied by the client.

Authorization is ownership-scoped. A normal credential may create jobs only for its bound user and may list/read/cancel/upload/download only that user's jobs and files.

Maintain emergency authorization separately from normal per-user credentials. Prefer revocable device-scoped emergency credentials/capabilities rather than one secret shared permanently by every phone. Emergency authorization grants only global OMP Remote kill/status and must not grant normal job access, shell/root access, or re-enable capability.

Keep secrets outside Git and logs with restrictive filesystem permissions. Keep `tomi` and `draz` OMP/provider credentials isolated from each other.

## Emergency subsystem

The emergency service is deliberately separate from the main API. Keep its code and privileges minimal.

Its remote surface should be only:

```text
POST /emergency/kill
GET  /emergency/status
```

No HTTP enable route exists.

Recommended kill semantics:

1. authenticate emergency authorization;
2. atomically mark OMP Remote `DISABLED` using root-controlled persistent state;
3. stop all OMP Remote per-user workers and their OMP Remote process/cgroup trees;
4. confirm no OMP Remote execution descendants remain;
5. stop the main API;
6. record minimal incident metadata outside the disposable job workspace;
7. keep emergency status available.

The kill action must not stop the global reverse proxy or any unrelated service.

Local administration should expose at least:

```text
sudo omp-remote disable
sudo omp-remote enable
omp-remote status
```

`enable` is local-root-only and should verify service/process state before restarting API and workers.

## Reverse proxy and network integration

Audit current DDNS, TLS, reverse proxy, ports, firewall, websites, VPN, and SSH before changing anything.

Bind application services to localhost/private interfaces when practical and expose them through narrowly scoped reverse-proxy routes. Validate proxy configuration before reload.

Do not alter router/NAT/firewall/global network configuration unless necessary. If a required change could affect existing connectivity, stop and ask the user before applying it.

## Android client

Android only; no PWA and no iOS work.

Prefer current officially supported Android tooling, Kotlin, and a maintainable UI stack such as Jetpack Compose when appropriate after checking current documentation.

Required features:

- server/device setup;
- display the Unix account/profile bound to the enrolled device;
- new job: project, runner, prompt/validated parameters, attachments;
- job list and detail;
- logs/status;
- result file download/open/share;
- normal cancel;
- settings;
- clearly separated emergency kill.

Use Android Keystore-backed secret storage according to current Android guidance. Normal and emergency credentials remain separate. Never embed credentials in source/resources.

Emergency kill requires a deliberate but quick confirmation. After kill, treat loss of the main API as expected and use emergency status to display `DISABLED`. The app must contain no enable action.

Provide a practical local provisioning/revocation flow:

```text
sudo omp-remote issue-device --user tomi
sudo omp-remote issue-device --user draz
sudo omp-remote revoke-device <id>
```

The Android app may support multiple enrolled profiles on the same phone, but each profile must use its own independently issued credential. Switching profile means switching credentials, never sending a mutable `run_as` field.

## Practical autonomy

During implementation, inspect and modify files, install required project dependencies, create services, build the Android APK, and use Web/API documentation as needed without asking for information that can be safely determined locally. Keep changes scoped to OMP Remote.

Prefer working code and tested integration over speculative infrastructure. Do not require a paid cloud service or app store. Produce an installable APK and document sideload/ADB installation.

## Repository layout

Prefer:

```text
omp-remote/
  .omp/
    AGENTS.md
    RULES.md
  TODO.md
  server/
  android/
  systemd/
  proxy/
  scripts/
  config/
  docs/
  README.md
```

## Development workflow

1. Audit before modifying the host.
2. Record important discovered facts and architecture decisions.
3. Implement the smallest complete increment.
4. Test it before proceeding.
5. For host-level changes, verify unrelated services afterwards.
6. Update `TODO.md` continuously.
7. Prefer current official documentation for version-sensitive implementation details.
8. Do not ask the user for facts that can be determined safely from local inspection.
9. Stop for user approval before destructive or connectivity-risking actions.

## Definition of done

Core:

```text
Android -> HTTPS API -> queued job -> OMP -> outputs -> Android download
```

Multi-user:

```text
tomi credential -> tomi-owned job -> process identity tomi
draz credential -> draz-owned job -> process identity draz
cross-user job/log/file access denied
no client-selected run-as identity
```

Emergency:

```text
Android KILL
 -> OMP Remote DISABLED
 -> all OMP Remote workers + descendants stop
 -> main API stops
 -> emergency status remains
 -> unrelated websites/VPN/SSH/services remain operational
 -> no remote re-enable
 -> local `sudo omp-remote enable` restores subsystem
```

Document residual risks explicitly.

## Current implementation plan

Keep repository-root `TODO.md` as the source of truth for execution progress.

@../TODO.md
