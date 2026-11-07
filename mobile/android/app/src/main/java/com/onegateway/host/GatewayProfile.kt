package com.onegateway.host

import android.content.Context
import org.json.JSONObject

/**
 * 两套外部网关的接入契约，容器里唯一一份「形态 → 地址/字段/脚本」的映射表。
 *
 * 用户能导入两种完全不同的外部项目，两者都能在手机里本机运行：
 *
 *  * `python`（ardeyouxipianyi/workbuddy2api-hub）：wb_proxy.py + dashboard.html，
 *    纯标准库，导入 ZIP 后由 App 内 CPython 托管，可热更新。面板在 `/`，探活
 *    `GET /health` 返回 accounts / accounts_ready，换肤靠 `wb:system-theme` 事件。
 *  * `go`（misakano7545/workbuddy2api-panel）：Go 单二进制，面板在 `/panel/`，
 *    探活 `GET /healthz` 返回 total / healthy，主题存 localStorage
 *    ['wb2api.theme'] 并由 applyTheme() 刷 data-theme。Android 10+ 禁止从可写的
 *    应用私有目录执行未签名 ELF、iOS 只允许运行签名进包的代码，所以它在**构建期**
 *    被交叉编译成随 APK/IPA 签名的二进制，由 [GoGateway] / GoBridge 在进程内或
 *    子进程里启动；导入的 ZIP 只用于确认形态与版本，不能替换二进制。需要连到别处
 *    已运行的实例时，仍可在设置里打开「远程接入」。
 *
 * 两个导入入口与交互保持不变：识别形态由 Python 薄运行时完成，外壳在这里只读
 * 结果。远程模式时必须先有这里的 kind 才能拼出正确地址——所以 kind 的优先级是
 * 「远程模式用户选的形态 > 已导入项目的形态」。
 */
object GatewayProfile {
    const val PYTHON = "python"
    const val GO = "go"

    /** Go 形态的外部仓库（自动更新源可切到它）。 */
    const val GO_REPO = "misakano7545/workbuddy2api-panel"
    const val GO_BRANCH = "main"
    const val GO_MARKER = "go.mod"
    const val GO_DEFAULT_PORT = 7863

    fun panelPath(kind: String): String = if (kind == GO) "/panel/" else "/"

    fun healthPath(kind: String): String = if (kind == GO) "/healthz" else "/health"

    fun defaultPort(kind: String): Int =
        if (kind == GO) GO_DEFAULT_PORT else Prefs.DEFAULT_PORT

    fun label(kind: String): String = if (kind == GO) "Go 网关" else "Python 网关"

    fun isRemote(context: Context): Boolean = Prefs.remote(context)

    /**
     * Go 型且没有开远程接入 = 本机运行随包内置的 Go 网关。
     * 面板/探活路径与远程 Go 完全一致，差别只在谁托管进程。
     * 用 localKind 而不是 projectKind：Go 二进制随包签名，直接切换即可，
     * 不必先导入一份 Go 源码 ZIP。
     */
    fun isLocalGo(context: Context): Boolean =
        !isRemote(context) && Prefs.localKind(context) == GO

    fun kind(context: Context): String =
        if (isRemote(context)) Prefs.remoteKind(context) else Prefs.localKind(context)

    /**
     * 远程地址规范化：接受 `127.0.0.1:7863`、`192.168.1.5`、完整 URL，
     * 缺端口时补该形态的默认端口，缺省地址回落到本机回环。
     * 允许 https 是为了能接公网反代；局域网 http 由清单的明文例外放行。
     */
    fun normaliseBase(raw: String, kind: String): String {
        var value = raw.trim().trimEnd('/')
        val scheme = if (value.startsWith("https://")) "https" else "http"
        value = value.removePrefix("https://").removePrefix("http://").trimEnd('/')
        if (value.isEmpty()) return "$scheme://127.0.0.1:${defaultPort(kind)}"
        // IPv6 的 [::1] 里也有冒号，只看右括号之后有没有端口。
        if (!value.substringAfterLast(']').contains(':')) {
            value = "$value:${defaultPort(kind)}"
        }
        return "$scheme://$value"
    }

    /** 不含路径的 origin；本地模式固定回环，远程模式取用户填的地址。 */
    fun baseUrl(context: Context, port: Int): String =
        if (isRemote(context)) {
            normaliseBase(Prefs.remoteBase(context), kind(context))
        } else {
            "http://127.0.0.1:$port"
        }

    fun panelBase(context: Context, port: Int): String =
        baseUrl(context, port) + panelPath(kind(context))

    fun healthUrl(context: Context, port: Int): String =
        baseUrl(context, port) + healthPath(kind(context))

    fun apiBase(context: Context, port: Int): String = baseUrl(context, port) + "/v1"

    /**
     * Go 的 /healthz 在账号池不可服务时返回 503，但响应体仍然带 total/healthy。
     * 这是「网关活着但没号可用」，不是「连不上」，所以 503 也算可达。
     */
    fun acceptsHealthCode(kind: String, code: Int): Boolean =
        if (kind == GO) code == 200 || code == 503 else code == 200

    /** 把两套健康响应归一化成 (账号总数, 可用账号数)。 */
    fun normaliseHealth(kind: String, json: JSONObject): Pair<Int, Int> =
        if (kind == GO) {
            Pair(json.optInt("total", 0), json.optInt("healthy", 0))
        } else {
            Pair(json.optInt("accounts", 0), json.optInt("accounts_ready", 0))
        }

    /**
     * Go 面板的主题同步脚本。
     *
     * 它的 theme.js 在首帧读 localStorage['wb2api.theme']，app.js 把偏好放在顶层
     * `let theme`，由 applyTheme() 刷 `data-theme`。WebView 的
     * prefers-color-scheme 跟的是 App 主题（外壳是 Light），「跟随系统」在手机上
     * 永远报浅色，所以由外壳把系统值直接写进去。Python 面板不认这套，仍走
     * `wb:system-theme` 事件（MainActivity 单独派发），两边互不干扰。
     */
    fun themeScript(dark: Boolean): String {
        val value = if (dark) "dark" else "light"
        return "(function(v){" +
            "try{localStorage.setItem('wb2api.theme',v);}catch(e){}" +
            "try{theme=v;applyTheme();}catch(e)" +
            "{document.documentElement.setAttribute('data-theme',v);}" +
            "try{window.dispatchEvent(new StorageEvent('storage'," +
            "{key:'wb2api.theme',newValue:v}));}catch(e){}" +
            "})('$value');"
    }
}
