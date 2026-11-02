# 移动端（iOS / Android）

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
    ├── scripts/sync-core.sh         # 复制核心 + 安装 certifi
    ├── WorkBuddyHub/Sources/
    │   ├── PyBridge.swift           # CPython C API 桥（GIL、PYTHONHOME）
    │   ├── RootViewController.swift  # WKWebView 面板 + 前后台让位
    │   ├── CorePaths.swift          # 沙盒路径 / 外壳开关 / 局域网地址
    │   └── AppDelegate.swift
    ├── WorkBuddyHub/PythonBridge/wb_hub_ios.py
    └── WorkBuddyHub/Resources/python/  # 构建时生成（不入库）
```

## 为什么是「内嵌 Python」而不是重写

网关是一万三千行的纯标准库 Python（账号池、双区域调度、计费、用量、面板接口、
上游转发、指纹与风控细节）。用 Kotlin/Swift 重写一遍意味着两套实现长期漂移，
而这份代码的价值恰恰在那些细节里。内嵌解释器后，**PC 版升级即移动端升级**：

- Android：Chaquopy 16.1 + CPython 3.13（`libpython3.13.so` + OpenSSL + sqlite3）；
- iOS：BeeWare [python-app-support](https://github.com/beeware/Python-Apple-support)
  的 `Python.xcframework`，以 `Python.framework` 形式随包嵌入，`PYTHONHOME` 指过去。

面板同理：`dashboard.html` 是「同源静态页 + JSON 接口」，WebView 直接打开
`http://127.0.0.1:8788/` 就等于移植了全部界面与交互，面板以后加功能 App 不用改。

## 两端各自适配了什么

| 主题 | Android | iOS |
|---|---|---|
| 进程存活 | 前台服务 + 常驻通知，息屏/切后台链路不断 | App 进后台 20 秒宽限后关闭监听，回前台自动恢复 |
| 控制入口 | 通知栏「停止」、App 内菜单 | App 内菜单（没有常驻通知） |
| 面板 | 系统 WebView + 下拉刷新 | `WKWebView` |
| 存储 | `filesDir/data`（accounts/usage/catalog/logs） | `Library/Application Support/data` |
| 明文回环 | `network_security_config` 放行 127.0.0.1 | ATS 例外 + `NSAllowsLocalNetworking` |
| HTTPS 根证书 | Android 系统证书目录（`SSL_CERT_DIR`） | 随包 `certifi`（iOS 无系统 CA 路径） |
| 局域网访问 | 绑 0.0.0.0，通知里给 IP；自动生成 API Key | 同左，另需「本地网络」权限 |
| 版本号 | Gradle 从 `wb_proxy.py` 的 `server_version` 解析 | CI 从同一处解析并注入 `MARKETING_VERSION` |

## 构建

### Android

```bash
# 需要 JDK 17、Android SDK（platform 35）；无需本机装 Gradle，CI 用 8.9
cd mobile/android
gradle :app:assembleDebug        # 或 :app:assembleRelease
# 产物：app/build/outputs/apk/{debug,release}/*.apk
```

- 首次构建会从仓库根目录把 `wb_*.py` + `dashboard.html` 同步进
  `app/src/main/assets/core/`（Gradle 的 `syncGatewayCore` 任务，构建产物不入库）；
- 没有提供 keystore 时 release 包用 debug 签名，保证可直接 sideload；要出正式包就设
  `WB_ANDROID_KEYSTORE` / `WB_ANDROID_KEYSTORE_PASSWORD` / `WB_ANDROID_KEY_ALIAS` /
  `WB_ANDROID_KEY_PASSWORD`；
- 装机后首次启动会解压核心到 `filesDir/core-bundle`，之后按版本号判断是否重解压。

### iOS

```bash
# 需要 macOS + Xcode；CI 用 macos-14 runner
cd mobile/ios
./scripts/sync-core.sh                       # 复制核心 + 安装 certifi
brew install xcodegen && xcodegen generate   # 生成 WorkBuddyHub.xcodeproj
# Vendor/Python.xcframework 需先下载（CI 会自动做）：
#   https://github.com/beeware/Python-Apple-support/releases/download/3.13-b18/Python-3.13-iOS-support.b18.tar.gz
open WorkBuddyHub.xcodeproj                  # 选自己的 Team 后可真机运行
```

- 仓库**不含** `Python.xcframework`（约 200MB）与生成的 `.xcodeproj`，都由 CI/本地脚本重建；
- 仓库也**不含**签名材料：CI 产出的是**未签名 IPA**，用 AltStore / Sideloadly 或
  本地 Xcode 配 Team 签名后才能装；
- 真机首次运行到「设置 → 通用 → VPN 与设备管理」信任开发者证书。

## CI

| 工作流 | 触发 | 产物 |
|---|---|---|
| `.github/workflows/mobile-android.yml` | 改动 `wb_*.py` / `dashboard.html` / `mobile/**` | `workbuddy-hub-android`（debug + release APK） |
| `.github/workflows/mobile-ios.yml` | 同上 | `workbuddy-hub-ios`（未签名 IPA） |

两条流水线都会先做语法门禁（`compileall`）并断言**核心确实进了包**
（APK 里查 `assets/core/wb_proxy.py`，IPA 里查 `python/wb_proxy.py` 与
`Frameworks/Python.framework`），避免打出一个「壳在、内核不在」的包。
打 `v*` tag 时产物会附加到 release 草稿。

## 已知限制

1. **iOS 后台**：App 被切后台/锁屏后网关于 20 秒后停止监听，回到前台自动恢复。
   这是 iOS 的平台约束（没有常驻后台服务），不是网关的行为差异。需要 24×7 常驻请用
   Android、电脑版或 Docker。
2. **账号导入**：电脑端 `.info` 凭据自 2026-09-24 起是加密信封，解密密钥要从未被
   扫描的桌面客户端进程内存里回收，手机上做不到。移动端请走面板的「+ 添加账号
   (OAuth)」完成登录；「导入账号凭证」只对未加密的旧格式文件有效。
3. **未签名 IPA**：需要自行签名；且仓库没有内置 App 图标（sideload 无影响，上架需补）。
4. **端口**：默认 8788，可在设置里改；改端口或局域网开关会重启网关。
5. **首次启动较慢**：要解压核心、初始化账号池与模型目录（几秒），期间面板显示
   「正在启动」。
