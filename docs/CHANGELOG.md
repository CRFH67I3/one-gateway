# 变更记录 · OneGateway（容器）

本文件只记录**容器仓库本身**的变更。网关实现、面板与协议行为的变更记录在外部
网关项目里，不属于本仓库。

## 未发布 / Unreleased

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
