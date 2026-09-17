# OMP Remote recovery

## If compromise is suspected

1. Use the separately provisioned emergency credential to call `POST /emergency/kill`.
2. Confirm emergency status reports `DISABLED`.
3. Do not call or create any enable route remotely.
4. Preserve incident metadata and quarantine affected job workspaces.
5. Verify Apache, SSH, Tailscale, Docker, Minecraft, and unrelated manual processes remain outside the OMP Remote boundary.
6. Revoke the affected normal and emergency credentials locally.
7. Inspect logs and filesystem state from a local root shell.

The local controller marks running jobs `aborted_emergency` and records minimal incident metadata. Production systemd cgroup containment must be verified before relying on remote kill.

## Local recovery

```bash
sudo omp-remote status
sudo omp-remote enable
systemctl restart omp-remote-api.service
systemctl restart omp-remote-worker@tomi.service
```

Only run the restart after confirming the state file, units, configuration permissions, and worker identity. If the workspace may be compromised, quarantine it; do not copy its files into an original project.

## Data recovery

Back up SQLite while the API is stopped or use SQLite's online backup mechanism. Keep database backups root-readable and outside job workspaces. Preserve output files only after integrity and ownership checks. Cleanup is not part of the emergency kill path.

## Credential rotation

Issue a replacement device credential locally, install it into Android Keystore, verify a harmless job, then revoke the old device. Emergency credentials are rotated independently. Never reuse normal credentials as emergency credentials.
