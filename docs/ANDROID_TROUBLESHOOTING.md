# Android 真机排障速查（HyperOS / MIUI）

> 适用设备：21091116AC，HyperOS 1.0 / Android 13（API 33）
> 自家应用包名：`com.onegateway.host`　短信转发器：`cn.ppps.forwarder`
> 本文所有结论均在真机验证过，命令可直接复制执行。

## 0. 环境准备

```bash
export PATH="/c/Users/Admin/AppData/Local/Android/Sdk/platform-tools:$PATH"
adb devices          # 应看到设备号 + device
```

后续命令统一用 `P=com.onegateway.host` 这类变量，避免手打错包名。

---

## 1. 短信不转发（第三方短信 App）

**症状**：通道测试通过、规则正确，但真实短信到了 App 内毫无日志——像没收到。

### 30 秒判据

```bash
# ① 看广播是否投递给了它
adb shell dumpsys activity broadcasts | grep -B2 -A6 "SMS_RECEIVED"

# ② 看 MIUI 私有 AppOp
adb shell cmd appops get cn.ppps.forwarder 10018
```

- ① 里出现 `Skipped 0 #1: (manifest)` + `name=cn.ppps.forwarder.receiver.SmsReceiver`
  → **广播在系统分发阶段就被跳过**，App 侧无论怎么配都没用。
- ② 返回 `MIUIOP(10018): ignore` → 确诊。

### 根因

`MIUIOP(10018)` = `OP_READ_NOTIFICATION_SMS`（读取通知类短信）。第三方应用默认
`ignore`，系统因此跳过所有带 `miui.intent.SERVICE_NUMBER=true` 的短信（银行、10010、
验证码等全部属于这类）。

### 修复

```bash
adb shell cmd appops set cn.ppps.forwarder 10018 0
adb shell cmd appops get cn.ppps.forwarder 10018     # 必须变成 allow
```

> **坑**：必须写数字 `0`（MODE_ALLOWED）。写字符串 `allow` 不会生效，op 会停在
> `default`，看起来"设置成功"其实没设进去。

> **坑**：HyperOS 的权限页里**没有**这个开关（短信权限只剩 拒绝/始终允许/空白通行证），
> 别在设置里找，找不到是正常的。

### 何时会复发

清数据、重装、换机后失效。**这是设备级设置，不随 App 数据保留**。

### 验证闭环

用系统短信 App 发给自己（普通短信），再给 10010 发 "1" 收服务号回复。
`dumpsys activity broadcasts` 里应变成 `Deliver +xxms #1`，转发日志出现全文。

---

## 2. 后台被杀 / 进程被冻结

**症状**：App 明明没几个，内存也不紧张，但切后台一会儿就死。

### 第一步：先排除这个（最容易中招）

```bash
adb shell dumpsys activity | grep -E "CUR_MAX_CACHED_PROCESSES|CUR_MAX_EMPTY_PROCESSES"
```

- 正常：`32` / `16`
- **异常：`4` / `2`** → 开发者选项 → 「后台进程限制」被设成了「不得超过 4 个进程」，
  第 5 个缓存进程立刻被砍。跟内存、装了多少软件**完全无关**。
- 该项**只存在内存里**，重启手机即恢复默认，所以现象常表现为"用久了才开始杀"。

修复：设置 → 开发者选项 → 后台进程限制 → 标准限制。改完立即复查上条命令。

### 第二步：核对保活六件套

```bash
P=com.onegateway.host

adb shell cmd appops get $P 10008                       # 自启动，需 allow
adb shell dumpsys deviceidle whitelist | grep $P        # 电池优化白名单，需在列
adb shell am get-standby-bucket $P                      # 应为 5（exempted）
adb shell dumpsys activity services $P | grep -E "isForeground|foregroundNoti"
adb shell dumpsys activity processes | grep -A3 $P      # oom adj 200 或 negatives
```

省电策略在 `dumpsys power` 里查 `noRestrict` / `MILLET_NO_RESTRICT_APP`。

### 坑清单（都踩过，别再踩）

| 坑 | 事实 |
|---|---|
| `cmd appops set $P 10008 allow` | 只写成 `default`，**自启动开关必须进应用详情页手动点** |
| 最近任务锁卡片 | 这台 HyperOS 1.0 长按只有「分屏/小窗/应用详情」，**没有锁图标**；直接写 `settings put system locked_apps` 值能写进去但不生效，已还原原值。放弃这条路 |
| 「一键延长 XX 分钟」 | 会批量「禁止应用自启动／关闭耗电应用」，转发器会被一起干掉，**别点** |
| 锁屏后清理内存 / 锁屏断开数据 | 必须设为「从不」，否则半夜必死 |

结论：这版 ROM 靠 **自启动 + 省电策略 noRestrict + 前台服务** 就够，不需要锁卡片。

---

## 3. 前台服务通知不显示 → 进程被冻

**症状**：服务确实在跑，但通知栏没有常驻通知；随后进程进入 `D` 状态、HTTP 超时。

### 判据

```bash
P=com.onegateway.host
adb shell dumpsys activity services $P | grep -E "isForeground|foregroundNoti"   # isForeground=true
adb shell cmd notification list | grep $P                                        # 但这里查不到
adb shell dumpsys notification | grep -E "numBlocked|numPostedByApp|POST_NOTIFICATIONS"
```

`isForeground=true` 但 `cmd notification list` 里没有 → **通知被系统丢弃了**。

