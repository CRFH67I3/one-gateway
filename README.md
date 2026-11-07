# OneGateway

> **本仓库只包含移动端容器外壳，不包含网关。**
> 网关实现（`wb_*.py`、`dashboard.html`、账号池、调度、计费、面板）属于外部项目，
> 由用户在 App 内通过「自动导入 / 手动导入」装载。这里既没有网关源码，也不分发网关包。

把一部 Android / iOS 手机变成可以运行外部网关项目的运行时：内嵌 CPython、前台服务与
后台保活、WebView 面板、项目导入与更新、系统深浅色跟随、通知与实时活动。

名字里的 Gateway 指的是**被装载的那份外部网关项目**，不是这个仓库；仓库本身是宿主，
只负责装载、启动、托管与展示，不含任何网关业务逻辑。名字不再暗示「仓库自带网关」，
也不再绑定某一种网关的形态（中转、转发、代理都可以装）。

## 边界

| 仓库里有什么 | 仓库里没有什么 |
|---|---|
| Android Kotlin 外壳 + Chaquopy CPython | 网关 Python 实现（`wb_*.py`） |
| iOS Swift 外壳 + Python.framework | 面板（`dashboard.html`） |
| 薄运行时、桥接层、解压 / 切指针 / 重启 | 账号池、API Key、配额、用量数据 |
| 构建脚本、CI、容器测试 | Docker / 服务器 / 路由器部署脚本 |

容器与网关之间只有一条契约：**一份可识别的项目 ZIP**。导入层认两种形态：

| 形态 | 外部项目 | 识别标志 | 面板 / 探活 | 手机上怎么跑 |
|---|---|---|---|---|
| Python | `ardeyouxipianyi/workbuddy2api-hub` | `wb_proxy.py` + `dashboard.html` | `/` · `/health` | CPython 本地托管（默认） |
| Go | `misakano7545/workbuddy2api-panel` | `go.mod` + `cmd/server` | `/panel/` · `/healthz` | 只能远程接入（见下） |

ZIP 来自哪里、里面是什么版本，容器不关心；换网关不需要重装 App，升级 App 也不会动
已经导入的网关。两种形态的接入契约集中在 `GatewayProfile.kt` / `GatewayProfile.swift`，
Python 薄运行时只负责识别，不携带任何网关实现。

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

> iOS 的形态不同：它在**构建时**由 `mobile/ios/scripts/sync-core.sh` 从外部项目 ZIP
> 拉取核心注入包内，没有运行时导入界面。换源用 `GATEWAY_CORE_ZIP_URL`，离线构建用
> `GATEWAY_CORE_ZIP` 指一份本地 ZIP；喂进 Go 型 ZIP 会给出「请用远程接入」的明确报错。

## 兼容矩阵与平台硬限制

| | Python 型网关 | Go 型网关 |
|---|---|---|
| ZIP 识别、版本、默认端口 | ✅ | ✅（端口从 `config.example.json` 的 `listen` 读） |
| 两个导入入口（手动 / 自动） | ✅ | ✅（自动源可在设置里切） |
| App 内本地托管 | ✅ 内嵌 CPython | ❌ 平台不允许 |
| 远程接入（面板 + 探活 + 复制 API 地址） | ✅ | ✅ |
| 深浅色跟随 | `?sysdark=` + `wb:system-theme` | `wb2api.theme` + `applyTheme()` 注入 |
| 健康字段 | `accounts` / `accounts_ready` | `total` / `healthy`（503 也算可达） |

Go 型是**单二进制项目**：Android 10+ 禁止从应用私有目录执行未签名 ELF，iOS 只允许
运行签名进包的代码。所以「兼容」不等于「把 Go 网关塞进手机里跑」，而是：容器能识别
它的 ZIP、导入后自动切到远程接入、按它的路径与字段显示面板并探活。把网关跑在电脑、
服务器或本机 Termux，App 里填 `127.0.0.1:7863`（同一台手机）或局域网地址即可。

设置入口：Android「端口设置」、iOS 菜单「远程网关接入…」。可填
`127.0.0.1:7863`、`192.168.1.5:7863` 或 `https://host:port`；省略端口时按形态补
默认值（Python 8788 / Go 7863）。远程开关**默认关**，不改原有本地托管行为；打开后
容器不启动本地进程、不占用前台服务，通知与日志会明确说明网关在别处。

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
│   └── .../GatewayProfile.kt      # 双形态接入契约（路径/字段/主题/地址）
└── ios/                       # Swift 外壳 + Python.framework + 构建脚本
    └── .../GatewayProfile.swift   # 与 Android 逐字对齐的同一份契约
tests/                         # 7 个容器测试套件 + run_all.py
.github/workflows/             # tests / mobile-android / mobile-ios
```

## 构建

Android（JDK 17 + Android SDK platform 37 / build-tools 36，CI 用 Gradle 9.6）：

```sh
cd mobile/android && ./gradlew assembleArm64Release
```

iOS（macOS + Xcode 26 / iOS 26 SDK + XcodeGen）：

```sh
mobile/ios/scripts/sync-core.sh      # 拉取外部网关核心 + 铺 CPython 标准库
cd mobile/ios && xcodegen generate && xcodebuild ...
```

详细步骤、包内布局、二进制扩展转 framework、后台保活等见 [`mobile/README.md`](mobile/README.md)。

## 测试与验证

```sh
python tests/run_all.py              # 跑全部 7 个容器套件
```

当前 **7 个套件（7 个 Python + 0 个 JS）**：自动导入契约、构建工具链、容器纯度、
深浅色换肤、minSdk、UI token、双形态兼容。三条 CI 工作流：`tests`（Python 3.9 / 3.12 ×
Ubuntu / Windows）、`mobile-android`、`mobile-ios`；两条移动端流水线也会在编译前
跑双形态契约套件。加 `WB_LIVE_DUAL_COMPAT=1` 会用两个真实仓库 ZIP 验证识别。

容器纯度测试会直接翻 APK，断言包里没有 `*.py` / `dashboard.html` / `assets/core/`——
这条边界比功能更容易被悄悄破坏。

## 注意

- 包标识是 `com.onegateway.host`；从旧包名升级的设备必须卸载重装。
- iOS 产物是**未签名 IPA**，需自行签名；仓库不含 App 图标。
- 容器不校验导入包的签名或来源，只做路径安全校验（拒绝 `../` 与绝对路径）。
  请自行确认 ZIP 的来源。
- 远程接入模式下 App 只做面板与探活，不托管网关进程；账号数据、用量与日志都在
  运行网关的那一侧，本机的 `data/` 不会被写入。
- 提交规范见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，其中有面向 AI / 智能体的硬性规则。
