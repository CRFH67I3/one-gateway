# OneGateway

> **本仓库只包含移动端容器外壳，不包含网关源码。**
> 网关实现（`wb_*.py`、`dashboard.html`、账号池、调度、计费、面板、Go 的 `cmd/server`）
> 属于外部项目：Python 型由用户在 App 内通过「自动导入 / 手动导入」装载，Go 型在
> **构建期**从外部仓库源码编译成随安装包签名的二进制。仓库里既没有网关源码，
> 也不分发网关二进制；这里只有装载、托管、桥接与展示。

把一部 Android / iOS 手机变成可以运行外部网关项目的运行时：内嵌 CPython、
构建期编译并随包签名的 Go 网关、前台服务与后台保活、WebView 面板、项目导入与
更新、系统深浅色跟随、通知与实时活动。

名字里的 Gateway 指的是**被装载的那份外部网关项目**，不是这个仓库；仓库本身是宿主，
只负责装载、启动、托管与展示，不含任何网关业务逻辑。名字不再暗示「仓库自带网关」，
也不再绑定某一种网关的形态（中转、转发、代理都可以装）。

## 边界

| 仓库里有什么 | 仓库里没有什么 |
|---|---|
| Android Kotlin 外壳 + Chaquopy CPython | 网关 Python 实现（`wb_*.py`） |
| iOS Swift 外壳 + Python.framework | 面板（`dashboard.html`） |
| 薄运行时、桥接层、解压 / 切指针 / 重启 | 账号池、API Key、配额、用量数据 |
| 构建期编译 Go 网关的脚本与 CI 接线 | Go 网关源码、`libgateway.so` / `libgateway.a` 二进制 |
| 构建脚本、CI、容器测试 | Docker / 服务器 / 路由器部署脚本 |

> 注意区分「仓库」与「产物」：仓库零网关源码；安装包里**含有**构建期从外部源码
> 编译、随包签名的 Go 二进制（Android `libgateway.so` / iOS `libgateway.a` 静态链接）。
> 这是两个平台的硬性要求——不能执行下载来的可执行文件。Python 型网关仍然完全
> 来自导入的 ZIP，产物里没有它。

容器与网关之间只有一条契约：**一份可识别的项目 ZIP**。导入层认两种形态：

| 形态 | 外部项目 | 识别标志 | 面板 / 探活 | 手机上怎么跑 |
|---|---|---|---|---|
| Python | `ardeyouxipianyi/workbuddy2api-hub` | `wb_proxy.py` + `dashboard.html` | `/` · `/health` | CPython 本地托管（默认），ZIP 可热更新 |
| Go | `misakano7545/workbuddy2api-panel` | `go.mod` + `cmd/server` | `/panel/` · `/healthz` | 构建期编译、随包签名，两端都本机运行 |

Python 型的 ZIP 来自哪里、里面是什么版本，容器不关心；换网关不需要重装 App。
Go 型的 ZIP 只用于**确认形态与版本**，不能替换二进制——它的版本与 App 版本绑定，
换版本要升级 App。两种形态的接入契约集中在 `GatewayProfile.kt` /
`GatewayProfile.swift`，Python 薄运行时只负责识别，不携带任何网关实现。

## 两个导入入口

首装与日后升级走的是同两条路，界面行为保持不变：

1. **自动导入（联网下载）** — 首装界面下方按钮，装好之后在右上角菜单叫「自动更新」。
   默认下载 `ardeyouxipianyi/workbuddy2api-hub` 的 `main` 分支快照，首选 gh-proxy
   镜像，失败依次退到第二个镜像、直连 github.com；下载完走与手动导入完全相同的
   解压 / 切指针 / 重启流程。设置里勾「自动更新下载 Go 仓库 ZIP」会改下
   `misakano7545/workbuddy2api-panel` 的 `main`；想换成自己的仓库或分支，改
   `GatewayBridge.kt` 的 `REPO` / `BRANCH` 与 `GatewayProfile.kt` 的 `GO_REPO` /
   `GO_BRANCH` 即可。
2. **手动导入（选择压缩包）** — 选一份本地 ZIP，离线可用，也能装任意版本或回退旧版。
   菜单里叫「手动更新」。

