# OMP Remote architecture decisions

These decisions are based on the initial host audit. They are implementation constraints, not a production deployment.

## Service boundaries

- Keep OMP Remote separate from Apache, Glances, Tailscale, SSH, Docker, the Minecraft workload, and the existing `sut` OMP auth broker.
- Development services bind to localhost only.
- Production deployment will use separate units for the API, one worker per Unix owner, and the emergency service. No unit is installed during Phase 0.
- The emergency service remains independent from the main API and exposes only kill/status behavior. There is no remote enable path.

## Identity

- Authentication resolves a server-side device credential to exactly one allowlisted Unix owner (`tomi` or `draz`).
- Client fields such as `run_as`, UID, GID, groups, HOME, or host paths are never accepted as execution controls.
- Workers run directly as their configured Unix owner; the API never dynamically switches identity.
- Existing `docker`/`lxd` membership is not used to broaden worker privileges.

## Temporary deployment scope

- The current local/default configuration enables only `tomi`, as explicitly requested for the initial implementation.
- `draz` remains part of the target architecture but is deferred; no `draz` credential, worker, OMP profile, or production permission is created by this scope.
- Re-enabling `draz` requires an explicit configuration change plus the root-assisted identity, credential, OMP, and filesystem audit.

## Queue and runner

- SQLite is the V1 persistence boundary.
- Jobs have immutable owner/device identity and server-generated UUIDs.
- One concurrent job per Unix owner is the default; per-owner and global limits are configurable.
- Projects are server-defined IDs filtered by owner; the phone never sends source paths.
- The runner interface is allowlisted; V1 has an argv-based OMP runner and no arbitrary shell runner.
- Job submission is asynchronous. The API never waits for OMP completion.

- Conversations are server-owned, owner-scoped containers for ordered user messages and their asynchronous OMP jobs.
- `POST /api/v1/conversations` creates the first conversation turn; `POST /api/v1/conversations/{id}/messages` appends later turns.
- Each turn keeps the user prompt separate from the bounded `execution_prompt` sent to OMP. Previous prompts and available stdout form the logical context; the runner still uses argv-only OMP execution.
- Android selects conversations, while jobs remain technical details for state, logs, files, and targeted cancellation.

## Workspaces and process control

- Each job receives a generated workspace with separate input, writable workspace, output, logs, and metadata directories.
- Original projects are read-only inputs and are never silently modified.
- Production workspaces will live under a root-controlled location after provisioning; development uses disposable temporary directories.
- Every worker launch creates an explicit process-group/cgroup boundary. Cancellation and timeout target that boundary, never a username or executable name.
- Emergency termination quarantines the affected workspace rather than attempting to reverse arbitrary process changes.

## Files and quotas

- Inputs are uploaded through an authenticated owner-scoped route using server-generated file IDs.
- File names are single path components; canonical-path, regular-file, symlink, size, count, and SHA-256 checks run before retrieval.
- Files remain under the generated job workspace. No client-supplied filesystem path is accepted.
- Disk-wide quotas, output discovery, and cleanup require the Phase 3 deployment policy.
- Public job responses omit workspace paths and process identifiers; those remain server-side execution metadata.

## Transport and deployment gate

- The current host has Apache HTTP on port 80 but no discovered TLS virtual host or certificate directory. Existing Apache and firewall configuration must be audited with root before adding HTTPS routes.
- No reverse-proxy, firewall, port, DNS/DDNS, or systemd changes are part of the local implementation phase.
- The API and workers fail closed while a persistent root-controlled `DISABLED` state is set.

## Security residuals

- Membership in `docker` is already a high-impact host capability for both initial users; OMP Remote must not rely on it as a sandbox.
- Provider/OMP credentials are per-user and must remain isolated; the audit has not yet validated `draz` credentials.
- Root-assisted audit and deployment review are required before production exposure.
