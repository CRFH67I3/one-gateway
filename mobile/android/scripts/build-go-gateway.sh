#!/usr/bin/env bash
# 构建期把外部 Go 网关源码交叉编译成随 APK 签名的可执行文件。
#
# 为什么走这一步：Android 10+（targetSdk>=29）禁止对可写的应用私有目录
# execve()，官方要求「只加载嵌入 APK 的二进制代码」。把 Go 网关在构建期编成
# PIE ELF、以 libgateway.so 之名放进 jniLibs/<abi>/，安装器会把它解压到只读的
# /data/app/.../lib/<abi>/（SELinux 标签 apk_data_file，允许执行）。运行时由
# GoGateway.kt 用 ProcessBuilder 启动；下载 ZIP 那条路换来的是 W^X 拒绝。
#
# 本脚本只产出二进制，不把网关源码放进仓库：ZIP 下载到临时目录，编完即弃。
# 产物由 .gitignore 排除，CI 每次构建重新生成，Go 网关版本因此与 App 版本绑定。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
OUT="$ROOT/mobile/android/app/src/main/jniLibs"

GO_REPO="misakano7545/workbuddy2api-panel"
GO_BRANCH="main"
GO_MIN_VERSION="1.26.0"
URLS=(
  "https://v4.gh-proxy.org/https://github.com/$GO_REPO/archive/refs/heads/$GO_BRANCH.zip"
  "https://gh-proxy.com/https://github.com/$GO_REPO/archive/refs/heads/$GO_BRANCH.zip"
  "https://github.com/$GO_REPO/archive/refs/heads/$GO_BRANCH.zip"
)

command -v go >/dev/null 2>&1 || { echo "error: 找不到 go，请先安装 Go $GO_MIN_VERSION+"; exit 1; }
GO_VERSION="$(go env GOVERSION)"
echo "Go toolchain: $GO_VERSION"
case "$GO_VERSION" in
  go1.26.*|go1.27.*|go1.28.*|go1.29.*) ;;
  *) echo "::warning::go.mod 要求 go 1.26.10，当前是 $GO_VERSION；若编译失败请升级工具链" ;;
esac

TMP="$(mktemp -d "${TMPDIR:-/tmp}/og-go-gateway.XXXXXX")"
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
  echo "error: ZIP 里没有 go.mod + cmd/server/main.go，不是预期的 Go 网关仓库"; exit 1;
}
echo "源码：$SRC（$(du -sh "$SRC" | cut -f1)）"

mkdir -p "$OUT/arm64-v8a" "$OUT/x86_64"
export CGO_ENABLED=0
export GOOS=android
export GOPROXY="${GOPROXY:-https://proxy.golang.org,direct}"

build_abi() {
  local abi="$1" arch="$2" machine="$3"
  local dest="$OUT/$abi/libgateway.so"
  echo "编译 $abi（GOARCH=$arch）→ $dest"
  ( cd "$SRC" && GOARCH="$arch" go build -p 2 -trimpath -ldflags="-s -w" \
      -o "$dest" ./cmd/server )
  python3 - "$dest" "$machine" <<'PY'
import struct, sys
path, want = sys.argv[1], int(sys.argv[2], 16)
data = open(path, 'rb').read(20)
assert data[:4] == b'\x7fELF', f'{path}: 不是 ELF'
assert data[4] == 2, f'{path}: 不是 64 位'
etype, machine = struct.unpack_from('<HH', data, 16)
assert etype == 3, f'{path}: e_type={etype}，Android 需要 PIE (ET_DYN)'
assert machine == want, f'{path}: machine=0x{machine:x}，期望 0x{want:x}'
print(f'  ELF OK: PIE, machine=0x{machine:x}, {len(open(path,"rb").read())} bytes')
PY
}

build_abi arm64-v8a arm64 b7
build_abi x86_64 amd64 3e

# 记录本次构建用的上游快照，便于排障；只写进构建目录，不进 APK、不入库。
mkdir -p "$ROOT/mobile/android/app/build"
{
  echo "repo=$GO_REPO"
  echo "branch=$GO_BRANCH"
  echo "go=$(go env GOVERSION)"
  echo "built=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$ROOT/mobile/android/app/build/go-gateway-source.txt"

echo "完成："
find "$OUT" -name 'libgateway.so' -printf '  %p (%k KB)\n' 2>/dev/null \
  || ls -l "$OUT"/*/libgateway.so
