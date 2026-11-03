# 移动端（iOS / Android）

> **实现方式：Vibe Coding**
>
> `mobile/` 下的全部代码——Kotlin 外壳、Swift 外壳、`wb_mobile_runtime.py`、
> Gradle / XcodeGen 工程描述、构建脚本与两条 CI 工作流——都是 **Vibe Coding**
> 的产物：通过对话描述需求、由 AI 直接生成并反复迭代，几乎没有逐行人工审查。
>
> 网关内核 `wb_*.py` 与 `dashboard.html` **不在此列**，它们是仓库原有的实现，
> 移动端只是原样打包，未做改写。
>
> 因此移动端外壳属于**实验性代码**：两端都能构建、能在设备上跑通主链路，
> 但异常分支、边界处理与长期可维护性没有经过人工 review。用于生产或分发前
> 请自行评估，遇到问题也请以「AI 生成代码」的预期来定位。

把电脑版的 WorkBuddy 多账号网关搬到手机上。**没有另写一套业务逻辑**：仓库根目录
的 `wb_*.py` 与 `dashboard.html` 就是移动端的内核，两端只做了「托管 + 平台适配」。

```
mobile/
├── shared/
│   └── wb_mobile_runtime.py     # 共享启动器：沙盒目录、核心同步、证书、start/stop/status
├── android/                     # Kotlin 外壳 + Chaquopy（内嵌 CPython 3.13）
│   ├── app/src/main/
│   │   ├── java/com/workbuddy2api/hub/
│   │   │   ├── HubApp.kt            # Application：初始化 CPython
│   │   │   ├── GatewayBridge.kt     # Kotlin ⇄ Python 唯一通道 + assets 解压
│   │   │   ├── GatewayService.kt    # 前台服务 + 常驻通知
│   │   │   ├── MainActivity.kt      # WebView 加载面板 + 原生菜单
│   │   │   └── Prefs.kt             # 端口 / 局域网 / 自启
│   │   ├── python/wb_hub_android.py # 桥接层（转调 shared/wb_mobile_runtime.py）
│   │   └── assets/core/             # 构建时从仓库根目录复制（不入库）
│   └── app/build.gradle.kts         # 版本号从 wb_proxy.py 解析，核心同步任务
└── ios/                         # Swift 外壳 + 内嵌 Python.framework
    ├── project.yml                  # XcodeGen 工程描述
    ├── scripts/sync-core.sh         # 复制核心 + 铺标准库 + 安装 certifi
    ├── WorkBuddyHub/Sources/
    │   ├── PyBridge.swift           # CPython C API 桥（GIL、PYTHONHOME/PYTHONPATH）
    │   ├── RootViewController.swift  # WKWebView 面板 + 前后台让位
    │   ├── BackgroundKeepAlive.swift # iOS 26 持续后台任务（BGContinuedProcessingTask）
    │   ├── LiveActivityController.swift  # 锁屏/灵动岛实时活动（ActivityKit）
    │   ├── BackgroundSettingsViewController.swift  # 「后台保持」三个开关
    │   ├── CorePaths.swift          # 沙盒路径 / 外壳开关 / 局域网地址
    │   ├── WorkBuddyHub-Bridging-Header.h  # #include <Python/Python.h>
    │   └── AppDelegate.swift
    ├── Shared/GatewayActivityAttributes.swift  # 主 App 与 Widget 共享的活动定义
    ├── WorkBuddyHubWidget/          # Widget Extension：Live Activity 的锁屏/灵动岛视图
    ├── WorkBuddyHub/PythonBridge/wb_hub_ios.py
    └── WorkBuddyHub/Resources/       # 构建时生成（不入库）
        ├── app/                      # 核心代码 + 桥接层 + certifi → PYTHONPATH
        └── python/lib/python3.13/    # CPython 标准库 → PYTHONHOME
```

## 为什么是「内嵌 Python」而不是重写

网关是一万三千行的纯标准库 Python（账号池、双区域调度、计费、用量、面板接口、
上游转发、指纹与风控细节）。用 Kotlin/Swift 重写一遍意味着两套实现长期漂移，
而这份代码的价值恰恰在那些细节里。内嵌解释器后，**PC 版升级即移动端升级**：

