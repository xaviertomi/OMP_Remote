# OMP Remote — TODO

This file is the execution plan and progress tracker.

Update it continuously:
- check completed items;
- add work discovered during implementation;
- record important architecture decisions;
- do not duplicate permanent project rules from `.omp/RULES.md`;
- do not duplicate background architecture from `.omp/AGENTS.md`.

## Current state

**Current phase:** Phase 8 — Native Android application
**Core status:** TOMI SERVICES DEPLOYED; PUBLIC HTTPS NOT CONFIGURED
**Android status:** DEBUG APK 0.5 BUILT; PHONE INSTALL/RELEASE APK PENDING
**Emergency kill status:** EMERGENCY SERVICE RUNNING; END-TO-END PRODUCTION TEST PENDING
**Initial Unix users:** `tomi`, `draz`
**Audit status:** PARTIAL — `tomi` findings recorded; `draz` is explicitly deferred; firewall and project-access checks remain open.
**Audit records:** `docs/host-audit.md`, `docs/architecture.md`
**Active Unix scope:** `tomi` only for the current local implementation; `draz` remains deferred.
**Backend records:** `server/omp_remote/`, `server/tests/test_backend.py`, `.gitignore`, `server/.venv/`
**Verification:** 26 backend tests pass; conversation creation, second message in same owner-scoped conversation, bounded OMP context, migration, isolation, error payload, queued/running job cancellation, output discovery in writable job workspace and execution_prompt runner tests pass. APK 0.5 compiles with server-owned conversation selector, multi-turn composer directly below dialogue, composer-targeted IME scrolling without global-bottom jump, visible current-job cancel button, namespace-isolated persistent cache and versioned artifact metadata. Conversation turns are now individual accessible collapsible cards; tapping a tour header expands/reduces its message. File listing discovers existing output/workspace files and Android renders rows/count instead of raw HTTP JSON. Final release reviewer accepted conversation/isolation/emergency checks; indulgent designer score: 86/100. Static conversation/cache/emergency contracts and shell syntax checks pass.
**Deployment preparation:** `scripts/repair-tomi-install.sh` is shell-validated and now publishes `omp-remote-v0.5.apk` plus the stable bootstrap copy. Execution requires a local root shell.
- Version source: `server/omp_remote/version.py` (`VERSION_CODE=5`, `VERSION_NAME=0.5`); the Android Gradle build reads this source directly and update metadata uses the same values.
**Deployment verification:** Core units were previously active and API health returned HTTP 200. Rerun `repair-tomi-install.sh` before the next production job and APK publication. Apache, SSH, Tailscale, Docker, and Glances remain outside this change.

## Phase 0 evidence

- Host baseline and existing-service inventory recorded without production changes.
- Existing Apache/Glances/Tailscale/SSH/Docker/Minecraft/OMP-broker boundaries recorded.
- OMP 17.3.7 harmless `--print --no-session --no-tools` invocation verified as `tomi`.
- No OMP profile exists in the empty `/home/draz`; execution as `draz` requires root-assisted testing.

# Phase 0 — Audit and architecture decision

- [x] Identify OS, filesystem, systemd, CPU/RAM/disk and relevant mount points.
- [ ] Verify `tomi` and `draz`: `tomi` is audited; `draz` is deferred by the current scope, and root-assisted ACL/filesystem checks remain open.
- [ ] Audit reverse proxy, DDNS, TLS, listening ports, firewall, hosted websites, VPN and SSH.
- [ ] Locate OMP, record version, verify supported non-interactive invocation from official documentation.
- [ ] Audit OMP authentication/configuration separately for `tomi` and `draz` without exposing credentials (`tomi` recorded; `draz` deferred).
- [x] Test harmless non-interactive OMP invocation as `tomi` (draz deferred by the current scope).
- [ ] Observe OMP child-process behavior and determine clean cancellation strategy.
- [ ] Identify candidate allowlisted projects and per-user access (no candidates found; per-user access still requires root-assisted verification).
- [x] Determine safe workspace strategy.
- [x] Record findings in `docs/host-audit.md`.
- [x] Record architecture decisions.

**Gate:** complete audit before production-facing host changes.

# Phase 1 — Backend foundation

