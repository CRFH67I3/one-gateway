#!/bin/sh
# 拉取外部网关项目的核心放进 iOS 包内资源目录，并铺好 CPython 的标准库。
#
# 本仓库只是容器，不带网关源码。构建时下载一份网关项目 ZIP，按与 Android
# 「导入项目 ZIP」相同的口径抽出 wb_*.py 与 dashboard.html；换源用
# GATEWAY_CORE_ZIP_URL，离线构建用 GATEWAY_CORE_ZIP 指一份本地 ZIP。
# 只有 Python 型网关能作为包内核心；Go 型由 build-go-gateway.sh 在构建期
# 单独编成随包签名的 c-archive 静态库，不是这里的 wb_*.py 资源（脚本遇到
# go.mod 会明确报错，避免把 Go 源码误当 Python 核心塞进包）。
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
RES="$ROOT/mobile/ios/OneGateway/Resources"
DEST_APP="$RES/app"
DEST_PY="$RES/python"
VENDOR="$ROOT/mobile/ios/Vendor/Python.xcframework"

mkdir -p "$DEST_APP" "$DEST_PY"

# 早期版本把核心平铺在 Resources/python 下（不在 sys.path 上，纯属重复占位）。
# 升级后要清掉，否则包里会同时存在两份 1MB 的面板与一份没用的核心源码。
rm -f "$DEST_PY"/wb_*.py "$DEST_PY"/dashboard.html
rm -rf "$DEST_PY"/certifi "$DEST_PY"/certifi-*.dist-info