- Android：Chaquopy 16.1 + CPython 3.13（`libpython3.13.so` + OpenSSL + sqlite3）；
- iOS：BeeWare [python-app-support](https://github.com/beeware/Python-Apple-support)
  的 `Python.xcframework`，以 `Python.framework` 形式随包嵌入。

包内布局照 CPython 官方 [Using Python on iOS](https://docs.python.org/3/using/ios.html)
的 `install_python` 约定，**这个形状不能改**：

| 包内路径 | 内容 | 用途 |
|---|---|---|
| `app/` | `wb_*.py`、`dashboard.html`、`wb_hub_ios.py`、`certifi/` | `PYTHONPATH` |
| `python/lib/python3.13/`（含 `lib-dynload`） | CPython 标准库 | `PYTHONHOME` |
| `Frameworks/Python.framework` | 解释器本体（XCFramework 切片） | 链接 + 嵌入 |

标准库必须落在 `python/lib/pythonX.Y` 这一层：CPython 的 getpath 把 `PYTHONHOME`
当 prefix、只认 `prefix/lib/pythonX.Y`。平铺在 `Resources/python` 下解释器能起来，
但一 import 就报 `No module named 'encodings'`。

面板同理：`dashboard.html` 是「同源静态页 + JSON 接口」，WebView 直接打开
`http://127.0.0.1:8788/` 就等于移植了全部界面与交互，面板以后加功能 App 不用改。

## 两端各自适配了什么

| 主题 | Android | iOS |
|---|---|---|
| 进程存活 | 前台服务 + 常驻通知，息屏/切后台链路不断 | iOS 26+：持续后台任务（`BGContinuedProcessingTask`）让网关在后台继续监听；更早系统上 20 秒宽限后关闭监听，回前台自动恢复 |
| 锁屏/灵动岛 | — | 可选的实时活动（Live Activity）：锁屏卡片 + 灵动岛，两个独立开关 |
| 控制入口 | 通知栏「停止」、App 内菜单（重启网关 / 端口设置 / 导入凭证 / 复制 API 地址 / 日志 / 关于 / 退出） | App 内菜单（含「后台保持」设置页） |
| 面板 | 系统 WebView + 下拉刷新 | `WKWebView` |
| 面板 JS 弹窗 | `WebChromeClient` 把 `alert/confirm/prompt` 接到原生对话框；`target=_blank` 在同一 WebView 打开 | `WKUIDelegate` 提供同样的三个回调 |
| 存储 | `filesDir/data`（accounts/usage/catalog/logs） | `Library/Application Support/data` |
| 明文回环 | `network_security_config` 放行 127.0.0.1 | ATS 例外 + `NSAllowsLocalNetworking` |
| HTTPS 根证书 | Android 系统证书目录（`SSL_CERT_DIR`） | 随包 `certifi`（iOS 无系统 CA 路径） |
| 局域网访问 | 绑 0.0.0.0，通知里给 IP；自动生成 API Key | 同左，另需「本地网络」权限 |
| 版本号 | Gradle 从 `wb_proxy.py` 的 `server_version` 解析 | CI 从同一处解析并注入 `MARKETING_VERSION` |

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
- Live Activity 的视图在 `WorkBuddyHubWidget` 这个 app-extension 里，主包必须把
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
# 需要 JDK 17、Android SDK（platform 35）；无需本机装 Gradle，CI 用 8.9
cd mobile/android
gradle :app:assembleDebug        # 或 :app:assembleRelease
# 产物：app/build/outputs/apk/<flavor>/<buildType>/app-<flavor>-<buildType>.apk
```

**每个 ABI 单独出包**（flavor 维度 `abi`）：

| flavor | ABI | 用途 | 大致体积 |
|---|---|---|---|
| `arm64` | `arm64-v8a` | 真机（现在几乎所有 Android 手机） | ~23 MB |
| `x64` | `x86_64` | 模拟器 / x86_64 设备 | ~23 MB |

- 拆包用的是 product flavor + 每个 flavor 只声明一个 `abiFilters`，**不是** `splits { abi }`
  或 App Bundle：Chaquopy 把解释器和标准库按 ABI 拆成三套资产
  （`libpython3.13.so`、`assets/chaquopy/stdlib-<abi>.imy`、
  `assets/chaquopy/bootstrap-native/<abi>/`），splits 只过滤 `lib/`，assets 里另一套
  ABI 的副本照样留在包内。官方 FAQ 也写明 splits 与 App Bundle 对 Chaquopy 帮助不大。
- 因此 `ndk.abiFilters` **只能写在 flavor 里**：它与 `defaultConfig` 是取并集，
  留在 `defaultConfig` 会让两个 flavor 都拿到两个 ABI，拆包白做。
- 合并后的包从 34.7 MB 降到约 23 MB（省掉另一套 native 库 + 标准库）。
- `versionName` 带 ABI 后缀（`1.10.7-arm64` / `1.10.7-x64`），装上后能在「关于」里
  确认手里的是哪个包；`versionCode` 两个 flavor 相同，同一台设备本来也只能装一个。
- CI 会逐个包校验「只有一套 ABI 原生库 + 一份 ABI 专属标准库」，拆包没生效直接失败。

- 首次构建会从仓库根目录把 `wb_*.py` + `dashboard.html` 同步进
  `app/src/main/assets/core/`（Gradle 的 `syncGatewayCore` 任务，构建产物不入库）；
- 没有提供 keystore 时 release 包用 debug 签名，保证可直接 sideload；要出正式包就设
  `WB_ANDROID_KEYSTORE` / `WB_ANDROID_KEYSTORE_PASSWORD` / `WB_ANDROID_KEY_ALIAS` /
  `WB_ANDROID_KEY_PASSWORD`；
- **CI 的 debug 签名是固定的**：仓库 secret `WB_ANDROID_DEBUG_KEYSTORE` 存了一份
  keystore 的 base64，构建前还原并由 `WB_ANDROID_DEBUG_KEYSTORE` 指给 Gradle，
  所以多次构建的证书一致、可以覆盖安装。注意 secret 里必须是 base64 文本——
  直接存二进制会让工作流在 startup 阶段失败（Secret 值必须是合法 UTF-8）；
  另外别指望 `~/.android/debug.keystore`：runner 的 `ANDROID_SDK_HOME` 与 `$HOME`
  不一致时 AGP 会忽略它、自己生成随机密钥。本地不设该变量时仍用各自的 debug keystore；
- 装机后首次启动会解压核心到 `filesDir/core-bundle`，之后按版本号判断是否重解压。

### iOS

```bash
# 需要 macOS + Xcode；CI 用 macos-14 runner
cd mobile/ios
./scripts/sync-core.sh                       # 复制核心 + 铺标准库 + 安装 certifi
brew install xcodegen && xcodegen generate   # 生成 WorkBuddyHub.xcodeproj
# Vendor/Python.xcframework 需先下载（CI 会自动做）：
#   https://github.com/beeware/Python-Apple-support/releases/download/3.13-b18/Python-3.13-iOS-support.b18.tar.gz
open WorkBuddyHub.xcodeproj                  # 选自己的 Team 后可真机运行
```

- 仓库**不含** `Python.xcframework`（约 200MB）与生成的 `.xcodeproj`，都由 CI/本地脚本重建；
- 仓库也**不含**签名材料：CI 产出的是**未签名 IPA**，用 AltStore / Sideloadly 或
  本地 Xcode 配 Team 签名后才能装；
- 真机首次运行到「设置 → 通用 → VPN 与设备管理」信任开发者证书。

接入 `Python.framework` 踩过的两个坑（`project.yml` 里都有注释，别改回去）：

1. **不要**给 `Python.xcframework` 手动加 `FRAMEWORK_SEARCH_PATHS`。Xcode 会把切片里的
   `Python.framework` 复制到 `$(BUILT_PRODUCTS_DIR)` 并自动加进搜索路径；再指一份
   `Vendor/` 下的副本，clang 会同时看到两个 `Headers/module.modulemap`，报
   `redefinition of module 'Python'`。
2. 桥接头的头文件搜索路径要指 `$(BUILT_PRODUCTS_DIR)/Python.framework/Headers`。
   `module.modulemap` 位于 `Headers/` 下，不把该目录放进搜索路径会报
   `module 'Python' ... is not defined in any loaded module map file`。

## CI

| 工作流 | 触发 | 产物 |
|---|---|---|
| `.github/workflows/mobile-android.yml` | 改动 `wb_*.py` / `dashboard.html` / `mobile/**` | `workbuddy-hub-android`（arm64 / x64 各一份 debug + release） |
| `.github/workflows/mobile-ios.yml` | 同上 | `workbuddy-hub-ios`（未签名 IPA） |

两条流水线都会先做语法门禁（`compileall`）并断言**核心确实进了包**
（APK 里查 `assets/core/wb_proxy.py`，IPA 里查 `app/wb_proxy.py`、
`python/lib/python3.*/os.py` 与 `Frameworks/Python.framework`），
避免打出一个「壳在、内核不在」的包。打 `v*` tag 时产物会附加到 release 草稿。

## 已知限制

1. **iOS 后台**：iOS 26 起可以在「后台保持」里打开**持续后台处理**
   （`BGContinuedProcessingTask`），切到后台后系统继续给网关后台时间，并在锁屏/灵动岛
   显示一张进度卡片。时长由系统按电量与后台任务数量决定，到期后会停监听、回前台自动恢复。
   iOS 26 以下没有这个能力，仍是 20 秒宽限后停止监听。需要 24×7 常驻请用 Android、
   电脑版或 Docker。
2. **账号导入**：电脑端 `.info` 凭据自 2026-09-24 起是加密信封，解密密钥要从未被
   扫描的桌面客户端进程内存里回收，手机上做不到。移动端请走面板的「+ 添加账号
   (OAuth)」完成登录；「导入账号凭证」只对未加密的旧格式文件有效。
3. **未签名 IPA**：需要自行签名；且仓库没有内置 App 图标（sideload 无影响，上架需补）。
4. **端口**：默认 8788，可在「端口设置」里改；改端口或局域网开关会重启网关。
5. **首次启动较慢**：要解压核心、初始化账号池与模型目录（几秒），期间面板显示
   「正在启动」。
6. **Android 菜单里的「退出」**：会连网关和前台服务一起停掉再关 App（菜单不再单列
   「启动/停止」——只想停网关用通知栏的「停止」按钮，重新打开 App 再点覆盖层的
   「启动网关」即可）。
7. **旋转屏幕会重建界面（Android）**：清单里只放行 `keyboardHidden`，旋转时 Activity 重建。
   放行 `orientation|screenSize` 时 Activity 不重建，WebView 会留着上一次的页面缩放，
   横屏右侧约 12% 的按钮（关闭网关、修改密码、添加 API Key）被挤出屏幕；实测
   `reload()` 清不掉这个缩放，只有新建的 WebView 才会按新视口重算。网关跑在前台服务里，
   界面重建不影响监听；面板从 URL 的 `?tab=` 恢复页签，登录态在 cookie 里不受影响。
