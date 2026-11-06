#!/bin/sh
set -eu

APP_DIR=/opt/proxy-gateway-runtime
APP_SERVICE=proxy-gateway-runtime.service
UPDATER_NAME=proxy-gateway-runtime-updater
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE=/etc/proxy-gateway-runtime-updater.env
DROPIN_DIR=/etc/systemd/system/proxy-gateway-runtime.service.d
DROPIN=$DROPIN_DIR/20-updater.conf
BACKUP_ROOT=/var/backups/proxy-gateway-runtime
STAMP=$(date +%Y%m%d-%H%M%S)
BOOTSTRAP_BACKUP=$BACKUP_ROOT/bootstrap-$STAMP
ENV_CREATED=0
# 更新源：新装写这个仓库；老部署的 env 里可能是改名前的旧值，下面按白名单放行，
# 以免改名后重跑本脚本直接退出。也可以用 WB_UPDATE_REPO 覆盖（写进同一份 env）。
REPO_DEFAULT=CRFH67I3/proxy-gateway-runtime
REPO_LEGACY=xybzljc/workbuddy2api-hub

targets='
/usr/local/libexec/proxy-gateway-runtime-updater.py
/etc/systemd/system/proxy-gateway-runtime-updater.service
/etc/systemd/system/proxy-gateway-runtime-updater.path
/etc/tmpfiles.d/proxy-gateway-runtime-updater.conf
/etc/systemd/system/proxy-gateway-runtime.service.d/20-updater.conf'

rollback_bootstrap(){
    systemctl disable --now proxy-gateway-runtime-updater.path >/dev/null 2>&1 || true
    printf '%s\n' "$targets" | while IFS= read -r target; do
        [ -n "$target" ] || continue
        rel=$(printf '%s' "$target" | sed 's#^/##; s#/#_#g')
        saved=$BOOTSTRAP_BACKUP/$rel
        if [ -f "$saved" ]; then
            cp -a -- "$saved" "$target"
        else
            rm -f -- "$target"
        fi
    done
    if [ "$ENV_CREATED" -eq 1 ]; then rm -f -- "$ENV_FILE"; fi
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl restart "$APP_SERVICE" >/dev/null 2>&1 || true
}

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi
if [ ! -d "$APP_DIR/accounts" ] || [ ! -d "$APP_DIR/usage" ]; then
    echo "Expected account and usage directories were not found; stopping." >&2
    exit 1
fi
if ! id wbproxy >/dev/null 2>&1; then
    echo "The wbproxy service user was not found; stopping." >&2
    exit 1
fi
for file in \
    proxy-gateway-runtime-updater.py \
    proxy-gateway-runtime-updater.service \
    proxy-gateway-runtime-updater.path \
    proxy-gateway-runtime-updater.conf \
    proxy-gateway-runtime-updater-app.conf; do
    if [ ! -f "$SCRIPT_DIR/$file" ]; then
        echo "Missing updater asset: $file" >&2
        exit 1
    fi
done

install -d -o root -g root -m 0700 "$BACKUP_ROOT" "$BOOTSTRAP_BACKUP"
install -d -o root -g wbproxy -m 1730 /run/proxy-gateway-runtime-updater
install -d -o root -g root -m 0700 \
    /var/lib/proxy-gateway-runtime-updater \
    /var/tmp/proxy-gateway-runtime-updater
install -d -o root -g root -m 0755 /usr/local/libexec /etc/tmpfiles.d "$DROPIN_DIR"

if [ -e "$ENV_FILE" ]; then
    if ! grep -Eqx "WB_UPDATE_REPO=($REPO_DEFAULT|$REPO_LEGACY)" "$ENV_FILE"; then
        echo "Existing updater repository differs; preserved $ENV_FILE." >&2
        exit 1
    fi
else
    env_tmp=$(mktemp /etc/proxy-gateway-runtime-updater.env.XXXXXX)
    printf '%s\n' "WB_UPDATE_REPO=$REPO_DEFAULT" > "$env_tmp"
    chown root:root "$env_tmp"
    chmod 0600 "$env_tmp"
    mv -f "$env_tmp" "$ENV_FILE"
    ENV_CREATED=1
fi

for target in \
    /usr/local/libexec/proxy-gateway-runtime-updater.py \
    /etc/systemd/system/proxy-gateway-runtime-updater.service \
    /etc/systemd/system/proxy-gateway-runtime-updater.path \
    /etc/tmpfiles.d/proxy-gateway-runtime-updater.conf \
    "$DROPIN"; do
    if [ -e "$target" ]; then
        rel=$(printf '%s' "$target" | sed 's#^/##; s#/#_#g')
        cp -a -- "$target" "$BOOTSTRAP_BACKUP/$rel"
    fi
done

install -o root -g root -m 0750 "$SCRIPT_DIR/proxy-gateway-runtime-updater.py" \
    /usr/local/libexec/proxy-gateway-runtime-updater.py
install -o root -g root -m 0644 "$SCRIPT_DIR/proxy-gateway-runtime-updater.service" \
    /etc/systemd/system/proxy-gateway-runtime-updater.service
install -o root -g root -m 0644 "$SCRIPT_DIR/proxy-gateway-runtime-updater.path" \
    /etc/systemd/system/proxy-gateway-runtime-updater.path
install -o root -g root -m 0644 "$SCRIPT_DIR/proxy-gateway-runtime-updater.conf" \
    /etc/tmpfiles.d/proxy-gateway-runtime-updater.conf
install -o root -g root -m 0644 "$SCRIPT_DIR/proxy-gateway-runtime-updater-app.conf" \
    "$DROPIN"

if ! systemd-tmpfiles --create /etc/tmpfiles.d/proxy-gateway-runtime-updater.conf; then
    echo "Could not create the updater request directory; restoring previous files." >&2
    rollback_bootstrap
    exit 1
fi
if ! systemctl daemon-reload || ! systemctl enable --now "$UPDATER_NAME.path" \
        || ! systemctl restart "$APP_SERVICE"; then
    echo "Could not start the updater or restart the gateway; restoring previous files." >&2
    rollback_bootstrap
    exit 1
fi

if ! /usr/bin/python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8788/health", timeout=5)); raise SystemExit(0 if d.get("ok") is True else 1)'; then
    echo "Gateway health check failed after enabling updater. Bootstrap files are backed up at $BOOTSTRAP_BACKUP." >&2
    rollback_bootstrap
    exit 1
fi

echo "Updater installed. Bootstrap backup: $BOOTSTRAP_BACKUP"