- [x] Finalize repository structure, `.gitignore`, isolated Python environment and reproducible dependencies.
- [x] Ensure credentials, runtime data, signing secrets and outputs are excluded from Git.
- [x] Implement configuration/secrets handling and dev/test separation.
- [x] Implement SQLite schema/migrations.
- [x] Job includes immutable `owner_user`, originating `device_id`, timestamps, state and execution metadata.
- [x] Implement revocable device/profile credentials bound to allowlisted Unix accounts.
- [x] Keep emergency authorization separate.
- [x] Implement `/api/v1/health`, authentication, identity resolution and ownership-scoped authorization.
- [x] Add automated tests.

**Gate:** backend tests pass before real OMP execution.

# Phase 2 — Queue, workers and OMP runner
**Local implementation evidence:** SQLite atomic claims, per-owner worker, argv-only OMP runner, process groups, UUID workspaces, timeout, cancellation, descendant termination, and metadata capture are implemented and covered by local tests.
**Gate evidence:** Local `tomi` submit → queued job → OMP runner → stdout/stderr capture → terminal `completed` was exercised with exit code 0 and recorded PID/process-group metadata. Production integration remains gated by unresolved root-assisted `draz` and firewall/project audits.

- [x] Persistent SQLite-backed queue with immutable owner.
- [x] One concurrent job per Unix user by default; configurable per-user/global limits.
- [x] Implement server-side allowlisted runner registry and `omp` runner.
- [x] Create UUID workspace per job with controlled input/workspace/output/log areas.
- [x] Implement server-side project IDs filtered by authenticated user.
- [x] Ensure client cannot select host paths or Unix identity.
- [ ] Implement `worker@tomi` and `worker@draz` or a demonstrably safer equivalent.
- [x] Each worker claims only jobs matching its configured Unix identity.
- [ ] Verify no cross-user HOME/SSH/OMP credential leakage.
- [x] Invoke OMP via argv/no shell interpolation with explicit cwd.
- [x] Capture stdout, stderr, exit code, timestamps and process/cgroup metadata.
- [x] Implement timeout, normal cancellation and descendant termination.
- [x] Simulate worker crash and reconcile interrupted jobs.

**Gate:** submit → OMP → capture output → complete works locally before public integration. [x]

# Phase 3 — Files and quotas
**Local implementation evidence:** Authenticated base64 input upload, server-generated file IDs, owner-scoped listing/download, SHA-256 integrity checks, per-file/count/job/total limits, output discovery, canonical workspace checks, traversal denial, symlink denial, and safe old-job cleanup are implemented and tested. Disk-wide policy remains deployment-configurable.
- [x] Authenticated upload with request/file/count limits.
- [x] Prevent traversal, unsafe names, symlink escape and archive extraction attacks.
- [x] Register outputs using server-side file IDs.
- [x] Secure file listing/download by `job_id + file_id`.
- [x] Prevent arbitrary filesystem paths.
- [x] Configure prompt, queue, runtime and disk limits.
- [x] Implement safe old-job cleanup.

# Phase 4 — Unix isolation and systemd
**Local implementation evidence:** API, worker, and emergency systemd templates plus compatible hardening are present in `systemd/`; no unit or protected runtime directory was installed.

- [ ] Keep API/emergency accounts separate from job owners where useful.
- [ ] Do not erase the `tomi`/`draz` Unix permission boundary with a shared execution account.
- [ ] Grant no new sudo/systemd/root/global Docker privileges.
- [ ] Verify cross-user filesystem denial.
- [ ] Create protected runtime/config directories.
- [x] Create `omp-remote-api.service` template.
- [ ] Create `omp-remote-worker@tomi.service` and `omp-remote-worker@draz.service` unless audit justifies safer equivalent.
- [ ] Keep each worker and descendants inside its OMP Remote cgroup boundary.
- [ ] Add compatible systemd hardening and test restart/reboot.
- [x] Implement persistent root-controlled ENABLED/DISABLED state.
- [x] API/workers fail closed while DISABLED, including after reboot.

# Phase 5 — Emergency subsystem
**Local implementation evidence:** Separate emergency credentials, two-route emergency app, atomic DISABLED state, exact recorded process-group termination, workspace quarantine, root-only local administration, and no remote enable route are implemented and tested. Production cgroup/main-API stop verification remains open.

