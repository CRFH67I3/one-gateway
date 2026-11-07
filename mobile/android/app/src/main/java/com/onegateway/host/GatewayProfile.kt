package com.onegateway.host

import android.content.Context
import org.json.JSONObject

/**
 * 容器里唯一一份「网关 → 地址/字段」的契约表。
 *
 * 本容器只运行 Python 网关（ardeyouxipianyi/workbuddy2api-hub）：wb_proxy.py +
 * dashboard.html，纯标准库，导入 ZIP 后由 App 内 CPython 托管，可热更新。面板在
 * `/`，探活 `GET /health` 返回 accounts / accounts_ready，换肤靠 `wb:system-theme`
 * 事件。
 *
 * 需要连到别处已运行的实例时，可在设置里打开「远程接入」——容器只做面板与探活，
 * 不启动本地进程。
 */
object GatewayProfile {
    const val PYTHON = "python"

    fun label(): String = "Python 网关"

    fun isRemote(context: Context): Boolean = Prefs.remote(context)

    /**
     * 远程地址规范化：接受 `127.0.0.1:8788`、`192.168.1.5`、完整 URL，
     * 缺端口时补默认端口，缺省地址回落到本机回环。
     * 允许 https 是为了能接公网反代；局域网 http 由清单的明文例外放行。
     */
    fun normaliseBase(raw: String): String {
        var value = raw.trim().trimEnd('/')
        val scheme = if (value.startsWith("https://")) "https" else "http"
        value = value.removePrefix("https://").removePrefix("http://").trimEnd('/')
        if (value.isEmpty()) return "$scheme://127.0.0.1:${Prefs.DEFAULT_PORT}"
        // IPv6 的 [::1] 里也有冒号，只看右括号之后有没有端口。
        if (!value.substringAfterLast(']').contains(':')) {
            value = "$value:${Prefs.DEFAULT_PORT}"
        }
        return "$scheme://$value"
    }

    /** 不含路径的 origin；本地模式固定回环，远程模式取用户填的地址。 */
    fun baseUrl(context: Context, port: Int): String =
        if (isRemote(context)) {
            normaliseBase(Prefs.remoteBase(context))
        } else {
            "http://127.0.0.1:$port"
        }

    fun panelBase(context: Context, port: Int): String = baseUrl(context, port) + "/"

    fun healthUrl(context: Context, port: Int): String = baseUrl(context, port) + "/health"

    fun apiBase(context: Context, port: Int): String = baseUrl(context, port) + "/v1"

    /** 把健康响应归一化成 (账号总数, 可用账号数)。 */
    fun normaliseHealth(json: JSONObject): Pair<Int, Int> =
        Pair(json.optInt("accounts", 0), json.optInt("accounts_ready", 0))
}
