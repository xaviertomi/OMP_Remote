#!/bin/sh
set -eu

PROJECT=/home/tomi/omp_app
INSTALL=/opt/omp-remote
ETC=/etc/omp-remote
DATA=/var/lib/omp-remote
APK_SOURCE="$PROJECT/android/app/build/outputs/apk/debug/app-debug.apk"
APK_DEST="$DATA/client/omp-remote.apk"
API_USER=omp-remote-api
RUNTIME_GROUP=omp-remote

fail() {
    printf '%s\n' "install-tomi-only: $*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || fail "run this script from a local root shell"
[ -f "$PROJECT/server/omp_remote/version.py" ] || fail "server version source is missing"
[ -f "$APK_SOURCE" ] || fail "build the debug APK before installing: $APK_SOURCE"
[ ! -L "$APK_SOURCE" ] || fail "APK source must not be a symlink"
[ -d "$PROJECT/server/omp_remote" ] || fail "project source not found at $PROJECT"
[ -f "$PROJECT/server/pyproject.toml" ] || fail "server package metadata is missing"
[ -f "$PROJECT/server/requirements.txt" ] || fail "server requirements file is missing"
[ ! -e "$INSTALL" ] || fail "$INSTALL already exists; aborting instead of overwriting"

getent group "$RUNTIME_GROUP" >/dev/null 2>&1 || groupadd --system "$RUNTIME_GROUP"
getent passwd "$API_USER" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --gid "$RUNTIME_GROUP" "$API_USER"

install -d -o root -g root -m 0755 "$INSTALL" "$INSTALL/server" "$INSTALL/server/omp_remote"
install -d -o root -g root -m 0755 "$INSTALL/systemd" "$INSTALL/scripts"
cp -a "$PROJECT/server/omp_remote/." "$INSTALL/server/omp_remote/"
cp -a "$PROJECT/server/pyproject.toml" "$PROJECT/server/requirements.txt" "$INSTALL/server/"
rm -rf "$INSTALL/server/omp_remote/__pycache__"
cp -a "$PROJECT/systemd/." "$INSTALL/systemd/"
cp -a "$PROJECT/scripts/omp-remote" "$INSTALL/scripts/"
python3 -m venv "$INSTALL/server/.venv"
chown -R root:root "$INSTALL"
chmod -R u+rwX,go+rX "$INSTALL"
chmod 0755 "$INSTALL/server/.venv/bin/python" "$INSTALL/scripts/omp-remote"

install -d -o root -g "$RUNTIME_GROUP" -m 0750 "$DATA/client"
install -o root -g "$RUNTIME_GROUP" -m 0640 "$APK_SOURCE" "$APK_DEST"
install -d -o root -g root -m 0750 "$ETC"
cat >"$ETC/server.env" <<'ENV'
OMP_REMOTE_DATA_DIR=/var/lib/omp-remote
OMP_REMOTE_DB_PATH=/var/lib/omp-remote/omp-remote.sqlite3
OMP_REMOTE_STATE_PATH=/var/lib/omp-remote/state/state
OMP_REMOTE_APK_PATH=/var/lib/omp-remote/client/omp-remote.apk
OMP_REMOTE_WORKSPACE_ROOT=/var/lib/omp-remote/jobs
OMP_REMOTE_ALLOWED_USERS=tomi
OMP_REMOTE_PROJECTS_JSON=[]
OMP_REMOTE_BIND_HOST=127.0.0.1
OMP_REMOTE_BIND_PORT=8099
OMP_REMOTE_EMERGENCY_PORT=18098
OMP_REMOTE_MAX_CONCURRENT_PER_USER=1
OMP_REMOTE_MAX_CONCURRENT_GLOBAL=1
ENV
chown root:"$RUNTIME_GROUP" "$ETC/server.env"
chmod 0640 "$ETC/server.env"

install -o root -g root -m 0644 "$INSTALL/systemd/omp-remote-api.service" /etc/systemd/system/omp-remote-api.service
install -o root -g root -m 0644 "$INSTALL/systemd/omp-remote-worker@.service" /etc/systemd/system/omp-remote-worker@.service
install -o root -g root -m 0644 "$INSTALL/systemd/omp-remote-emergency.service" /etc/systemd/system/omp-remote-emergency.service
install -o root -g root -m 0644 "$INSTALL/systemd/omp-remote-gateway.service" /etc/systemd/system/omp-remote-gateway.service
systemd-analyze verify \
    /etc/systemd/system/omp-remote-api.service \
    /etc/systemd/system/omp-remote-worker@.service \
    /etc/systemd/system/omp-remote-emergency.service \
    /etc/systemd/system/omp-remote-gateway.service
systemctl daemon-reload
systemctl enable omp-remote-api.service omp-remote-worker@tomi.service omp-remote-emergency.service omp-remote-gateway.service
systemctl start omp-remote-api.service omp-remote-worker@tomi.service omp-remote-emergency.service omp-remote-gateway.service

for attempt in 1 2 3 4 5; do
    if curl --fail --silent --show-error http://127.0.0.1:8099/api/v1/health >/dev/null \
        && curl --fail --silent --show-error http://127.0.0.1:18080/api/v1/health >/dev/null; then
        printf '%s\n' 'OMP Remote tomi-only services started successfully.'
        exit 0
    fi
    sleep 1
done
systemctl status --no-pager omp-remote-api.service omp-remote-worker@tomi.service omp-remote-emergency.service omp-remote-gateway.service || true
fail 'health check failed after service start'
