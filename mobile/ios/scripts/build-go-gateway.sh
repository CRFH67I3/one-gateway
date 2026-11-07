#!/usr/bin/env bash
# 构建期把外部 Go 网关编译成 iOS 静态库（c-archive），随 App 签名、进程内运行。
#
# iOS 不允许 fork/exec 子进程，也不允许加载未随包签名的代码，所以 Go 网关必须
# 静态链接进来：GoBridge.swift 通过 C 接口 StartGateway/StopGateway 在本进程里
# 起一个 goroutine 跑 http.Server。-buildmode=c-archive 正是为此存在的。
#
# 与 Android 一样：ZIP 只用来取源码，编译完即弃；本仓库不含网关源码，产物
# libgateway.a / .h 由 .gitignore 排除，CI 每次重新生成。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
OUT="$ROOT/mobile/ios/OneGateway/GoGateway"
GO_REPO="misakano7545/workbuddy2api-panel"
GO_BRANCH="main"
URLS=(
  "https://v4.gh-proxy.org/https://github.com/$GO_REPO/archive/refs/heads/$GO_BRANCH.zip"
  "https://gh-proxy.com/https://github.com/$GO_REPO/archive/refs/heads/$GO_BRANCH.zip"
  "https://github.com/$GO_REPO/archive/refs/heads/$GO_BRANCH.zip"
)

command -v go >/dev/null 2>&1 || { echo "error: 找不到 go（需要 1.26.10+）"; exit 1; }
command -v xcrun >/dev/null 2>&1 || { echo "error: c-archive 只能在 macOS + Xcode 上编译"; exit 1; }
echo "Go toolchain: $(go env GOVERSION)"

CC="$(xcrun --sdk iphoneos --find clang)"
SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
echo "iOS SDK: $SDK"
echo "clang:   $CC"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/og-go-ios.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

ZIP="$TMP/gateway.zip"
for url in "${URLS[@]}"; do
  echo "下载 Go 网关源码：$url"
  if curl -sSL --connect-timeout 20 --max-time 180 --retry 2 -o "$ZIP" "$url" \
      && [ -s "$ZIP" ] && unzip -tq "$ZIP" >/dev/null 2>&1; then
    break
  fi
  echo "  该地址不可用，换下一个"
  rm -f "$ZIP"
done
[ -s "$ZIP" ] || { echo "error: 三个下载地址都拿不到 Go 网关 ZIP"; exit 1; }

unzip -q "$ZIP" -d "$TMP/src"
SRC="$(find "$TMP/src" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[ -f "$SRC/go.mod" ] && [ -f "$SRC/cmd/server/main.go" ] || {
  echo "error: ZIP 里没有 go.mod + cmd/server/main.go"; exit 1;
}

python3 "$SCRIPT_DIR/prepare-go-bridge.py" "$SRC"

mkdir -p "$OUT"
export CGO_ENABLED=1
export GOOS=ios
export GOARCH=arm64
export CC
export CGO_CFLAGS="-isysroot $SDK -miphoneos-version-min=15.0 -arch arm64"
export CGO_LDFLAGS="-isysroot $SDK -miphoneos-version-min=15.0 -arch arm64"
export GOPROXY="${GOPROXY:-https://proxy.golang.org,direct}"

echo "编译 c-archive → $OUT/libgateway.a"
( cd "$SRC" && go build -buildmode=c-archive -p 2 -trimpath \
    -o "$TMP/libgateway.a" ./cmd/server )
cp "$TMP/libgateway.a" "$OUT/libgateway.a"
cp "$TMP/libgateway.h" "$OUT/libgateway.h"

grep -q "StartGateway" "$OUT/libgateway.h" || {
  echo "error: 生成的头文件里没有 StartGateway，//export 没生效"; exit 1;
}
grep -q "StopGateway" "$OUT/libgateway.h" || {
  echo "error: 生成的头文件里没有 StopGateway，//export 没生效"; exit 1;
}
echo "完成："
ls -lh "$OUT/libgateway.a" "$OUT/libgateway.h"
