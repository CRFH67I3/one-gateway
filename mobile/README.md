# 移动端容器（iOS / Android）

> **这个仓库只含容器，不含网关源码。** 网关实现（`wb_*.py` / `dashboard.html`、
> 账号池、调度、计费、面板）属于外部项目：Android 的 Python 型在首装时导入，
> iOS 的 Python 型在构建时从外部 ZIP 注入。仓库没有任何网关源码或二进制，
> 也不分发网关包。

容器只做三件事：**装载**（项目 ZIP → App 沙盒）、**托管**（内嵌 CPython 启动
Python 网关、前台服务 / 后台保活）、**展示**（WebView 面板、通知、实时活动）。

Android 是**运行时导入** Python 型：APK 里只有 CPython 运行时与薄运行时，首装界面
两个入口把外部网关项目装进沙盒（详见「Android 的容器模式」）。iOS 是**构建时注入**
Python 型：`sync-core.sh` 从外部项目 ZIP 拉取核心放进包内，随包交付，没有运行时
导入界面。曾短暂支持的「构建期编译、随包签名 Go 网关」已整体回退（详见
「容器契约与远程接入」）。

> **实现方式：Vibe Coding**
>
> `mobile/` 下的全部代码——Kotlin 外壳、Swift 外壳、`wb_mobile_runtime.py`、
> Gradle / XcodeGen 工程描述、构建脚本与 CI 工作流——都是 **Vibe Coding**
> 的产物：通过对话描述需求、由 AI 直接生成并反复迭代，几乎没有逐行人工审查。
> 因此属于**实验性代码**：两端都能构建、能在设备上跑通主链路，但异常分支、
> 边界处理与长期可维护性没有经过人工 review。用于生产或分发前请自行评估。

```
mobile/
├── shared/
│   └── wb_mobile_runtime.py     # iOS 启动器：沙盒目录、核心同步、证书、start/stop/status
├── android/                     # Kotlin 外壳 + Chaquopy（内嵌 CPython 3.13）
│   ├── app/src/main/
│   │   ├── java/com/onegateway/host/
│   │   │   ├── HubApp.kt            # Application：初始化 CPython
│   │   │   ├── GatewayBridge.kt     # Kotlin ⇄ Python 通道、项目解压与指针、自动下载
│   │   │   ├── GatewayProfile.kt    # 接入契约：路径 / 字段 / 远程地址
│   │   │   ├── GatewayService.kt    # 前台服务 + 常驻通知（含端口顺延）
│   │   │   ├── MainActivity.kt      # WebView 加载面板 + 原生菜单
│   │   │   └── Prefs.kt             # 端口 / 局域网 / 自启 / 当前项目
│   │   └── python/wb_hub_env.py     # 容器运行时：解压 ZIP、找项目根、托管网关
│   └── app/build.gradle.kts         # 版本号从 mobile/VERSION 读取；不打包网关核心
└── ios/                         # Swift 外壳 + 内嵌 Python.framework
    ├── project.yml                  # XcodeGen 工程描述
    ├── scripts/sync-core.sh         # 拉取外部 Python 核心 + 铺标准库 + 装 certifi
    ├── scripts/install-python.sh    # 构建后：lib-dynload 的 .so 转 Frameworks/*.framework
    ├── OneGateway/Sources/
    │   ├── PyBridge.swift           # CPython C API 桥（GIL、PYTHONHOME/PYTHONPATH）
    │   ├── RootViewController.swift  # WKWebView 面板 + 前后台让位
    │   ├── BackgroundKeepAlive.swift # iOS 26 持续后台任务（BGContinuedProcessingTask）
    │   ├── LiveActivityController.swift  # 锁屏/灵动岛实时活动（ActivityKit）
    │   ├── BackgroundSettingsViewController.swift  # 「后台保持」三个开关
    │   ├── CorePaths.swift          # 沙盒路径 / 外壳开关（含远程接入、端口顺延）/ 局域网地址
    │   ├── GatewayProfile.swift     # 与 Android 对齐的接入契约
    │   ├── OneGateway-Bridging-Header.h  # #include <Python/Python.h>
    │   └── AppDelegate.swift
    ├── Shared/GatewayActivityAttributes.swift  # 主 App 与 Widget 共享的活动定义
    ├── OneGatewayWidget/          # Widget Extension：Live Activity 的锁屏/灵动岛视图
    ├── OneGateway/PythonBridge/wb_hub_ios.py
    └── OneGateway/Resources/       # 构建时生成（不入库）
        ├── app/                      # 核心代码 + 桥接层 + certifi → PYTHONPATH
        └── python/lib/python3.13/    # CPython 标准库 → PYTHONHOME
```

## 为什么是「内嵌 Python」而不是重写

被装载的网关是一万三千行的纯标准库 Python（账号池、双区域调度、计费、用量、
面板接口、上游转发、指纹与风控细节），但它**不在本仓库里**。容器既不重写它，
也不打包它的源码，只内嵌 CPython 让它原样跑起来：换网关只要换一份 ZIP，
不需要改外壳，也不存在两套实现长期漂移。

