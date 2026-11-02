#!/bin/sh
# 把仓库根目录的网关核心复制进 iOS 包内资源目录，并铺好 CPython 的标准库。
#
# 单一来源原则：仓库根目录的 wb_*.py 与 dashboard.html 是唯一真身，移动端只复制
# 文件、不复制代码，所以 PC 版升级后移动端自动跟随（CI 每次构建都会重跑本脚本）。
#
# 包内布局严格照 CPython 官方文档《Using Python on iOS》第 7 步 `install_python`
# 的产物来（BeeWare python-app-support 的 USAGE.md 给的初始化代码也一致）：
#
#   Resources/python/lib/python3.13        ← 标准库（PYTHONHOME 指 Resources/python）
#   Resources/python/lib/python3.13/lib-dynload
#   Resources/app                          ← 网关核心 + 桥接层 + certifi（PYTHONPATH）
#
# 标准库必须落在 `python/lib/pythonX.Y` 这一层：CPython 的 getpath 把 PYTHONHOME
# 当 prefix、只认 `prefix/lib/pythonX.Y`。平铺在 Resources/python 下时解释器能起来，
# 但一 import 就报 `No module named 'encodings'`——这是最容易踩的坑。
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
RES="$ROOT/mobile/ios/WorkBuddyHub/Resources"
DEST_APP="$RES/app"
DEST_PY="$RES/python"
VENDOR="$ROOT/mobile/ios/Vendor/Python.xcframework"

mkdir -p "$DEST_APP" "$DEST_PY"

# 早期版本把核心平铺在 Resources/python 下（不在 sys.path 上，纯属重复占位）。
# 升级后要清掉，否则包里会同时存在两份 1MB 的面板与一份没用的核心源码。
rm -f "$DEST_PY"/wb_*.py "$DEST_PY"/dashboard.html
rm -rf "$DEST_PY"/certifi "$DEST_PY"/certifi-*.dist-info

cp "$ROOT"/wb_*.py "$DEST_APP"/
cp "$ROOT/dashboard.html" "$DEST_APP"/
cp "$ROOT/mobile/shared/wb_mobile_runtime.py" "$DEST_APP"/
cp "$ROOT/mobile/ios/WorkBuddyHub/PythonBridge/wb_hub_ios.py" "$DEST_APP"/

# 交付前自检：核心与面板缺一不可，缺了直接让构建失败，别等运行时才报错。
for required in wb_proxy.py dashboard.html wb_mobile_runtime.py wb_hub_ios.py; do
    if [ ! -f "$DEST_APP/$required" ]; then
        echo "error: $required missing in $DEST_APP" >&2
        exit 1
    fi
done

echo "synced $(ls "$DEST_APP" | wc -l | tr -d ' ') files into Resources/app"

# iOS 没有系统 CA 路径（OpenSSL 找不到 /etc/ssl），必须自带一份证书束。
# 已存在就跳过，避免每次 Xcode 增量构建都联网；装不上只告警，CI 里另有强制步骤。
if [ ! -f "$DEST_APP/certifi/cacert.pem" ] && command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --quiet --disable-pip-version-check --target "$DEST_APP" certifi \
        || echo "warning: certifi 安装失败，App 内 HTTPS 请求会校验失败" >&2
fi

# 标准库：从 python-app-support 的设备切片铺进 Resources/python/lib/。
# 切片里 `lib/python3.13` 在 macOS 上是符号链接，指向同级的 `lib-arm64/python3.13`，
# 这里用 pwd -P 跟随到真实目录再复制，避免把链接本身带进 bundle。
if [ -d "$VENDOR" ]; then
    SLICE="$VENDOR/ios-arm64"
    if [ ! -d "$SLICE" ]; then
        echo "error: $SLICE 不存在（XCFramework 结构不符合预期）" >&2
        exit 1
    fi
    STDLIB=""
    for candidate in "$SLICE"/lib-*/python3.* "$SLICE"/lib/python3.*; do
        if [ -f "$candidate/os.py" ]; then
            STDLIB=$(cd "$candidate" && pwd -P)
            break
        fi
    done
    if [ -z "$STDLIB" ]; then
        echo "error: CPython stdlib (os.py) not found under $SLICE" >&2
        exit 1
    fi
    LIB_VER=$(basename "$STDLIB")          # python3.13
    DEST_LIB="$DEST_PY/lib/$LIB_VER"
    mkdir -p "$DEST_PY/lib"
    # 只在标准库缺失时重铺：17MB / 数千文件，增量构建没必要每次都拷。
    if [ ! -f "$DEST_LIB/os.py" ]; then
        rm -rf "$DEST_LIB"
        cp -R "$STDLIB" "$DEST_LIB"
        echo "installed CPython stdlib ($LIB_VER) -> Resources/python/lib/$LIB_VER"
    fi
    if [ ! -f "$DEST_LIB/os.py" ] || [ ! -f "$DEST_LIB/encodings/__init__.py" ]; then
        echo "error: 标准库不完整（$DEST_LIB）" >&2
        exit 1
    fi
else
    echo "warning: $VENDOR 不存在，跳过标准库（仅本地开发可忽略，CI 会先下载）" >&2
fi
