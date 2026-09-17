# OMP Remote administration

## Safety boundary

The repository contains implementation and service templates only. Do not install units, create users, modify Apache, change UFW, add ports, or create `/srv/omp-remote` until the Phase 0 host gate is approved.

Never run the API or workers as root. The emergency service is separate and root-controlled only because its operation must write the persistent disabled state and terminate OMP Remote cgroups.

## Local commands

After installing the project at the deployment path, the local wrapper provides:

```bash
sudo omp-remote issue-device --user tomi --label phone
sudo omp-remote revoke-device DEVICE_UUID
sudo omp-remote issue-emergency --label phone
sudo omp-remote revoke-emergency CREDENTIAL_UUID
sudo omp-remote disable
sudo omp-remote enable
omp-remote status
```

`enable` is local-root-only. There is no remote enable command. Enabling only writes `ENABLED`; service restart must be performed by the local administrator after verifying the host state.

## Root handoff

The prepared tomi-only installer is:

```bash
sudo /home/tomi/omp_app/scripts/install-tomi-only.sh
```

It creates only OMP Remote users/directories/units, binds both services to localhost, starts `tomi`, and does not modify Apache, UFW, SSH, VPN, Docker, or `draz`. Review the script locally before execution.

## Versioned server and APK publication

The canonical server version is defined in `server/omp_remote/version.py`. The Android Gradle build reads the same `VERSION_CODE` and `VERSION_NAME`; the title, APK metadata, update metadata, and filename therefore remain aligned. The server reads the stable APK path `/var/lib/omp-remote/client/omp-remote.apk`, while the repair script also retains the versioned file.

Build the debug APK first, then publish the current server package and APK with the local root repair command:

```bash
cd /home/tomi/omp_app/android
gradle assembleDebug
sudo /home/tomi/omp_app/scripts/repair-tomi-install.sh
```

For the current delivery (`0.5`, code `5`), the versioned build artifact is `android/app/build/outputs/apk/debug/omp-remote-v0.5.apk` and the published copy is `/var/lib/omp-remote/client/omp-remote-v0.5.apk`. The repair script updates only the OMP Remote `tomi` API/worker/emergency/gateway units and never targets `draz` or unrelated services.

## Planned deployment sequence

1. Complete root-assisted audit of users, ACLs, projects, firewall, TLS, reverse proxy, and service boundaries.
2. Create protected `/etc/omp-remote`, `/var/lib/omp-remote`, `/run/omp-remote`, and workspace directories with separate ownership.
3. Create a dedicated API account and configure only the intended `tomi` worker instance.
4. Install the four reviewed systemd templates and validate them with `systemd-analyze verify`.
5. Provision credentials locally; never send tokens through chat or commit them.
6. Bind API and emergency listeners to private/localhost addresses.
7. Add narrowly scoped Apache HTTPS routes only after configuration validation and explicit connectivity review.
8. Start with a harmless tomi job and verify UID, cgroup, workspace, logs, and output.

## Operations

Inspect status and logs with unit-specific commands only:

```bash
systemctl status omp-remote-api.service
systemctl status omp-remote-worker@tomi.service
systemctl status omp-remote-emergency.service
journalctl -u omp-remote-api.service
journalctl -u omp-remote-worker@tomi.service
```

Do not stop unrelated services or kill by executable name or Unix username. Emergency termination must target only the OMP Remote service cgroups.
