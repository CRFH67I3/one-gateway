#!/bin/sh
# 把标准库里的二进制扩展从 .so 换成 iOS 能加载的 framework。
#
# 为什么必须有这一步（不是可选优化）：
#   iOS 不允许 app 包里存在 Frameworks/ 之外的可执行二进制，`.so` 即使放进去
#   也不会被签名，`dlopen` 一律失败——表现为
#       AttributeError: module 'hashlib' has no attribute 'sha1'
#   （CPython 的 hashlib 要么走 _hashlib，要么走 _sha1，都是二进制扩展；同样
#   完蛋的还有 _socket / _ssl / _json / zlib，网关连端口都监听不了。）
#
# 做法与 CPython 官方《Using Python on iOS》第 7 步的 `install_python` 一致：
#   1. 共享 stdlib + 设备切片的 lib-arm64 合并进 <app>/python/lib/；
#   2. lib-dynload 下每个 xxx.so 移进 Frameworks/xxx.framework/，原位置留一个
#      xxx.fwork 文本文件指向它，framework 里再放一个 .origin 指回来；
#   3. 有签名身份时立刻签掉（Xcode 的 CodeSign 阶段不会递归签这些新框架）。
#
# 由 project.yml 的 postBuildScripts 调用；CI 用 CODE_SIGNING_ALLOWED=NO 构建，
# 这一步仍然执行（框架布局照做），签名留给装机时重签的工具。
set -eu

APP="${CODESIGNING_FOLDER_PATH:-}"
SRC="${SRCROOT:-}"
VENDOR="$SRC/Vendor/Python.xcframework"

if [ -z "$APP" ] || [ ! -d "$APP" ]; then
    echo "warning: CODESIGNING_FOLDER_PATH 不可用（$APP），跳过 Python 库处理" >&2
    exit 0
fi
if [ ! -d "$VENDOR" ]; then
    echo "warning: $VENDOR 不存在，跳过 Python 库处理" >&2
    exit 0
fi

# 选平台切片。iOS 只有真机 arm64 与模拟器两种可用形态。
SLICE=""
case "${EFFECTIVE_PLATFORM_NAME:-}" in
    -iphonesimulator)
        for candidate in ios-arm64-simulator ios-arm64_x86_64-simulator \
                         ios-x86_64-simulator; do
            [ -d "$VENDOR/$candidate" ] && { SLICE="$VENDOR/$candidate"; break; }
        done
        ;;
    *)
        [ -d "$VENDOR/ios-arm64" ] && SLICE="$VENDOR/ios-arm64"
        ;;
esac
[ -n "$SLICE" ] || SLICE="$VENDOR"

# 1. 合并标准库：共享的纯 Python 部分 + 切片里的二进制扩展。
#    rsync 只在有增量时写盘，几百个文件一次也就零点几秒，不必做缓存判断。
LIB_DEST="$APP/python/lib"
mkdir -p "$LIB_DEST"
if [ -d "$VENDOR/lib" ]; then
    rsync -a --exclude 'libpython*.dylib' "$VENDOR/lib/" "$LIB_DEST/"
fi
# 模拟器切片下有 lib-arm64 与 lib-x86_64 两份，只能取当前 ARCHS 匹配的那份，
# 否则两份同名 .so 互相覆盖，最后加载的架构对不上。
ARCH_PICK=$(printf '%s' "${ARCHS:-arm64}" | cut -d' ' -f1)
SLICE_ARCH=""
for candidate in "$SLICE/lib-$ARCH_PICK" "$SLICE"/lib-*; do
    if [ -d "$candidate" ]; then
        SLICE_ARCH="$candidate"
        break
    fi
done
if [ -n "$SLICE_ARCH" ]; then
    rsync -a --exclude 'libpython*.dylib' "$SLICE_ARCH/" "$LIB_DEST/"
fi
echo "python libraries synced from $(basename "$SLICE")/$(basename "${SLICE_ARCH:-none}")"