# 网关核心来自外部 ZIP：先清掉上次注入的副本，再按文件名抽取项目根那一层。
CORE_ZIP_URL=${GATEWAY_CORE_ZIP_URL:-https://github.com/ardeyouxipianyi/workbuddy2api-hub/archive/refs/heads/main.zip}

if [ -n "${GATEWAY_CORE_ZIP:-}" ]; then
    CORE_ZIP=$GATEWAY_CORE_ZIP
else
    command -v curl >/dev/null 2>&1 || { echo "error: 需要 curl 下载网关项目 ZIP" >&2; exit 1; }
    # 模板的 X 必须放在末尾：BSD mktemp（macOS CI）不接受 .XXXXXX.zip 这种中段模板。
    CORE_ZIP=$(mktemp "${TMPDIR:-/tmp}/gateway-core.XXXXXX")
    curl -fsSL --retry 3 --connect-timeout 20 --max-time 180 \
        -o "$CORE_ZIP" "$CORE_ZIP_URL" \
        || { echo "error: 下载网关项目失败：$CORE_ZIP_URL" >&2; exit 1; }
fi

# 不用 find -maxdepth / -delete：macOS 的 BSD find 两者都不支持。
for stale in "$DEST_APP"/wb_*.py; do
    [ -f "$stale" ] && rm -f "$stale"
done
rm -f "$DEST_APP/dashboard.html"

python3 - "$CORE_ZIP" "$DEST_APP" <<'PY'
import os, sys, zipfile
zip_path, dest = sys.argv[1], sys.argv[2]
picked = []
with zipfile.ZipFile(zip_path) as zf:
    for name in zf.namelist():
        base = os.path.basename(name)
        wanted = (base.startswith("wb_") and base.endswith(".py")) or base == "dashboard.html"
        # GitHub archive 会多套一层 <repo>-<branch>/；更深的是项目内子目录，不要。
        if not wanted or name.count("/") > 1 or not base:
            continue
        picked.append((name, base))
    if not any(base == "wb_proxy.py" for _, base in picked):
        if any(os.path.basename(n) == "go.mod" for n in zf.namelist()):
            sys.exit("error: 这是 Go 网关源码包（go.mod）。iOS 包内 CPython 只能托管 "
                     "Python 网关；Go 网关由 scripts/build-go-gateway.sh 在构建期编成 "
                     "c-archive 静态库随 App 签名，运行时在菜单「本机网关形态…」里切到 "
                     "Go 即可。要继续注入 Python 核心，请把 GATEWAY_CORE_ZIP_URL 指回 "
                     "Python 网关仓库。")
        sys.exit("error: ZIP 里找不到 wb_proxy.py（不是网关项目 ZIP？）")
    for name, base in picked:
        with zf.open(name) as src, open(os.path.join(dest, base), "wb") as out:
            out.write(src.read())
    print("extracted %d gateway core files" % len(picked))
PY

[ -n "${GATEWAY_CORE_ZIP:-}" ] || rm -f "$CORE_ZIP"

# 这两份是容器自己的桥接层，不属于网关，直接随包携带。
cp "$ROOT/mobile/shared/wb_mobile_runtime.py" "$DEST_APP"/
cp "$ROOT/mobile/ios/OneGateway/PythonBridge/wb_hub_ios.py" "$DEST_APP"/

# 交付前自检：外部核心与容器桥接层缺一不可，缺了直接让构建失败，
# 别等 App 在手机上启动才暴露。
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

# 在支持包里找标准库：python-app-support 每个版本的内部分层都略有出入，所以按
# 「先试已知路径、再全树搜 os.py」的顺序找，命中后统一用 pwd -P 解成真实目录。
# 3.13-b18 的实际布局：
#   Python.xcframework/lib/python3.13              ← 各平台共享的标准库（就用这份）
#   Python.xcframework/ios-arm64/Python.framework  ← 设备解释器
#   Python.xcframework/ios-arm64_x86_64-simulator/ ← 模拟器切片
# $1 = 搜索根；输出命中的标准库目录。
find_stdlib() {
    for candidate in "$1"/lib-arm64/python3.* "$1"/lib/python3.* "$1"/python3.* \
                     "$1"/Python.framework/lib/python3.*; do
        if [ -f "$candidate/os.py" ]; then
            (cd "$candidate" && pwd -P)
            return 0
        fi
    done
    for mode in "" "-L"; do
        hit=$(find $mode "$1" -name os.py 2>/dev/null \
              | grep -v -e 'simulator' -e 'x86_64' -e '/site-packages/' \
              | head -n 1)
        if [ -n "$hit" ]; then
            (cd "$(dirname "$hit")" && pwd -P)
            return 0
        fi
    done
    return 1
}

# 标准库：铺进 Resources/python/lib/<pythonX.Y>。
#
# 支持包把标准库拆成两份，缺一不可：
#   lib/python3.13/                  纯 Python（os.py、encodings/、hashlib.py……）
#   ios-arm64/lib-arm64/python3.13/  二进制扩展，全在 lib-dynload 下（68 个 .so：
#                                    _hashlib、_sha1、_socket、_ssl、_json、zlib……）
# 只拷共享那份时解释器能起来（os.py 在），但一 import 就露馅：
#   AttributeError: module 'hashlib' has no attribute 'sha1'
# 因为 CPython 的 hashlib 要么走 _hashlib（OpenSSL），要么走 _sha1，两者都是
# lib-dynload 里的 .so。缺了 lib-dynload 不只是哈希——_socket / _ssl 同样没有，
# 网关连监听端口都做不到。
if [ -d "$VENDOR" ]; then
    SLICE="$VENDOR/ios-arm64"
    [ -d "$SLICE" ] || SLICE="$VENDOR"
    # 共享标准库优先（顶层 lib/），取不到再全树兜底。
    STDLIB=$(find_stdlib "$VENDOR/lib" || find_stdlib "$VENDOR" || true)
    if [ -z "$STDLIB" ]; then
        echo "error: 在 $VENDOR 里找不到标准库（os.py），支持包结构可能变了：" >&2
        find "$VENDOR" -type d 2>/dev/null | head -40 >&2
        exit 1
    fi
    LIB_VER=$(basename "$STDLIB")          # python3.13
    DEST_LIB="$DEST_PY/lib/$LIB_VER"
    mkdir -p "$DEST_PY/lib"

    # 设备切片的二进制扩展目录。单架构包会把整份标准库放在切片里，所以按
    # lib-<arch>/pythonX.Y 也找一遍，避免只认一种布局。
    SLICE_LIB=""
    for candidate in "$SLICE/lib-arm64/$LIB_VER" "$SLICE"/lib-*/"$LIB_VER"; do
        if [ -d "$candidate/lib-dynload" ]; then
            SLICE_LIB=$candidate
            break
        fi
    done

    # 重铺条件：缺 os.py，或**缺二进制扩展**。后者是关键——早期版本只铺了共享
    # 标准库，升级 App 时如果只看 os.py 就会跳过重铺，用户仍然报 hashlib 错误。
    need_install=0
    [ -f "$DEST_LIB/os.py" ] || need_install=1
    ls "$DEST_LIB"/lib-dynload/_sha1*.so >/dev/null 2>&1 || need_install=1
    if [ "$need_install" = "1" ]; then
        rm -rf "$DEST_LIB"
        cp -R "$STDLIB" "$DEST_LIB"
        if [ -n "$SLICE_LIB" ]; then
            cp -R "$SLICE_LIB"/. "$DEST_LIB"/
            echo "merged binary modules from $SLICE_LIB -> Resources/python/lib/$LIB_VER/lib-dynload"
        fi
        echo "installed CPython stdlib ($LIB_VER) from $STDLIB -> Resources/python/lib/$LIB_VER"
    fi
    if [ ! -f "$DEST_LIB/os.py" ] || [ ! -f "$DEST_LIB/encodings/__init__.py" ]; then
        echo "error: 标准库不完整（$DEST_LIB）" >&2
        exit 1
    fi
    if ! ls "$DEST_LIB"/lib-dynload/_sha1*.so >/dev/null 2>&1; then
        echo "error: 标准库缺少二进制扩展（$DEST_LIB/lib-dynload）" >&2
        echo "       iOS 上 hashlib / socket / ssl 会全部不可用，App 启动即报" >&2
        echo "       AttributeError: module 'hashlib' has no attribute 'sha1'" >&2
        echo "       检查 $SLICE 下是否存在 lib-arm64/$LIB_VER/lib-dynload" >&2
        exit 1
    fi
else
    echo "warning: $VENDOR 不存在，跳过标准库（仅本地开发可忽略，CI 会先下载）" >&2
fi