- [x] Create independent minimal `omp-remote-emergency.service` template.
- [x] Implement independently revocable emergency authorization.
- [x] Expose only `POST /emergency/kill` and `GET /emergency/status`.
- [x] Confirm no HTTP enable route exists.
- [x] Implement smallest root-owned kill operation required; no general sudo.
- [ ] Kill sequence: authenticate → DISABLED → stop all OMP Remote workers/descendants → verify empty → stop main API → record incident → retain emergency status.
- [x] Never kill by executable name or Unix username.
- [x] Quarantine compromised workspace rather than deleting it in kill path.
- [x] Implement local `sudo omp-remote disable`, `sudo omp-remote enable`, `omp-remote status`.
- [x] Ensure enable requires local root and cannot be invoked by API/worker/emergency web service.
- [ ] Test emergency kill with harmless synthetic child processes.
- [ ] Verify hosted websites, VPN, SSH and unrelated services/processes remain running.
- [ ] Verify manual OMP processes of `tomi` and `draz` outside OMP Remote remain running.
- [ ] Verify local root enable restores service.

**Gate:** server-side emergency tests pass before Android emergency control ships.

# Phase 6 — HTTPS/reverse proxy

- [ ] Back up only configurations being changed.
- [ ] Reuse existing reverse proxy/TLS when safe.
- [ ] Bind internal services locally/private where practical.
- [ ] Add isolated OMP Remote routes, body/time limits and rate limiting.
- [ ] Do not add CORS unless native Android actually requires it.
- [ ] Validate proxy config before reload.
- [ ] Verify existing sites, VPN, SSH and public OMP Remote HTTPS.

# Phase 7 — Complete API
**Local API evidence:** Health, projects/runners registry, asynchronous jobs, owner-scoped conversations/messages, bounded conversation context, job detail/cancel for queued and running jobs, secure file list/download, and separate emergency status/kill routes are implemented and covered by backend tests. Production HTTPS integration remains open.

- [x] `GET /api/v1/conversations`
- [x] `POST /api/v1/conversations`
- [x] `GET /api/v1/conversations/{conversation_id}`
- [x] `POST /api/v1/conversations/{conversation_id}/messages`
- [x] `GET /api/v1/jobs`
- [x] `GET /api/v1/jobs/{job_id}`
- [x] `POST /api/v1/jobs/{job_id}/cancel`
- [x] `GET /api/v1/jobs/{job_id}/logs`
- [x] `GET /api/v1/jobs/{job_id}/files`
- [x] `GET /api/v1/jobs/{job_id}/files/{file_id}`
- [x] `POST /emergency/kill`
- [x] `GET /emergency/status`
- [x] Test missing/invalid/revoked tokens.
- [x] Test `tomi`/`draz` ownership creation and reject identity injection.
- [x] Test cross-user list/read/cancel/download denial and metadata non-leakage.
- [ ] Test project filtering, emergency authorization separation, traversal, SQL/shell payloads, oversized input, queue limits, unknown IDs, timeout, races and crash recovery.

# Phase 8 — Native Android application
**Android evidence:** Kotlin project, HTTPS client, Keystore credential separation, tomi profile display, exact three-menu navigation (`Conversation`, `Application`, `Kill`), server-owned conversation selector, namespace-isolated persistent conversation cache, composer directly below active dialogue with IME handling, selectable conversations, live conversation/job polling, visible current-job cancellation, jobs/files/logs, targeted cancellation, authenticated update metadata/download, PackageInstaller flow, and private bootstrap download are implemented. Debug APK 0.5 is built; phone install, release signing, and physical validation remain pending.