导入后项目解压到 App 沙盒 `filesDir/data/projects/<时间戳>/`，**成功才切指针**，
失败时旧项目继续可用；账号等数据放在独立的 `data/{accounts,usage,catalog,logs}`，
覆盖升级不丢配置。面板地址默认 `http://127.0.0.1:8788/`。

> iOS 的 Python 核心形态不同：它在**构建时**由 `mobile/ios/scripts/sync-core.sh`
> 从外部项目 ZIP 拉取核心注入包内，没有运行时导入界面。换源用
> `GATEWAY_CORE_ZIP_URL`，离线构建用 `GATEWAY_CORE_ZIP` 指一份本地 ZIP；喂进 Go 型
> ZIP 会明确提示改用 `build-go-gateway.sh`，并在 App 菜单「本机网关形态…」里切到 Go。
> Go 网关由 `mobile/ios/scripts/build-go-gateway.sh` 在构建期编译成 c-archive 静态库，
> 由 `GoBridge.swift` 在 App 进程内启动。

## 兼容矩阵与平台边界

| | Python 型网关 | Go 型网关 |
|---|---|---|
| ZIP 识别、版本、默认端口 | ✅ | ✅（端口从 `config.example.json` 的 `listen` 读） |
| 两个导入入口（手动 / 自动） | ✅ | ✅（自动源可在设置里切） |
| App 内本地托管 | ✅ 内嵌 CPython，ZIP 热更新 | ✅ 构建期编译、随包签名；ZIP 不能换二进制 |
| 远程接入（面板 + 探活 + 复制 API 地址） | ✅ 可选 | ✅ 可选 |
| 深浅色跟随 | `?sysdark=` + `wb:system-theme` | `wb2api.theme` + `applyTheme()` 注入 |
| 健康字段 | `accounts` / `accounts_ready` | `total` / `healthy`（503 也算可达） |

Go 型是**单二进制项目**。两个平台都禁止执行「下载到可写目录的未签名代码」：
Android 10+ 禁止从应用私有目录 `execve()`，iOS 只允许运行签名进包的代码。
所以 Go 网关不走 ZIP 热更新，而是在**构建期**从同一个外部仓库源码编译：

- **Android**：`mobile/android/scripts/build-go-gateway.sh` 用
  `CGO_ENABLED=0 GOOS=android` 交叉编译 arm64 / x86_64 两份 PIE ELF，以
  `libgateway.so` 放进 `jniLibs/<abi>/`；安装器解压到只读的
  `/data/app/.../lib/<abi>/`，`GoGateway.kt` 从 `nativeLibraryDir` 起进程。
- **iOS**：`mobile/ios/scripts/build-go-gateway.sh` 先用
  `prepare-go-bridge.py` 把 CLI `main()` 改造成 `//export StartGateway` /
  `StopGateway`，再以 `-buildmode=c-archive` 编成 `libgateway.a` 静态链接进 App；
  `GoBridge.swift` 在进程内起 goroutine 跑 `http.Server`，`StopGateway()` 通过
  context 取消走优雅停机。

两端共用一个沙盒配置（随机 API Key、绝对 `auth_dir` / `state_file`、关掉自更新
与后台外连任务）。远程接入仍保留，用于连电脑 / 服务器上已运行的实例，但已从
「Go 的唯一路径」降级为可选连接方式；开关**默认关**。

设置入口：Android「端口设置」（含「本机形态」开关）、iOS 菜单「远程网关接入…」
与「本机网关形态…」。本机形态开关让 Go 网关不用先导入 ZIP 就能直接运行；
地址可填 `127.0.0.1:7863`、`192.168.1.5:7863` 或 `https://host:port`，省略端口时
按形态补默认值（Python 8788 / Go 7863）。打开远程开关后容器不启动本地进程、
不占用前台服务，通知与日志会明确说明网关在别处。

## 版本

`mobile/VERSION` 是容器版本的唯一来源，Android 与 iOS 都读它，与导入的网关版本
彼此独立。Android `versionName` = 前两段 + 8 位构建日期 + ABI 后缀（如
`1.10.20261107-arm64`），`versionCode` 只由 `mobile/VERSION` 计算。

## 目录