PY_VER=$(ls -1 "$LIB_DEST" 2>/dev/null | grep -E '^python3\.[0-9]+$' | sort -V | tail -n 1 || true)
if [ -z "$PY_VER" ]; then
    echo "error: $LIB_DEST 下没有 python3.x 目录，标准库没铺进来" >&2
    exit 1
fi
DYNLOAD="$LIB_DEST/$PY_VER/lib-dynload"
if [ ! -d "$DYNLOAD" ]; then
    echo "error: 缺少 $DYNLOAD（二进制扩展），App 启动会报 hashlib 无 sha1" >&2
    exit 1
fi

# 2. 逐个 .so → Frameworks/<模块名>.framework。
IDENTITY="${EXPANDED_CODE_SIGN_IDENTITY:-}"
if [ "${CODE_SIGNING_ALLOWED:-YES}" = "NO" ] || [ -z "$IDENTITY" ] || [ "$IDENTITY" = "-" ]; then
    IDENTITY=""
fi

converted=0
for so in "$DYNLOAD"/*.so; do
    [ -f "$so" ] || continue
    base=$(basename "$so")
    # _sha1.cpython-313-iphoneos.so → _sha1；array.cpython-313-iphoneos.so → array
    module=${base%%.*}
    fw="$APP/Frameworks/$module.framework"
    if [ ! -d "$fw" ]; then
        mkdir -p "$fw"
        if [ -f "$VENDOR/build/iOS-dylib-Info-template.plist" ]; then
            cp "$VENDOR/build/iOS-dylib-Info-template.plist" "$fw/Info.plist"
            plutil -replace CFBundleExecutable -string "$module" "$fw/Info.plist"
            plutil -replace CFBundleIdentifier \
                -string "$(printf '%s.%s' "${PRODUCT_BUNDLE_IDENTIFIER:-com.workbuddy2api.hub}" "$module" | tr '_' '-')" \
                "$fw/Info.plist"
        else
            cat > "$fw/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key><string>en</string>
	<key>CFBundleExecutable</key><string>$module</string>
	<key>CFBundleIdentifier</key><string>com.workbuddy2api.hub.$module</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>1.0</string>
	<key>CFBundleVersion</key><string>1</string>
	<key>MinimumOSVersion</key><string>15.0</string>
</dict>
</plist>
PLIST
        fi
    fi

    relative="${so#"$APP"/}"
    mv "$so" "$fw/$module"
    # 原位置留 .fwork（内容为 framework 二进制相对 app 包的路径），
    # framework 里留 .origin（指回 .fwork），两者是 CPython 的 AppleFrameworkLoader 的约定。
    printf 'Frameworks/%s.framework/%s' "$module" "$module" > "${so%.so}.fwork"
    printf '%s.fwork' "${relative%.so}" > "$fw/$module.origin"

    if [ -n "$IDENTITY" ]; then
        # shellcheck disable=SC2086
        codesign --force --sign "$IDENTITY" ${OTHER_CODE_SIGN_FLAGS:-} \
            -o runtime --timestamp=none \
            --preserve-metadata=identifier,entitlements,flags \
            --generate-entitlement-der "$fw"
    fi
    converted=$((converted + 1))
done

if [ "$converted" -eq 0 ]; then
    # 增量构建时 Xcode 可能判定资源未变、没把 .so 重新拷进 bundle，上一次已经
    # 转过的 .fwork 还在——这是正常状态，不能再报错（否则第二次 Run 直接失败）。
    if ls "$DYNLOAD"/*.fwork >/dev/null 2>&1 && [ -d "$APP/Frameworks/_sha1.framework" ]; then
        echo "already converted ($(ls "$DYNLOAD"/*.fwork | wc -l | tr -d ' ') .fwork present), nothing to do"
        exit 0
    fi
    echo "error: $DYNLOAD 里既没有 .so 也没有 .fwork，标准库合并失败" >&2
    exit 1
fi

if [ -n "$IDENTITY" ]; then
    echo "converted $converted binary modules to frameworks (signed)"
else
    echo "converted $converted binary modules to frameworks (unsigned; 装机时重签会一并签掉)"
fi