### 根因（这是最容易误判的一条）

Android 13+ 只在执行 `startForeground()` 的**那一瞬间**判断这张通知发不发得出去：

- 那一刻没有 `POST_NOTIFICATIONS` 权限（或 MIUI 侧「允许通知」关着）→ 通知被丢弃；
- **系统不会补投**，之后就算把权限打开，通知也永远不会自己出现；
- 结果：服务是前台服务，但没有通知 → MIUI 当普通后台应用冻结 → 进程 `D` 状态、
  `/health` 超时（HTTP 000，socket 停在 `CLOSE_WAIT`）。

权限带 `USER_SET` flag = 事后在设置里手动开的，正是这个场景。

### 修复

App 侧（已实现，`529b775`）：

- `MainActivity.requestNotificationPermissionIfNeeded()`：TIRAMISU+ 首次进入就申请；
- `GatewayService.ACTION_REFRESH`：只重挂通知，沿用 `lastTitle`/`lastText`，**不重启服务**；
- 触发点：`onRequestPermissionsResult` + `onResume`，且仅当 `GatewayService.alive` 为真。

第三方 App（如 Termux）没有这个机制，只能：

```bash
adb shell am force-stop com.termux     # am start 不会重发通知，必须 force-stop 后重启
```

### 稳定复现 / 验证手法

```bash
adb shell pm revoke com.onegateway.host android.permission.POST_NOTIFICATIONS
# 启动 App → 应无通知；再 pm grant → 回前台 → 通知出现，且服务未重启（端口仍 200）
adb shell pm grant com.onegateway.host android.permission.POST_NOTIFICATIONS
```

---

## 4. 装机验收（不本地构建）

**约定：APK 一律来自 GitHub Actions，不在本地跑 gradle。**

### 确认 CI 与提交对应

```bash
cd /c/Users/Admin/Desktop/Projects/workbuddy2api-hub-ios
git log --oneline -1 && git rev-list --left-right --count origin/main...HEAD   # 应为 0  0

# 判断「APK 是不是最新代码」要看这个，不是看 HEAD：
git log -1 --format='%h %s' -- mobile/android/

curl -s "https://api.github.com/repos/CRFH67I3/one-gateway/actions/runs?per_page=3" \
  | python -c "import sys,json;[print(r['name'],r['head_sha'][:7],r['conclusion'],r['created_at']) for r in json.load(sys.stdin)['workflow_runs']]"
```

> **别被 HEAD 骗了**：`mobile-android.yml` / `mobile-ios.yml` 的 `push.paths` 只含
> `mobile/**` 和 workflow 文件本身，所以**只改 docs 的提交不会重建 APK**。
> 这种时候 HEAD 比 APK 新是正常的；正确判据是 `git log -1 -- mobile/android/`
> 的 sha 与 CI artifact 对应的 sha 一致，再叠加下面的 md5 / 符号校验。

三条流水线（mobile-android / mobile-ios / tests）必须都在**同一个 sha**上 `success`。

### 下载 artifact 的坑

`artifact.zip` 的下载地址会 302 到 Azure Blob。**`curl -L` 会因为跨域仍携带
`Authorization` 头而 401**，`urllib` 自动重定向同样失败。必须手动拿 `Location`
再不带 token 请求。

### 校验设备上装的到底是不是这一版

```bash
P=$(adb shell pm path com.onegateway.host | tr -d '\r' | sed 's/package://')
adb pull "$P" /tmp/dev.apk && md5sum /tmp/dev.apk
md5sum /c/Users/Admin/Downloads/wb-apk/app-arm64-release-*.apk      # 应与上一行完全一致

# 再确认新代码符号真的在包里（比看版本号可靠）
unzip -p /tmp/dev.apk classes.dex | grep -ac "com.onegateway.host.action.REFRESH"
unzip -p /tmp/dev.apk resources.arsc | grep -ac "后台保活设置"
```

符号命中数 > 0 才算"装的是新代码"，避免版本号相同、包是旧的。

### 安装

```bash
adb install -r -d <apk>      # 降级/同版本覆盖都要 -d
```

---

## 5. UI 自动化注意

- MIUI 应用详情页会**二次重定向**：`com.android.settings/.applications.InstalledAppDetails`
  → `com.miui.securitycenter/com.miui.appmanager.ApplicationsDetailsActivity`，
  **启动后要等约 4 秒**再 `uiautomator dump`，太早会误判成"没跳转"。
- 复用 `.workbuddy/ui.py`（封装 `dump` / 节点解析 / `find`），别每次重写。
- 坐标点击前一定先 dump 校准，MIUI 页面布局每次可能不同。

---

## 一页速查表

| 现象 | 一条命令定位 | 结论/修复 |
|---|---|---|
| 短信不转发 | `cmd appops get cn.ppps.forwarder 10018` | `ignore` → `set ... 10018 0`（数字 0） |
| 后台老被杀 | `dumpsys activity \| grep CUR_MAX_CACHED` | `4` → 后台进程限制改回标准 |
| 有服务没通知 | `cmd notification list \| grep <pkg>` | 查不到 → 权限后授，需 App 侧重挂 / force-stop |
| 进程 `D` 状态超时 | 上一条 + `dumpsys activity services` | 无通知被 MIUI 冻结 |
| 装的包是不是最新 | `adb pull` 后 `md5sum` 对比 artifact | 不一致就重装 |
| 设置页没跳转 | 等 4 秒再 dump | MIUI 二次重定向 |