```
mobile/
├── VERSION                    # 容器版本（Android / iOS 共用）
├── README.md                  # 移动端构建与实现细节
├── shared/wb_mobile_runtime.py    # 共享运行时：托管导入项目的 start/stop/status
├── android/                   # Kotlin 外壳 + Chaquopy CPython + 薄运行时
│   ├── scripts/build-go-gateway.sh   # 构建期交叉编译 libgateway.so（两个 ABI）
│   └── .../GatewayProfile.kt      # 双形态接入契约（路径/字段/主题/地址）
└── ios/                       # Swift 外壳 + Python.framework + 构建脚本
    ├── scripts/build-go-gateway.sh   # 构建期生成 c-archive 静态库
    ├── scripts/prepare-go-bridge.py  # 把 Go CLI 改造成 //export 入口
    ├── .../GoBridge.swift             # Swift ⇄ Go C 接口，进程内启停
    └── .../GatewayProfile.swift      # 与 Android 逐字对齐的同一份契约
tests/                         # 7 个容器测试套件 + run_all.py
.github/workflows/             # tests / mobile-android / mobile-ios
```

## 构建

两端都需要 **Go 1.26.10**（与上游 `go.mod` 一致）；CI 用 `actions/setup-go@v5`。
Android 的 x86_64 那份还需要 **Android NDK**（Go 的内部链接器不给
`android/amd64` 产 PIE，必须走 cgo 外部链接；arm64 是纯 Go 静态 PIE）。
iOS 侧需要 macOS + Xcode 的 clang。

Android（JDK 17 + Android SDK platform 37 / build-tools 36，CI 用 Gradle 9.6）：

```sh
bash mobile/android/scripts/build-go-gateway.sh   # 产出 jniLibs/*/libgateway.so
cd mobile/android && ./gradlew assembleArm64Release
```

iOS（macOS + Xcode 26 / iOS 26 SDK + XcodeGen）：

```sh
mobile/ios/scripts/sync-core.sh                  # 拉取外部 Python 核心 + 铺 CPython 标准库
bash mobile/ios/scripts/build-go-gateway.sh      # 产出 GoGateway/libgateway.a + .h
cd mobile/ios && xcodegen generate && xcodebuild ...
```

`libgateway.so` / `libgateway.a` / `libgateway.h` 都由 `.gitignore` 排除、每次构建
重新生成。详细步骤、包内布局、二进制扩展转 framework、后台保活等见
[`mobile/README.md`](mobile/README.md)。

## 测试与验证

```sh
python tests/run_all.py              # 跑全部 7 个容器套件
```

当前 **7 个套件（7 个 Python + 0 个 JS）**：自动导入契约、构建工具链、容器纯度、
深浅色换肤、minSdk、UI token、双形态兼容（39 例，含 iOS Go 桥接源码变换的行为测试）。
三条 CI 工作流：`tests`（Python 3.9 / 3.12 × Ubuntu / Windows）、`mobile-android`、
`mobile-ios`；两条移动端流水线也会在编译前跑双形态契约套件，并各自编译 Go 网关。
加 `WB_LIVE_DUAL_COMPAT=1` 会用两个真实仓库 ZIP 验证识别。

容器纯度测试会直接翻 APK，断言包里没有 `*.py` / `dashboard.html` / `assets/core/`，
同时要求 `lib/<abi>/libgateway.so` 存在；iOS 流水线校验 `libgateway.a` 里的
`StartGateway` / `StopGateway` 符号与 App 包内资源齐备。这条边界比功能更容易被
悄悄破坏。

## 注意

- 包标识是 `com.onegateway.host`；从旧包名升级的设备必须卸载重装。
- iOS 产物是**未签名 IPA**，需自行签名；仓库不含 App 图标。
- 容器不校验导入包的签名或来源，只做路径安全校验（拒绝 `../` 与绝对路径）。
  请自行确认 ZIP 的来源。下载来的可执行文件**永远不会**被执行：Go 网关只来自
  构建期编译、随包签名的那一份。
- Go 网关版本与 App 版本绑定，两个更新入口对它只刷新形态与版本信息；要换 Go
  版本请升级 App。Python 网关仍可导入 ZIP 热更新。
- 远程接入模式下 App 只做面板与探活，不托管网关进程；账号数据、用量与日志都在
  运行网关的那一侧，本机的 `data/` 不会被写入。
- 提交规范见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，其中有面向 AI / 智能体的硬性规则。
