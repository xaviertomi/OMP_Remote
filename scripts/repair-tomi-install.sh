#!/bin/sh
set -eu

PROJECT=/home/tomi/omp_app
INSTALL=/opt/omp-remote
ETC=/etc/omp-remote
DATA=/var/lib/omp-remote
VERSION_NAME=$(sed -n 's/^VERSION_NAME = "\([^"]*\)"$/\1/p' "$PROJECT/server/omp_remote/version.py")
[ -n "$VERSION_NAME" ] || { printf '%s\n' 'repair-tomi-install: VERSION_NAME is missing' >&2; exit 1; }
APK_SOURCE="$PROJECT/android/app/build/outputs/apk/debug/omp-remote-v${VERSION_NAME}.apk"
APK_DEST="$DATA/client/omp-remote-v${VERSION_NAME}.apk"
APK_STABLE_DEST="$DATA/client/omp-remote.apk"
API_USER=omp-remote-api
RUNTIME_GROUP=omp-remote
API_UNIT=omp-remote-api.service
WORKER_UNIT=omp-remote-worker@tomi.service
EMERGENCY_UNIT=omp-remote-emergency.service
GATEWAY_UNIT=omp-remote-gateway.service

fail() {
    printf '%s\n' "repair-tomi-install: $*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || fail "run from a local root shell"
[ -f "$PROJECT/server/omp_remote/version.py" ] || fail "server version source is missing"
[ -f "$APK_SOURCE" ] || fail "build the debug APK before repairing: $APK_SOURCE"
[ ! -L "$APK_SOURCE" ] || fail "APK source must not be a symlink"
[ -d "$PROJECT/server/omp_remote" ] || fail "project source not found"
[ -f "$PROJECT/server/pyproject.toml" ] || fail "server package metadata is missing"
[ -f "$PROJECT/server/requirements.txt" ] || fail "server requirements file is missing"
[ -d "$INSTALL/server/.venv" ] || fail "existing install has no Python environment"

systemctl stop "$API_UNIT" "$WORKER_UNIT" "$EMERGENCY_UNIT" "$GATEWAY_UNIT" || true

getent group "$RUNTIME_GROUP" >/dev/null 2>&1 || groupadd --system "$RUNTIME_GROUP"
getent passwd "$API_USER" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --gid "$RUNTIME_GROUP" "$API_USER"
cp -a "$PROJECT/server/pyproject.toml" "$PROJECT/server/requirements.txt" "$INSTALL/server/"

install -d -o root -g root -m 0755 "$INSTALL/server/omp_remote"
cp -a "$PROJECT/server/omp_remote/." "$INSTALL/server/omp_remote/"
rm -rf "$INSTALL/server/omp_remote/__pycache__"
chown -R root:root "$INSTALL/server/omp_remote"
chmod -R u+rwX,go+rX "$INSTALL/server/omp_remote"

install -d -o "$API_USER" -g "$RUNTIME_GROUP" -m 0770 "$DATA" "$DATA/jobs" "$DATA/quarantine"
install -d -o root -g "$RUNTIME_GROUP" -m 0770 "$DATA/state"
if [ ! -e "$DATA/state/state" ]; then
    printf '%s\n' ENABLED | install -o root -g "$RUNTIME_GROUP" -m 0640 /dev/stdin "$DATA/state/state"
else
    chown root:"$RUNTIME_GROUP" "$DATA/state/state"
    chmod 0640 "$DATA/state/state"
fi
chown -R "$API_USER":"$RUNTIME_GROUP" "$DATA"
chown root:"$RUNTIME_GROUP" "$DATA/state" "$DATA/state/state"
if [ -e "$DATA/omp-remote.sqlite3" ]; then
    chown "$API_USER":"$RUNTIME_GROUP" "$DATA/omp-remote.sqlite3"
    chmod 0660 "$DATA/omp-remote.sqlite3"
fi
chmod 2770 "$DATA" "$DATA/jobs" "$DATA/quarantine" "$DATA/state"
for workspace in "$DATA"/jobs/*; do
    [ -d "$workspace" ] || continue
    [ -L "$workspace" ] && continue
    chgrp "$RUNTIME_GROUP" "$workspace"
    chmod 2770 "$workspace"
    for area in input workspace output logs metadata; do
        if [ -d "$workspace/$area" ] && [ ! -L "$workspace/$area" ]; then
            chgrp "$RUNTIME_GROUP" "$workspace/$area"
            chmod 2770 "$workspace/$area"
        fi
    done
done
install -d -o root -g "$RUNTIME_GROUP" -m 0750 "$DATA/client"
install -o root -g "$RUNTIME_GROUP" -m 0640 "$APK_SOURCE" "$APK_DEST"
install -o root -g "$RUNTIME_GROUP" -m 0640 "$APK_SOURCE" "$APK_STABLE_DEST"
chmod 0640 "$DATA/state/state"

[ -f "$ETC/server.env" ] || fail "$ETC/server.env is missing; rerun the installer after inspecting it"
chown root:"$RUNTIME_GROUP" "$ETC/server.env"
chmod 0640 "$ETC/server.env"

install -o root -g root -m 0644 "$PROJECT/systemd/omp-remote-api.service" /etc/systemd/system/omp-remote-api.service
install -o root -g root -m 0644 "$PROJECT/systemd/omp-remote-worker@.service" /etc/systemd/system/omp-remote-worker@.service
install -o root -g root -m 0644 "$PROJECT/systemd/omp-remote-emergency.service" /etc/systemd/system/omp-remote-emergency.service
install -o root -g root -m 0644 "$PROJECT/systemd/omp-remote-gateway.service" /etc/systemd/system/omp-remote-gateway.service
install -o root -g root -m 0755 "$PROJECT/scripts/omp-remote" "$INSTALL/scripts/omp-remote"
systemd-analyze verify /etc/systemd/system/omp-remote-api.service /etc/systemd/system/omp-remote-worker@.service /etc/systemd/system/omp-remote-emergency.service /etc/systemd/system/omp-remote-gateway.service
systemctl daemon-reload
systemctl reset-failed "$API_UNIT" "$WORKER_UNIT" "$EMERGENCY_UNIT" "$GATEWAY_UNIT" || true
systemctl start "$API_UNIT" "$WORKER_UNIT" "$EMERGENCY_UNIT" "$GATEWAY_UNIT"

sleep 2
systemctl is-active --quiet "$API_UNIT" || fail "$API_UNIT is not active"
systemctl is-active --quiet "$WORKER_UNIT" || fail "$WORKER_UNIT is not active"
systemctl is-active --quiet "$EMERGENCY_UNIT" || fail "$EMERGENCY_UNIT is not active"
systemctl is-active --quiet "$GATEWAY_UNIT" || fail "$GATEWAY_UNIT is not active"
curl --fail --silent --show-error http://127.0.0.1:8099/api/v1/health >/dev/null || fail 'API health check failed'
curl --fail --silent --show-error http://127.0.0.1:18080/api/v1/health >/dev/null || fail 'gateway health check failed'
printf '%s\n' 'OMP Remote tomi-only repair succeeded.'
