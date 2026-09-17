# OMP Remote host audit

## Scope and privilege

This audit was performed from the project directory as Unix user `tomi` (UID 1000), not as root. No production service, firewall rule, network route, existing project, container, or system configuration was changed.

Root-only observations remain explicitly marked below. The audit did not read provider/API tokens or other credential values.

The current local implementation is intentionally scoped to `tomi` only. `draz` remains in the target architecture but is deferred; no `draz` credentials, worker, OMP profile, or production access is being created in this scope.

## Host baseline

- OS: Ubuntu 24.04.4 LTS (Noble)
- Kernel: Linux 6.8.0-106-generic x86_64
- CPU: 4 logical CPUs
- Memory: 15 GiB RAM, 4 GiB swap
- Root filesystem: ext4, 98 GiB total, 60 GiB available at audit time
- `/mnt/garage`: ext4, 37 GiB total, 34 GiB available
- `/mnt/warehouse`: ext4, 3.3 TiB total, 3.1 TiB available
- `/srv`: present and empty
- systemd: 255

The available disk space is sufficient for development, but production quotas and workspace retention still require explicit policy before deployment.

## Unix identities and privilege boundary

- `tomi`: UID/GID 1000, home `/home/tomi`, shell `/bin/bash`
- `draz`: UID/GID 1001, home `/home/draz`, shell `/bin/sh`
- `tomi` supplementary groups: `adm`, `cdrom`, `dip`, `plugdev`, `lxd`, `docker`, `dev`
- `draz` supplementary groups: `docker`, `dev`
- Neither user is in the `sudo` group.
- Non-interactive sudo from this session requires a password; no passwordless delegation was available.
- `/home/draz` is mode `750` and empty at audit time.
- `/home/tomi/.omp/agent/config.yml` is mode `600`, owned by `tomi:tomi`.

Both allowed users are members of `docker`. This is an existing, high-impact privilege boundary: Docker control is effectively host-administrative on this host. OMP Remote must not add privileges and must not use Docker as its worker isolation mechanism without a separate security review.

The `lxd` membership is present for `tomi` only and is also an existing elevated capability. OMP Remote workers must not inherit or use it for privilege changes.

Because root credentials were unavailable, this audit did not attempt to change ownership, create system users, inspect protected sudo policy, or launch a process as `draz`.

## Existing services and network

Running services relevant to integration:

- Apache 2.4.58, default virtual host on `*:80`, document root `/var/www/html`
- SSH on TCP port `2242`
- Glances on TCP port `55555`
- Docker/containerd
- Tailscale 1.102.2
- systemd-resolved and systemd-networkd
- an existing OMP auth broker on `127.0.0.1:9000`, owned by Unix user `sut`

The only enabled Apache site is the default HTTP virtual host. Apache reports no configured TLS virtual host, and `/etc/letsencrypt` was absent. No DDNS or cloudflared configuration was found in the audited standard locations. TCP port 443 was not listening.

Network addresses observed:

- LAN: `192.168.129.2/23` on `enp4s0`
- Tailscale: `100.107.209.71/32` on `tailscale0`
- Docker bridge networks are present; the active Minecraft container publishes TCP `25565`.

UFW is configured `ENABLED=yes`. The current user could not read the protected rule files or run `ufw status`; the active allow policy therefore remains a root-only follow-up. `nft list ruleset` was also unavailable without root. No firewall or connectivity change was made.

Existing unrelated workloads include a Minecraft server under `/home/tomi/server/dockermodedserv1` and its published container port. They must remain untouched by OMP Remote.

`systemd-analyze verify` also reported an existing malformed `Restart=always RestartSec=5` line in `/etc/systemd/system/glances.service`; it was not modified because Glances is unrelated.

## OMP installation and behavior

- Binary: `/home/tomi/.local/bin/omp`
- Version: `17.3.7`
- Supported non-interactive flags observed in `omp --help`: `--print`/`-p`, `--no-session`, `--no-tools`, and `--max-time`.
- The `tomi` profile uses a local auth broker at `127.0.0.1:9000`. Credential material remains in the profile with restrictive mode and is not copied into this project or job workspaces.
- Harmless invocation succeeded with `AUDIT_OK` using `omp -p --no-session --no-tools --max-time=30s ...`.
- A second bounded invocation returned `PROCESS_AUDIT_OK`.
- During the no-tools invocation, the OMP process ran under `tomi`; no child process remained after completion. This is not evidence about tool-enabled OMP descendants, which must be handled by OMP Remote's own process-group/cgroup boundary.
- The existing broker processes are owned by `sut` and are outside OMP Remote's control boundary.
- Independent execution as `draz` could not be performed: `sudo -n -u draz` required a password, and `/home/draz` had no OMP profile. This is an explicit Phase 0 blocker, not an assumption.

## Projects and workspace candidates

No allowlisted bioinformatics or OMP project was found in the audited roots. `/mnt/warehouse` contained only `lost+found`; `/mnt/garage` contained Minecraft-related data; `/home/tomi/server` contained the existing Minecraft project; and `/srv` was empty.

## Outstanding audit blockers

1. `draz` identity, OMP/provider, and filesystem audit is deferred by the current tomi-only scope; it is required before enabling `draz`.
2. Record protected UFW rules and any host-level firewall policy as root.
3. Identify the intended allowlisted projects and verify `tomi`'s real Unix permissions.
4. Test OMP with a controlled tool-enabled child process only inside a disposable process group/cgroup.

Until these blockers are resolved, the Phase 0 gate remains open and no production-facing service, reverse-proxy, firewall, systemd, or storage change is permitted.
