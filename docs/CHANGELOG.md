# 变更记录 · OneGateway（容器）

本文件只记录**容器仓库本身**的变更。网关实现、面板与协议行为的变更记录在外部
网关项目里，不属于本仓库。

## 未发布 / Unreleased

### 双形态兼容：同一容器同时接 Python 型与 Go 型网关

把容器的接入契约从单一 Python 型扩到两种形态，**两个导入入口与交互保持不变**。

- **导入层**：`wb_hub_env` 同时认 `wb_proxy.py` + `dashboard.html`（Python 型）与
  `go.mod` + `cmd/server`（Go 型，如 `misakano7545/workbuddy2api-panel`）；返回
  `kind` / `default_port`（后者从 Go 项目的 `config.example.json` 读 `listen`），
  未知 ZIP 的报错同时列出两种入口。Python 型的版本读取、解压、purge、start 全链路
  一步未改。
- **契约层**：新增 `GatewayProfile.kt` / `GatewayProfile.swift`，统一面板路径
  （`/` vs `/panel/`）、探活路径（`/health` vs `/healthz`）、健康字段
  （`accounts`/`accounts_ready` vs `total`/`healthy`）、默认端口、远程地址规范化与
  主题脚本。Go 的 `/healthz` 回 503 时仍读响应体，算「可达但没号」。
- **远程接入**：两端新增开关（**默认关**）。打开后不启动本地进程 / 前台服务 /
  CPython，面板、探活、复制 API 地址、通知、关于页全部指向所填 origin；地址支持
  回环、局域网 IP 与 https。Go 型 ZIP 导入成功会自动切到该模式并填好
  `127.0.0.1:7863`（本机 Termux 常见形态）。
- **双源自动更新**：Android 设置里可切到 Go 仓库的 `main` 快照，下载仍走
  「镜像优先、直连兜底」，之后复用同一个 `installProject()`；默认仍是 Python 源。
- **深浅色**：Python 面板保留 `?sysdark=` + `wb:system-theme`；Go 面板在页面就绪与
  系统切深浅时注入 `wb2api.theme` + `applyTheme()`，两端都不重载页面、不掉登录。
- **平台限制**：Go 单二进制无法在 App 内执行（Android 10+ 禁止从私有目录 exec
  未签名 ELF、iOS 只允许签名代码），因此只做「识别 + 远程接入」；容器仍不含任何
  网关源码或二进制。`sync-core.sh` 遇到 Go ZIP 给出可操作的诊断而非空泛报错。
- **测试**：新增 `tests/_test_dual_gateway_compat.py`（21 例：两种 ZIP 真调
  `install()`、三端契约静态比对、iOS 抽取脚本执行、可选真实仓库联网验证），
  套件数 6 → 7；两条移动端流水线在编译前也跑它。

### 更名：Proxy Gateway Runtime → OneGateway

按项目目标重新定名：仓库只是装载外部网关的宿主，不含网关实现，名字的主语不该是
「网关运行时」。

- 英文项目名与 App 展示名统一为 **OneGateway**；仓库改名 `CRFH67I3/one-gateway`，
  旧地址由 GitHub 自动重定向。
- Android `applicationId` / `namespace` 与 iOS bundle id 统一为 `com.onegateway.host`；
  Kotlin 包路径改为 `com/onegateway/host/`，iOS target、目录与产物改为 `OneGateway` /
  `OneGatewayWidget`。**已装旧包的设备需卸载重装**，旧包收不到新版本。
- CI 产物改名 `one-gateway-android` / `one-gateway-ios`；外部网关源
  `ardeyouxipianyi/workbuddy2api-hub`、两个导入入口与全部交互保持不变。
- 历史名称（WorkBuddy2API-Hub、Proxy Gateway Runtime）保留在下方记录里，不再回改。

### 仓库收敛为纯容器：移除全部网关内容

仓库不再包含网关实现，只保留 Android / iOS 外壳与装载逻辑。

- **删除（364 个受控文件）**：根目录 36 个 `wb_*.py`、`dashboard.html`、273 个网关
  测试套件、网关文档与部署运维工具（`ops/` `wrt/` `release/` `scripts/` `tools/`）、
  桌面启动脚本、账号 / 目录 / 定价 / 用量数据目录，以及网关侧的发布与回滚检查工作流。
  仓库从 433 个文件收敛到 69 个。
- **导入入口不变**：首装的「自动导入（联网下载）」与「手动导入（选择压缩包）」
  两个按钮及菜单里对应的「自动更新 / 手动更新」保持原样。自动导入的默认源改为
  仍提供完整网关核心的外部仓库 `ardeyouxipianyi/workbuddy2api-hub` 的 `main` 分支；
  改 `GatewayBridge.kt` 的 `REPO` / `BRANCH` 即可换源。
- **iOS**：`sync-core.sh` 不再复制仓库根目录的核心，改为构建时从外部项目 ZIP 拉取
  `wb_*.py` + `dashboard.html` 注入包内；可用 `GATEWAY_CORE_ZIP_URL` 换源、
  `GATEWAY_CORE_ZIP` 离线构建。
- **版本独立**：新增 `mobile/VERSION` 作为容器版本唯一来源；Android `versionName` /
  `versionCode` 与 iOS `MARKETING_VERSION` / `CFBundleVersion` 都从它计算，不再读取
  已删除的 `wb_proxy.py` 里的 `server_version`。容器版本与导入的网关版本互不绑定。
- **CI**：三条工作流收敛为 `tests` / `mobile-android` / `mobile-ios`；语法门禁只编译
  容器桥接层，删除「tag == 网关源码版本」断言。
- **测试**：保留 6 个容器套件（自动导入契约、构建工具链、容器纯度、深浅色换肤、
  minSdk、UI token）；面板相关的文件断言改为显式 token 契约，因为面板已属外部项目。
- **贡献规范**：冒烟门禁从「网关 → 上游真实响应」改为容器级验证（构建、导入、
  产物纯度、干净检出）；端到端链路验证归外部网关项目。

### 更名：WorkBuddy2API-Hub → Proxy Gateway Runtime（中转网关运行时）

- 中文「中转网关运行时」/ 英文「Proxy Gateway Runtime」；仓库改名
  `CRFH67I3/proxy-gateway-runtime`，旧地址由 GitHub 自动重定向。
- Android `applicationId` 与 iOS bundle id 统一为 `com.proxygateway.runtime`。
  **已装旧包的设备需卸载重装**，旧包收不到新版本。
- Docker 部署相关文件与文档已移除（项目只在移动平台使用）。
