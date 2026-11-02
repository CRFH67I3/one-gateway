#!/bin/sh
# 把仓库根目录的网关核心复制进 iOS 包内资源目录。
#
# 单一来源原则：仓库根目录的 wb_*.py 与 dashboard.html 是唯一真身，移动端不复制
# 代码、只复制文件，所以 PC 版升级后移动端自动跟随（CI 每次构建都会重跑本脚本）。
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
DEST="$ROOT/mobile/ios/WorkBuddyHub/Resources/python"

mkdir -p "$DEST"

cp "$ROOT"/wb_*.py "$DEST"/
cp "$ROOT/dashboard.html" "$DEST"/
cp "$ROOT/mobile/shared/wb_mobile_runtime.py" "$DEST"/
cp "$ROOT/mobile/ios/WorkBuddyHub/PythonBridge/wb_hub_ios.py" "$DEST"/

# 交付前自检：核心与面板缺一不可，缺了直接让构建失败，别等运行时才报错。
for required in wb_proxy.py dashboard.html wb_mobile_runtime.py wb_hub_ios.py; do
    if [ ! -f "$DEST/$required" ]; then
        echo "error: $required missing in $DEST" >&2
        exit 1
    fi
done

echo "synced $(ls "$DEST" | wc -l | tr -d ' ') files into Resources/python"

# iOS 没有系统 CA 路径（OpenSSL 找不到 /etc/ssl），必须自带一份证书束。
# 已存在就跳过，避免每次 Xcode 增量构建都联网；装不上只告警，CI 里另有强制步骤。
if [ ! -f "$DEST/certifi/cacert.pem" ] && command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --quiet --disable-pip-version-check --target "$DEST" certifi \
        || echo "warning: certifi 安装失败，App 内 HTTPS 请求会校验失败" >&2
fi