- Android：Chaquopy 16.1 + CPython 3.13（`libpython3.13.so` + OpenSSL + sqlite3）；
- iOS：BeeWare [python-app-support](https://github.com/beeware/Python-Apple-support)
  的 `Python.xcframework`，以 `Python.framework` 形式随包嵌入。

包内布局照 CPython 官方 [Using Python on iOS](https://docs.python.org/3/using/ios.html)
的 `install_python` 约定，**这个形状不能改**：

| 包内路径 | 内容 | 用途 |
|---|---|---|
| `app/` | `wb_*.py`、`dashboard.html`、`wb_hub_ios.py`、`certifi/` | `PYTHONPATH` |
| `python/lib/python3.13/`（含 `lib-dynload`） | CPython 标准库 | `PYTHONHOME` |
| `Frameworks/_sha1.framework` 等 68 个 | 标准库的二进制扩展（`_hashlib`、`_socket`、`_ssl`…） | App 内 `dlopen` |
| `Frameworks/Python.framework` | 解释器本体（XCFramework 切片） | 链接 + 嵌入 |

标准库必须落在 `python/lib/pythonX.Y` 这一层：CPython 的 getpath 把 `PYTHONHOME`
当 prefix、只认 `prefix/lib/pythonX.Y`。平铺在 `Resources/python` 下解释器能起来，
但一 import 就报 `No module named 'encodings'`。

**二进制扩展必须转成 framework**。iOS 不允许 app 包里出现 `Frameworks/` 之外的
可执行二进制，`lib-dynload` 里的 `.so` 即使拷进去也不会被签名，`dlopen` 一律失败。
症状是启动即报 `AttributeError: module 'hashlib' has no attribute 'sha1'`——CPython
的 hashlib 要么走 `_hashlib`、要么走 `_sha1`，都是扩展模块；同样废掉的还有
`_socket` / `_ssl` / `_json`，网关连端口都监听不了。所以 `install-python.sh` 在
构建后把每个 `.so` 移进 `Frameworks/<名字>.framework/`，并在原位置留一个 `.fwork`
指针、在 framework 里留 `.origin` 指回来（CPython `AppleFrameworkLoader` 的约定）。
支持包的这两部分是分开的：`lib/python3.13` 只有纯 Python，68 个 `.so` 在
`ios-arm64/lib-arm64/python3.13/lib-dynload` 下，`sync-core.sh` 会把两者合并。

面板同理：`dashboard.html` 是「同源静态页 + JSON 接口」，WebView 直接打开
`http://127.0.0.1:8788/` 就等于移植了全部界面与交互，面板以后加功能 App 不用改。

## 容器契约与远程接入

容器只运行 Python 型外部网关，契约收在 `GatewayProfile.kt`（Android）/
`GatewayProfile.swift`（iOS）里：

| 契约 | 值 |
|---|---|
| 识别标志 | `wb_proxy.py`（Go 源码包 `go.mod` + `cmd/server` 会被明确拒绝） |
| 面板 / 探活 | `/` · `/health` |
| 健康字段 | `accounts` / `accounts_ready` |
| 默认端口 | 8788 |
| 深浅色 | `?sysdark=` + `wb:system-theme` 事件 |

**为什么 Go 形态被回退**：曾实现过「构建期交叉编译、随包签名」的 Go 网关
（Android `libgateway.so` 子进程 / iOS c-archive 静态库进程内运行），它能绕开
「禁止执行下载来的 ELF」的平台限制，但代价是 Go 上游每次更新都要重新构建、
分发并重装整个 App——热更新彻底失效；而「拉源码到设备上编译」在两个平台上
都不可行（Android 10+ 的 W^X 禁止从可写目录 exec 新产物，iOS 只允许运行
签名进包的代码）。权衡后回退为纯 Python 托管：Go 源码包由 `install()` 拒绝
并给出可操作提示。

**远程接入**（可选，默认关）用于连电脑 / 服务器上已运行的实例。打开后：

- Android `GatewayService` 与 iOS 的 CPython 都不启动本地进程；
  `fetchHealth` / `refreshHealth` 打远程地址；
- 面板地址、复制的 API 地址、通知与关于页都指向远程 origin；日志页提示去网关那侧看；
- 深浅色：Android 在 `onConfigurationChanged`、iOS 在 `traitCollectionDidChange`
  把系统值用 `wb:system-theme` 事件派给面板；
- 设置：Android「端口设置」里的远程开关 / 地址；iOS 菜单「远程网关接入…」。
  地址支持 `127.0.0.1:8788`、局域网 IP 与 `https://host:port`，省略端口补 8788。

**端口被占用时自动顺延**（默认关，Android「端口设置」/ iOS 菜单里的开关）：
开启后启动前先探一次回环 TCP，预设端口被占（Termux 里另跑一份网关是典型）就
+1 递增、最多试 10 个，实际生效端口写回设置，状态栏、面板与「复制 API 地址」
自然跟随。关闭时维持原行为：直接报「端口被占用」让人手动处理。Android 在
`GatewayService.resolvePort()`，iOS 在 `Settings.effectivePort()`。

`tests/_test_gateway_container.py` 用同构 ZIP 真调 `install()`（Python 包识别、
Go 包拒绝），静态比对三端的路径、字段、端口与「容器不携带网关源码 / Go 产物」；
还单独执行 iOS 抽取脚本，确保 Go ZIP 得到的是可操作的诊断。

## 两端各自适配了什么

| 主题 | Android | iOS |
|---|---|---|
| 进程存活 | 前台服务 + 常驻通知，息屏/切后台链路不断 | iOS 26+：持续后台任务（`BGContinuedProcessingTask`）让网关在后台继续监听；更早系统上 20 秒宽限后关闭监听，回前台自动恢复 |
| 锁屏/灵动岛 | — | 可选的实时活动（Live Activity）：锁屏卡片 + 灵动岛，两个独立开关 |
| 控制入口 | 通知栏「停止」、App 内菜单（重启网关 / 端口设置 / 导入凭证 / 复制 API 地址 / 日志 / 关于 / 退出） | App 内菜单（含「后台保持」设置页） |
| 面板 | 系统 WebView + 下拉刷新 | `WKWebView` |
| 面板 JS 弹窗 | `WebChromeClient` 把 `alert/confirm/prompt` 接到原生对话框；`target=_blank` 在同一 WebView 打开 | `WKUIDelegate` 提供同样的三个回调 |
| 深色模式 | 面板「跟随系统」按外壳传进来的 `?sysdark=` 取值（见下），原生外壳走 `values-night` 资源 | `WKWebView` 跟随系统外观；系统值另用 `wb:system-theme` 事件派给面板 |
| 接入契约 | `GatewayProfile.kt` + 设置里的远程开关 / 地址 | `GatewayProfile.swift` + 菜单「远程网关接入…」 |
| 存储 | `filesDir/data`（accounts/usage/catalog/logs） | `Library/Application Support/data` |
| 明文回环 | `network_security_config` 全局放行（远程 LAN 也能连） | ATS 例外 + `NSAllowsLocalNetworking` + 用户自填地址的明文例外 |
| HTTPS 根证书 | Android 系统证书目录（`SSL_CERT_DIR`） | 随包 `certifi`（iOS 无系统 CA 路径） |
| 局域网访问 | 绑 0.0.0.0，通知里给 IP；自动生成 API Key | 同左，另需「本地网络」权限 |
| 版本号 | Gradle 读 `mobile/VERSION` | CI 读同一份并注入 `MARKETING_VERSION` |

**Android 的「跟随系统」为什么要单独走一条通道**：`targetSdk 33+` 的 WebView 是按
**App 主题的 `isLightTheme`** 决定 `prefers-color-scheme` 的（[Android 13 行为变更](https://developer.android.google.cn/about/versions/13/behavior-changes-13)），
不是手机的系统设置。外壳原来固定 `Theme.Material.Light`，于是手机切深色时 WebView
照样报 `light`，面板里选「跟随系统」和选「浅色」没有区别。现在两层都对上：

- 外壳按 `uiMode` 判断系统深浅，把 `?sysdark=1` / `?sysdark=0` 拼进面板地址，页面脚本在
  首帧之前就读到（并缓存——面板里有 `history.replaceState` 会重写 URL）；浏览器里没有
  这个参数，仍用 `prefers-color-scheme`，桌面行为不变；
- `values-night/` 提供深色档的主题与配色，状态栏图标、顶栏、导航栏和 WebView 首帧底色
  一起沉下去，不会在深色面板顶上留一条白。

切深浅时 Activity **不重建**（清单放行了 `uiMode`）：重建会把 WebView 一起销毁，而面板
的登录态活在页面内存里（不是 cookie），用户只是把手机调成深色，回来就得重新输密码——
真机实测掉过两次，而且系统深色是很多人的常开设置，不是一次性操作。所以改由
`onConfigurationChanged` 原地换肤：重刷原生那一圈颜色（XML 里的 `@color` 不会随配置
自动更新），再把新值用 `wb:system-theme` 派给页面，页面不重载、滚动位置和会话都保住。

细节上有一处容易漏：换肤后 URL 里的 `sysdark` 还是旧值，必须同步 `loadedUrl`，否则
2.5 秒一次的状态轮询会发现 `loadedUrl != panelUrl` 并立刻 `loadUrl`，等于白换肤。

旋转仍然走重建（`configChanges` 只有 `keyboardHidden|uiMode`）：WebView 不重建就会留着
上一次的 pinch 缩放，横屏右侧按钮被裁掉。

## Android 外壳与面板共用一套外观

原生那层（顶栏 + 覆盖层）和 WebView 里的面板拼在同一个屏幕上：状态栏压着面板顶栏，
覆盖层直接盖在面板区域上。两边各写一套颜色，看上去就是两个 App 拼起来的，所以外壳
的颜色资源直接对应面板新界面（`html[data-ui="next"]`，手机上看到的就是这套）的
CSS token，改名/改值时两边一起动：

| 外壳资源 | 面板 token | 用在哪 |
|---|---|---|
| `@color/page_bg` | `--bg` | 页面底色、覆盖层、WebView 首帧底色 |
| `@color/header_bg` | `--panel` | 顶栏、状态栏、导航栏 |
| `@color/text_primary` / `@color/text_secondary` | `--fg` / `--dim` | 顶栏状态、提示文案 |
| `@color/colorPrimary` | `--accent` | 系统对话框强调色、主按钮 |

> 面板里还有一套旧的 `:root` token（暖米 + 陶土，只在旧界面下生效）。外壳对齐的是
> next 那套（冷灰 + 靛蓝），别照着 `:root` 改，否则真机上又对不上。

按钮只有一套组件，两个变体：`Hub.Button.Primary`（实心 accent）与
`Hub.Button.Secondary`（panel 底 + 1dp line 描边）。高度、最小宽度、圆角、字号都在
`Hub.Button` 与 `values/dimens.xml` 里定义一次，布局里只写 `style=`，不要再给单个
按钮补 `android:textSize` / `background` / `textColor`——首装界面那两个「灰块 + 裸文字」
就是各写各的后果。另外 `Hub.Button` 必须清掉 `backgroundTint`：`borderlessButtonStyle`
还带着主题的 `colorButtonNormal`，不清掉会把自绘的圆角背景整块染成系统灰。

`tests/_test_android_ui_tokens.py` 同时盯两件事：按钮有没有在布局里分叉、外壳配色
有没有跟面板 token 跑偏。面板属于外部项目，仓库里没有它的文件，所以这套 token 以
**显式契约**写在测试里；外部面板换肤时，同步改契约与 `colors.xml`。

## iOS 后台保持与实时活动

App 内菜单 →「后台保持」有三个开关，对应三种手段：

| 开关 | 机制 | 关掉之后 |
|---|---|---|
| 持续后台处理 | iOS 26 的 `BGContinuedProcessingTask`：切到后台后系统继续给进程后台时间，并自动在锁屏/灵动岛生成一张带进度的系统卡片（用户可在卡片上取消） | 回到旧行为：进后台 20 秒后停监听 |
| 锁屏实时活动 | 自己发起的 Live Activity（ActivityKit），锁屏上一张卡片显示端口、账号可用数、状态 | 立即 `end(.immediate)`，整张活动（含灵动岛）一起消失 |
| 灵动岛显示 | 同一张 Live Activity 的紧凑/展开视图 | 灵动岛不再渲染内容，锁屏卡片保留 |

几个实现上的硬约束，改动时别踩：

- `BGTaskScheduler.register` 必须在 `didFinishLaunching` 里、且 identifier 出现在
  Info.plist 的 `BGTaskSchedulerPermittedIdentifiers`（同时要有 `UIBackgroundModes:
  processing`），否则注册即抛异常 / 提交被拒；
- 持续后台任务的**请求只能在 App 前台、且由用户操作触发**时提交。所以提交点有两个：
  打开开关的那一刻（最稳，App 明确在前台），以及 `willResignActive`（用户刚切走、
  还没进后台）作为兜底。放进 `didEnterBackground` 一定失败；不用 GPU 就不需要额外
  entitlement（`Background GPU Access` 只在 `requiredResources = .gpu` 时才要）；
- 必须持续上报 `task.progress`：长时间不动会被判定卡死，系统会引导用户取消。这里用
  「一片 1800 秒、跑满再续一片」让进度单调递增、永远不到 100%（到 100% 系统会认为
  任务完成，收回后台时间）；
- 实时活动的锁屏与灵动岛是**同一张活动**，只能在 App 前台 `request`。所以「锁屏实时活动」
  实际是总开关，关掉即 `end`；「灵动岛显示」通过 `ContentState` 里的标志位控制视图，
  这样不用开 App Group（Widget Extension 直接读 `context.state`）；
- Live Activity 的视图在 `OneGatewayWidget` 这个 app-extension 里，主包必须把
  `.appex` 嵌进 `PlugIns/`，否则卡片不会出现；
- 未签名 IPA 里 appex 也是未签名的，用 AltStore / Sideloadly 重签时会一起处理嵌套 bundle；
- 主 target 的部署版本是 **iOS 15**，而 ActivityKit 要 16.1，所以 `GatewayActivityAttributes`
  带 `@available(iOS 16.1, *)`，控制器里 `Activity` / `ContentState` 用 `Any?` 擦除存取
  （与 `BGContinuedProcessingTask` 存成 `AnyObject` 同理）。直接写成存储属性会报
  `'Activity' is only available in iOS 16.1 or newer`。另外 `Task` / `DispatchQueue`
  的逃逸闭包**不继承**外层可用性，闭包体里要再判一次 `#available`。

## 构建

### Android

```bash
# 需要 JDK 17、Android SDK（platform 37 + build-tools 36）；无需本机装 Gradle，CI 用 9.6
cd mobile/android
gradle :app:assembleDebug        # 或 :app:assembleRelease
# 产物：app/build/outputs/apk/<flavor>/<buildType>/app-<flavor>-<buildType>.apk
```

**工具链口径**：`compileSdk = 37`（Android 17）、`targetSdk = 34`、`minSdk = 26`，
AGP 9.4.1 + Gradle 9.6 + Chaquopy 17.0.0。`compileSdk` 只决定「编译器能看见哪些新 API」，
运行时行为由 `targetSdk` 决定，所以可以跟着平台往上走；但 AGP 对它有硬上限（9.4 → API 37），
升级平台必须同时升 AGP，否则会报 unsupported compileSdk。

AGP 9 有两处和 8.x 不一样，改动构建脚本时容易踩：

- **Kotlin 内建**：不再声明 `org.jetbrains.kotlin.android`，编译器由 AGP 自带的 KGP 提供
  （9.4 的底线是 2.2.10），`android { kotlinOptions {} }` 这个 DSL 也随之删除，`jvmTarget`
  默认对齐 `compileOptions.targetCompatibility`。
- **`resValues` 变成显式开关**：`defaultConfig.resValue("string", ...)`（外壳用它生成
  `app_version_name`）属于「自定义资源值」，AGP 9 默认关闭，必须在
  `buildFeatures { resValues = true }` 里打开，否则配置阶段就报
  「defaultConfig contains custom resource values, but the feature is disabled」。

**最低支持 Android 8.0（`minSdk = 26`）**。外壳的两处地基都假定这一档：
常驻通知用通知渠道（`NotificationChannel`），服务启动用 `startForegroundService()`。
停在 24/25 就得为它们各写一条旧路径，而 8.0 以下机型又跑不动 Chaquopy 的
Python 3.13 与现代 WebView，所以底线直接抬到 26；`targetSdk = 34`。

**每个 ABI 单独出包**（flavor 维度 `abi`）：

| flavor | ABI | 用途 | 大致体积 |
|---|---|---|---|
| `arm64` | `arm64-v8a` | 真机（现在几乎所有 Android 手机） | ~19 MB |
| `x64` | `x86_64` | 模拟器 / x86_64 设备 | ~19 MB |

**两种构建类型刻意不同**：

| buildType | 混淆/裁剪 | 用途 |
|---|---|---|
| `debug` | 全量：`isMinifyEnabled = false`、`isShrinkResources = false` | 调试、抓栈、反编译对类名与 `R` 资源 |
| `release` | 最小化：R8 裁代码 + 资源裁剪 + 名字压缩 | 发布/日常安装，包更小、反编译更难读 |

release 的保留规则在 `app/proguard-rules.pro`：Chaquopy 的 `com.chaquo.python.**` 整份保留
（解释器在运行时按名字查类），`HubApp` / `MainActivity` / `GatewayService` 由系统按类名
实例化所以整份保留，外壳其余类只 `-keepnames`（保名字、成员照裁）。网关本身是纯 Python
且不 import 任何 Java 类，所以不需要为「Python 调 Java」额外保什么。

- 拆包用的是 product flavor + 每个 flavor 只声明一个 `abiFilters`，**不是** `splits { abi }`
  或 App Bundle：Chaquopy 把解释器和标准库按 ABI 拆成三套资产
  （`libpython3.13.so`、`assets/chaquopy/stdlib-<abi>.imy`、
  `assets/chaquopy/bootstrap-native/<abi>/`），splits 只过滤 `lib/`，assets 里另一套
  ABI 的副本照样留在包内。官方 FAQ 也写明 splits 与 App Bundle 对 Chaquopy 帮助不大。
- 因此 `ndk.abiFilters` **只能写在 flavor 里**：它与 `defaultConfig` 是取并集，
  留在 `defaultConfig` 会让两个 flavor 都拿到两个 ABI，拆包白做。
- 合并后的包从 34.7 MB 降到约 19 MB（省掉另一套 native 库 + 标准库，以及容器模式不再携带的网关源码）。
- `versionName` 形如 `1.10.20261107-arm64`：前两段取 `mobile/VERSION`，
  **第三段是构建日期（8 位 `yyyyMMdd`）**，末尾是 ABI 后缀。日期段
  让人一眼看出这个包是哪天出的、跨天自然递增；同日多次构建版本名相同（`versionCode`
  仍由 `mobile/VERSION` 算，不随日期漂移，覆盖安装不受影响）。`versionCode` 两个 flavor
  相同，同一台设备本来也只能装一个。
- CI 会逐个包校验「只有一套 ABI 原生库 + 一份 ABI 专属标准库」，拆包没生效直接失败。

- **不再把网关源码打进 APK**：容器模式下 `assets/core/` 不再生成，构建只负责把
  `src/main/python/wb_hub_env.py` 与 Chaquopy 运行时塞进去；
- 装机后**不会**解压任何随包核心：首次启动只会显示「尚未导入网关项目」，装网关必须
  走「手动更新」或「自动更新」（详见下文「Android 的容器模式」）；
- 没有提供 keystore 时 release 包用 debug 签名，保证可直接 sideload；要出正式包就设
  `WB_ANDROID_KEYSTORE` / `WB_ANDROID_KEYSTORE_PASSWORD` / `WB_ANDROID_KEY_ALIAS` /
  `WB_ANDROID_KEY_PASSWORD`；
- **CI 的 debug 签名是固定的**：仓库 secret `WB_ANDROID_DEBUG_KEYSTORE` 存了一份
  keystore 的 base64，构建前还原并由 `WB_ANDROID_DEBUG_KEYSTORE` 指给 Gradle，
  所以多次构建的证书一致、可以覆盖安装。注意 secret 里必须是 base64 文本——
  直接存二进制会让工作流在 startup 阶段失败（Secret 值必须是合法 UTF-8）；
  另外别指望 `~/.android/debug.keystore`：runner 的 `ANDROID_SDK_HOME` 与 `$HOME`
  不一致时 AGP 会忽略它、自己生成随机密钥。本地不设该变量时仍用各自的 debug keystore；
- 装机后首次启动**不会**解压任何随包核心：没有导入过项目时只显示「尚未导入网关项目」，
  装网关必须走「手动更新」或「自动更新」（见下文「Android 的容器模式」）。

### Android 的容器模式

APK 是**运行环境**，不是网关本身。装网关只有一件事——把一份仓库代码放进去——
区别只在代码从哪来，所以菜单里就两个动作：**手动更新**（你选一个 ZIP）与
**自动更新**（联网下仓库 main）。首次安装与日后升级走的是同两条路，不需要任何
凭证，也没有「先导入项目」这种前置步骤。导入层只认 Python 型 ZIP；Go 源码包
会被明确拒绝（见上文「容器契约与远程接入」），Python 型由沙盒里的 CPython 起网关。

**一、自动更新（推荐）**

打开 App，点首装界面上的「**自动更新（联网下载）**」（装好之后从右上角菜单进
「自动更新」）。App 会自己去
`https://v4.gh-proxy.org/https://github.com/ardeyouxipianyi/workbuddy2api-hub/archive/refs/heads/main.zip`
下载仓库 `main` 的最新快照（首选 gh-proxy 镜像，失败再退第二个镜像、最后直连
github.com），下载完走与手动更新完全相同的解压/切指针/重新加载流程。覆盖层会实时显示
「正在下载…42%（1.1 MB）」这类进度，失败时给出每个地址各自的失败原因。

> 想换成自己的仓库或分支：改 `GatewayBridge.kt` 里的 `REPO` / `BRANCH` 两个常量即可，
> `PROJECT_ZIP_URLS` 会自动跟着变。两个入口与后续解压流程都不变。

**二、手动更新**

1. 在**网关项目**仓库点 **Code → Download ZIP**，把压缩包发到手机（微信/网盘/数据线均可）；
2. 打开 App → 点「手动更新（选择压缩包）」（或菜单里的「手动更新」）→ 在系统文件
   选择器里选中这个 ZIP；
3. App 自动解压到 `filesDir/data/projects/<时间戳>/`，找到可识别的项目根
   （看 `wb_proxy.py`），读出版本号与默认端口后切换指针；
4. 随后启动网关，面板在 `http://127.0.0.1:8788/`。

已经装过项目时再点同一个入口，效果就是**原地换一份代码**：解到新目录、成功才切指针，
旧项目在新项目跑起来之前一直可用。这条路只看 ZIP 里有没有项目根，**不看来源、不要
凭证**——换代码这件事本身没有身份概念。

**三、菜单里的两项怎么分工**

- **手动更新**：装你手上的包。离线可用，可装任意版本（包括回退到旧版）。
- **自动更新**：拿仓库 `main` 的最新代码。一键升级，不用先去 GitHub 下载。

还没装过项目时点「自动更新」也算一次安装（只是文案换成「安装/更新」口径），
不会被一句「还没有项目」挡回来。

**四、远程接入与端口顺延**

「端口设置」里有两个开关：连接外部网关（默认关）与端口顺延（默认关）。
打开远程接入后：

- 不启动前台服务与本地 CPython 网关，通知与日志明确说明网关在别处；
- 探活打 `<地址>/health`，只有 200 算可达；
- WebView 加载 `<地址>/`；
- 「复制 API 地址」给出 `<地址>/v1`，而不是本机回环；关于页显示当前接入方式与地址。

关掉远程开关就回到本机运行路径，已导入的项目与账号数据都不变。

更新期间覆盖层会显示实时进度（面板还开着，所以进度必须浮在上面）；更新完**不重启进程**，
Python 侧把旧项目的模块摘掉后直接重新 `start()`，面板重新载一次即可看到新版本。
更新失败时旧项目仍然可用，覆盖层直接收掉只弹原因，不挡面板。

几个刻意的取舍：

- **数据目录与项目目录分开**：`filesDir/data/{accounts,usage,catalog,logs}` 由 App 持有，
  项目解压在 `data/projects/` 下。所以手动更新一份新 ZIP 覆盖升级，已登录的账号不丢。
- **每次更新解到新目录，成功后才切指针**：解压中断时旧项目仍可用，不会两头落空。
- **更新后不重启进程，原地换代码**：`install()` 成功时会把上一份项目 `import` 进来的模块
  从 `sys.modules` 摘掉、把旧目录从 `sys.path` 去掉，下一次 `start()` 真正 `import`
  的就是新版本。为什么不能重启：Android 10 起进程自己死掉后就再也起不来——系统会拦
  「后台启动 Activity」（真机日志 `Abort background activity starts from 10237`），
  普通闹钟、精确闹钟、`setAlarmClock` 全试过，用户只会看到 App 在更新后凭空消失。
- **ZIP 结构不挑**：GitHub 的 `仓库名-分支/` 外层会自动识别；也接受直接把仓库根目录
  压成的包。Python 网关项目不认的包会明确报错；Go 源码包会被单独指出并说明原因，
  不会留下半个项目。
- **下载先验 ZIP 魔数**：镜像返回 200 不代表拿到的是包（限流页、警告页都是 200），
  所以看前两字节是不是 `PK`，不是就换下一个地址。
- **不校验签名/来源**：这里导入的是你自己下载的仓库代码，App 只做路径安全校验（拒绝
  `../` 与绝对路径，跳过 `__MACOSX`）。

**APK 里到底有什么（这条边界怎么守）**

包内只有三样东西：Chaquopy 的 CPython 3.13 运行时、`assets/chaquopy/app.imy`
（里面唯一的 Python 模块是薄运行时 `wb_hub_env.pyc`）、原生外壳（Kotlin +
布局/字符串/图标）。**没有** `wb_*.py`、`dashboard.html`、`assets/core/`，没有
任何 pip 包（`requirements-*.imy` 是空 ZIP），也没有 Go 产物——曾短暂内置的
`libgateway.so` 已随 Go 形态回退移除。换句话说：把网关源码全部删掉，这个 APK
仍然能装、能启动，只是会停在「尚未导入网关项目」。网关实现完全来自导入的 ZIP。

守这条边界的四道闸：

1. `tests/_test_android_container_purity.py` 逐层钉住：源码树里不许出现网关
   文件、`src/main/python` 只许一个模块且不许 `.pyc`/`__pycache__` 残留、薄运行时不
   出现账号池/HTTP 处理器/模型白名单这类骨架、Kotlin 不读 `assets` 也不用 `file://`、
   `start()` 必须先有导入的项目；本机能找到 APK 时直接翻包（`app.imy` 只含
   `wb_hub_env.pyc`、依赖清单为空、没有 `assets/core`、没有 `libgateway.so`）。
2. `mobile-android` 工作流的 `Report artifacts` 逐个包检查：出现 `assets/core/`、
   `.py`/`.pyc`、`dashboard.html`、pip 依赖或 `libgateway.so`，直接判红。
3. `.gitignore` 保留 `assets/core/` 与 `jniLibs/` 规则当绊线——真有人拷回来
   至少不会入库。
4. 打包路径只有 `src/main/python` 一处：构建脚本不改写 `sourceSets` / `assets.srcDirs`，
   构建脚本只在算版本号时读一次 `mobile/VERSION`（只读不拷；前两段进版本名，
   `versionCode` 由它算，第三段换成构建日期）。

自动化测试：`python tests/_test_android_auto_import.py`（外壳接线 + 解压契约，离线秒级）；
加 `WB_LIVE_IMPORT=1` 会真的从镜像下一次并解压，验证地址当前可用、包结构没变。

真机端到端（点按真实界面，不 mock）：

```bash
python mobile/android/tools/e2e_auto_import.py        # 需要 adb + 已装本包
python mobile/android/tools/e2e_auto_import.py --keep # 结束后不停 App，方便接着看
```

它做这些事：确认首装界面两个按钮都在、且「自动更新」在「手动更新」下方；点自动更新并等
覆盖层消失；从 `/proc/net/tcp` 的 uid 确认「监听该端口的确实是本 App」（避免把别人
的面板测成自己的）；检查状态栏显示运行中、`/health` 返回 200、面板有内容；再展开
右上角菜单，确认「手动更新」与「自动更新」是两个入口、「项目信息」已并入
「关于与项目信息」（且关于里不出现凭证字样），最后点一次「自动更新」把下载流程
重跑一遍，更新后网关仍然在监听、`/health` 仍返回 200。

**端口占用是这台手机上的常见情况**：如果 Termux 里也跑着一份 `start-wb-proxy.sh`，
它就占着 8788。此时本 App 的网关会 bind 失败——状态栏直接显示
「启动失败：端口 8788 已被占用…到菜单『端口设置』换一个端口」，不会假装运行中、
更不会把 Termux 那份面板当成自己的。端到端脚本遇到这种情况会自动走 App 的端口
设置换到 18788 再继续。

> iOS 目前仍是「打包核心」形态；两端后续可以统一成容器模式，但 iOS 侧要改的是
> PyBridge 的 `PYTHONPATH` 与资源布局，改动面比 Android 大，暂未一并切换。

### iOS

```bash
# 需要 macOS + Xcode；CI 用 macos-15 + Xcode 26（iOS 26 的持续后台任务要用新 SDK）
cd mobile/ios
./scripts/sync-core.sh                       # 拉取外部 Python 核心 + 合并标准库/二进制扩展 + 装 certifi
brew install xcodegen && xcodegen generate   # 生成 OneGateway.xcodeproj
# Vendor/Python.xcframework 需先下载（CI 会自动做）：
#   https://github.com/beeware/Python-Apple-support/releases/download/3.13-b18/Python-3.13-iOS-support.b18.tar.gz
open OneGateway.xcodeproj                  # 选自己的 Team 后可真机运行
```

> 构建时还有一步 `install-python.sh`（`postBuildScripts` 里，自动执行），把
> `lib-dynload` 的 `.so` 转成 `Frameworks/*.framework` 并签名。这一步不能省，
> 也不能只靠 `sync-core.sh`——裸 `.so` 在 iOS 上加载不了。

- 仓库**不含** `Python.xcframework`（约 200MB）与生成的 `.xcodeproj`，都由 CI/本地脚本重建；
- 仓库也**不含**签名材料：CI 产出的是**未签名 IPA**，用 AltStore / Sideloadly 或
  本地 Xcode 配 Team 签名后才能装；
- 真机首次运行到「设置 → 通用 → VPN 与设备管理」信任开发者证书。

接入 `Python.framework` 踩过的三个坑（`project.yml` 里都有注释，别改回去）：

1. **不要**给 `Python.xcframework` 手动加 `FRAMEWORK_SEARCH_PATHS`。Xcode 会把切片里的
   `Python.framework` 复制到 `$(BUILT_PRODUCTS_DIR)` 并自动加进搜索路径；再指一份
   `Vendor/` 下的副本，clang 会同时看到两个 `Headers/module.modulemap`，报
   `redefinition of module 'Python'`。
2. 桥接头的头文件搜索路径要指 `$(BUILT_PRODUCTS_DIR)/Python.framework/Headers`。
3. **标准库要合并两份，二进制扩展要转 framework**。只拷 `lib/python3.13` 会漏掉
   68 个 `.so`（症状：启动即报 `hashlib has no attribute 'sha1'`）；拷了 `.so` 但不
   转 framework，iOS 又拒绝 `dlopen`（症状相同）。两件事分别由 `sync-core.sh` 和
   `install-python.sh` 负责，缺一不可。
   `module.modulemap` 位于 `Headers/` 下，不把该目录放进搜索路径会报
   `module 'Python' ... is not defined in any loaded module map file`。

## CI

| 工作流 | 触发 | 产物 |
|---|---|---|
| `.github/workflows/tests.yml` | push / PR | 7 个容器套件，Python 3.9 / 3.12 × Ubuntu / Windows |
| `.github/workflows/mobile-android.yml` | 改动 `mobile/**` | `one-gateway-android`（arm64 / x64 各一份 debug + release） |
| `.github/workflows/mobile-ios.yml` | 改动 `mobile/**` | `one-gateway-ios`（未签名 IPA） |

两条移动端流水线先做语法门禁（只编译容器桥接层 + 跑容器契约套件）。Android
断言 **APK 里没有网关源码**（`*.py` / `dashboard.html` / `assets/core/`）、没有
`libgateway.so` 且只有一套 ABI 原生库；iOS 断言 `app/wb_proxy.py`、
`app/dashboard.html`、`python/lib/python3.*/os.py` 与 `Frameworks/Python.framework`
齐备。前者守「容器不带网关源码 / Go 产物」，后者守「外部核心确实注入了」。
容器版本由 `mobile/VERSION` 统一提供。

## 已知限制

1. **iOS 后台**：iOS 26 起可以在「后台保持」里打开**持续后台处理**
   （`BGContinuedProcessingTask`），切到后台后系统继续给网关后台时间，并在锁屏/灵动岛
   显示一张进度卡片。时长由系统按电量与后台任务数量决定，到期后会停监听、回前台自动恢复。
   iOS 26 以下没有这个能力，仍是 20 秒宽限后停止监听。需要 24×7 常驻请用 Android。
2. **账号登录**：导入的网关项目自行负责账号登录，容器不提供凭证导入入口；
   登录在面板里完成扫码授权。
3. **未签名 IPA**：需要自行签名；且仓库没有内置 App 图标（sideload 无影响，上架需补）。
4. **端口**：默认 8788，可在「端口设置」里改；改端口或局域网开关会重启网关。
5. **首次启动较慢**：Android 要下载 / 解压外部网关项目并初始化网关（几秒到几十秒），
   期间面板显示「正在启动」。
6. **Android 菜单里的「退出」**：会连网关和前台服务一起停掉再关 App（菜单不再单列
   「启动/停止」——只想停网关用通知栏的「停止」按钮，重新打开 App 再点覆盖层的
   「启动网关」即可）。
7. **端口被占**：默认 8788 被别的服务占用时网关会报「端口被占用」并停在那里；
   可手动换端口，或在两端开关里打开「端口被占用时自动顺延」（+1 递增探测，
   最多 10 个）。需要连电脑 / 服务器上的实例时，用「远程接入」。
8. **旋转屏幕会重建界面（Android）**：清单只放行 `keyboardHidden|uiMode`，旋转时 Activity 重建。
   放行 `orientation|screenSize` 时 Activity 不重建，WebView 会留着上一次的页面缩放，
   横屏右侧约 12% 的按钮（关闭网关、修改密码、添加 API Key）被挤出屏幕；实测
   `reload()` 清不掉这个缩放，只有新建的 WebView 才会按新视口重算。网关跑在前台服务里，
   界面重建不影响监听；面板从 URL 的 `?tab=` 恢复页签，登录态在 cookie 里不受影响。