- [x] Create supported Kotlin Android project with reproducible Gradle configuration.
- [x] Implement HTTPS client and clean connection error handling.
- [ ] Implement device/profile enrollment bound server-side to `tomi` or `draz`.
- [ ] Implement credential revocation.
- [x] Keep normal and emergency credentials independent.
- [ ] Support multiple independently enrolled profiles on one phone if practical.
- [x] Protect credentials with Android Keystore-backed storage.
- [x] Never embed secrets in APK resources/source or URLs/logs.
- [x] Implement exactly three fixed bottom menus: `Conversation`, `Application`, `Kill`.
- [x] Keep inactive menu bodies `GONE` and outside the active scroll surface/accessibility tree.
- [x] Keep files, upload, download/share, logs, diagnostics, history, prompt, and tracked job in `Conversation`.
- [x] Keep `Application` empty with the required French placeholder.
- [x] Keep targeted job cancellation separate from confirmed emergency kill.
- [x] Keep settings and versioned update flow in the header secondary action.
- [x] Clearly display active Unix profile; no free-form run-as selector.
- [x] Add clearly separated `KILL OMP REMOTE` with explicit scope confirmation.
- [x] After kill, use emergency status and display local-root reactivation requirement.
- [x] Confirm no Enable UI/code path exists.
- [x] Build versioned debug APK 0.5/code 5 at `android/app/build/outputs/apk/debug/omp-remote-v0.5.apk`; size `918316` bytes; SHA-256 `17c404f89b054d6fe094b2f5c0f708cddccd939089b15fa0b29b257c665e327c`. Fixed startup crash, restored HTTPS fields, added IME composer targeting, server-owned multi-turn conversations, bounded OMP context, owner-scoped cache isolation, JSON message persistence, conversation error display, queued/running job cancellation, on-demand output discovery, human-readable file listing/document saving and collapsible turn cards with persisted expansion state.
- [ ] Build release APK with deployment signing key.
- [x] Keep signing secrets outside Git.
- [ ] Test on device/ADB if available; current environment has no physical/emulator verification.

# Phase 9 — End-to-end validation

- [ ] Android HTTPS connection as `tomi`.
- [ ] Android HTTPS connection as `draz` with separate credential/profile.
- [ ] If both profiles are on one phone, switching profile switches credentials and resulting owner.
- [ ] Submit harmless jobs and verify actual process UID/GID/groups.
- [ ] Verify cross-user API/files/logs isolation.
- [ ] Verify per-user project visibility and OMP/provider config isolation.
- [ ] Verify simultaneous jobs remain in separate worker/cgroup boundaries.
- [ ] Verify cancelling one user's job cannot terminate the other's.
- [ ] Test normal cancel, timeout, worker/API restart, host reboot and reconciliation.
- [ ] Test Android emergency kill, DISABLED state, all OMP Remote descendants stopped, main API stopped, emergency status alive.
- [ ] Verify unrelated cluster services remain healthy.
- [ ] Verify remote enable impossible.
- [ ] Verify local root enable and subsequent harmless job.

# Phase 10 — Optional notifications

- [ ] Decide whether email adds enough value.
- [ ] Prefer existing safe SMTP/provider configuration.
- [ ] Do not deploy a full mail server solely for this.
- [ ] Store mail credentials as secrets.
- [ ] Send completion/failure notification without large attachments.
- [ ] Prefer link/reference to job/app for results.

# Phase 11 — Documentation and release

- [ ] `README.md`
- [ ] `docs/host-audit.md`
- [ ] `docs/architecture.md`
- [ ] `docs/security.md`
- [ ] `docs/administration.md`
- [ ] `docs/android-installation.md`
- [ ] `docs/recovery.md`
- [ ] Document APK build/location, ADB/manual install, enrollment, first job, download, cancel, safe kill test, local re-enable, revocation and update.
- [ ] Document status/start/stop/disable/enable/logs/incident inspection/SQLite backup/workspace cleanup/token rotation/device revocation/server+Android update/TLS renewal.
- [ ] Add a short executable `WHAT TO DO IF OMP REMOTE IS COMPROMISED` section.

# Final acceptance criteria

- [ ] Android submits asynchronous OMP jobs over HTTPS.
- [ ] Every credential is server-bound to one Unix user.
- [ ] `tomi` jobs execute as `tomi`; `draz` jobs execute as `draz`.
- [ ] No Android/API field can request arbitrary Unix impersonation.
- [ ] Job/history/log/file authorization is isolated between users.
- [ ] OMP Remote never grants more host privileges than the existing Unix account.
- [ ] Job state persists.
- [ ] OMP executes in isolated per-job workspaces.
- [ ] Original project data is protected from automatic modification.
- [ ] Logs/outputs are securely retrievable.
- [ ] Cancellation terminates only the selected OMP Remote job tree.
- [ ] Emergency kill terminates all OMP Remote execution and main API, but not unrelated/manual processes or services.
- [ ] Android/HTTP cannot re-enable; local root can inspect/re-enable.
- [ ] Credentials are absent from Git/logs/workspaces.
- [ ] Installable Android APK exists.
- [ ] Installation/recovery documentation is tested.
- [ ] Future allowlisted runners can reuse the job/API/output model without Android redesign.
