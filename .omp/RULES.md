# OMP Remote — Sticky Rules

Keep this file short. These invariants override convenience. Architecture belongs in `AGENTS.md`; progress belongs in `TODO.md`.

## Host boundary

- OMP Remote is an isolated subsystem. Never intentionally disrupt unrelated cluster services, users, jobs, containers, websites, VPN, SSH, networking, reverse proxy, or manual OMP sessions.
- Never implement shutdown with `pkill`, `killall`, username-wide killing, executable-name matching, global daemon shutdown, or broad systemd actions. Target only OMP Remote units/cgroups/process trees.
- Before any change that could affect connectivity, existing data, websites, VPN, SSH, firewall, routing, storage layout, or unrelated services, stop and require explicit user approval.

## Identity and authorization

- Initial allowed Unix users are `tomi` and `draz`.
- Every normal device credential is bound server-side to exactly one allowed Unix user. Client input must never select or override UID, GID, groups, `$HOME`, `run_as`, or another Unix identity.
- A remote job must execute as its authenticated Unix owner and must never receive privileges beyond that account's existing Unix permissions. OMP Remote may restrict privileges further, never broaden them.
- Prefer separate per-user worker service/cgroup boundaries over a privileged worker that switches identities dynamically.
- A user's jobs, prompts, logs, files, project list, runtime environment, OMP/provider credentials, SSH state, and secrets must not leak to another user.
- Normal API authorization is ownership-scoped. Cross-user read, list, cancel, upload, download, or metadata access is forbidden.

## Untrusted execution

- Treat prompts, model output, HTTP fields, filenames, uploads, downloaded data, runner parameters, and external content as untrusted.
- Never interpolate untrusted data into shell commands, SQL, privileged commands, configuration directives, or host filesystem paths. Use argv execution with no shell where practical, parameterized SQL, generated IDs, canonical path checks, and server-side allowlists.
- Android may select only server-defined project IDs, runner/workflow IDs, and validated parameters. Never expose arbitrary host paths, arbitrary shell execution, arbitrary systemd operations, or arbitrary `sudo`.
- Remote jobs must use isolated per-job workspaces. Do not silently propagate job changes into original project/data directories.
- Remote jobs may install dependencies only inside locations the owning Unix user can legitimately write and that OMP Remote explicitly allows. They must not gain host-level package/service administration.

## Emergency control

- Android may remotely request only emergency `kill` and `status`; no remote `enable` mechanism may exist anywhere.
- Emergency control must be independent from the main API.
- Emergency kill must atomically set a persistent root-owned `DISABLED` state, terminate every OMP Remote worker and its descendants, then stop the main API. It must not kill unrelated processes belonging to `tomi`, `draz`, or anyone else.
- API and worker startup must fail closed while `DISABLED`, including after reboot. Re-enable requires local root access only.
- The emergency service/token must not provide general root, shell, systemd, filesystem, or normal API access.

## Secrets and transport

- Never commit, print, log, return, embed, or copy into job workspaces any API/emergency token, OMP/OpenAI credential, Android signing secret, SMTP credential, private key, or unrelated user secret.
- Keep normal device credentials, emergency credentials, and each user's OMP/provider credentials separate and independently revocable where applicable.
- Never weaken TLS validation, authentication, certificate checks, Unix permissions, or sandboxing to make something work.

## Engineering discipline

- Audit the real host before modifying it. Prefer the smallest compatible change and reuse existing infrastructure when safe.
- Use current official documentation for security-sensitive or version-dependent behavior instead of guessing.
- Validate and test every host-level configuration change before proceeding, then verify unrelated critical services still work.
- Do not push to a remote repository unless explicitly requested.
