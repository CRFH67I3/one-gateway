package com.onegateway.host

import android.content.Context
import android.content.SharedPreferences

/**
 * 外壳配置。与导入网关项目的 settings.json 分开存：
 * 这里只有「移动端怎么跑」的开关（端口、是否监听局域网、是否随 App 启动），
 * 账号、API Key、面板密码等业务配置仍然由网关自己写在 accounts/ 里，
 * 这样换外壳、换设备都不影响已导入的账号。
 */
object Prefs {
    private const val FILE = "hub-shell"
    private const val KEY_PORT = "port"
    private const val KEY_LAN = "lan"
    private const val KEY_AUTOSTART = "autostart"
    private const val KEY_PROJECT_PATH = "project-path"
    private const val KEY_PROJECT_VERSION = "project-version"
    private const val KEY_PROJECT_IMPORTED_AT = "project-imported-at"
    private const val KEY_REMOTE = "remote"
    private const val KEY_REMOTE_BASE = "remote-base"
    private const val KEY_PORT_HOP = "port-hop"

    const val DEFAULT_PORT = 8788

    private fun prefs(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun port(context: Context): Int =
        prefs(context).getInt(KEY_PORT, DEFAULT_PORT).coerceIn(1024, 65535)

    fun setPort(context: Context, port: Int) {
        prefs(context).edit().putInt(KEY_PORT, port.coerceIn(1024, 65535)).apply()
    }

    fun lan(context: Context): Boolean = prefs(context).getBoolean(KEY_LAN, false)

    fun setLan(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_LAN, enabled).apply()
    }

    fun autoStart(context: Context): Boolean = prefs(context).getBoolean(KEY_AUTOSTART, true)

    fun setAutoStart(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_AUTOSTART, enabled).apply()
    }

    /** 当前生效的网关项目根目录（指向 projects/<时间戳>）。 */
    fun projectPath(context: Context): String? = prefs(context).getString(KEY_PROJECT_PATH, null)

    fun setProjectPath(context: Context, path: String) {
        prefs(context).edit().putString(KEY_PROJECT_PATH, path).apply()
    }

    fun projectVersion(context: Context): String =
        prefs(context).getString(KEY_PROJECT_VERSION, "") ?: ""

    fun setProjectVersion(context: Context, version: String) {
        prefs(context).edit().putString(KEY_PROJECT_VERSION, version).apply()
    }

    fun projectImportedAt(context: Context): Long = prefs(context).getLong(KEY_PROJECT_IMPORTED_AT, 0L)

    fun setProjectImportedAt(context: Context, at: Long) {
        prefs(context).edit().putLong(KEY_PROJECT_IMPORTED_AT, at).apply()
    }

    // ---------------------------------------------------------------- 远程接入
    /** 远程接入：容器只做面板与探活，不启动本地 Python 网关。 */
    fun remote(context: Context): Boolean = prefs(context).getBoolean(KEY_REMOTE, false)

    fun setRemote(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_REMOTE, enabled).apply()
    }

    /** 远程网关的 origin（可带 http/https，可省略端口）。 */
    fun remoteBase(context: Context): String =
        prefs(context).getString(KEY_REMOTE_BASE, "") ?: ""

    fun setRemoteBase(context: Context, base: String) {
        prefs(context).edit().putString(KEY_REMOTE_BASE, base).apply()
    }

    /**
     * 端口被占用时自动顺延（+1 递增探测，默认关）。
     *
     * 开启后启动网关前先探一次 TCP：预设端口被别的服务占着（Termux 里另跑
     * 一份网关是典型）就依次试 port+1、port+2…，最多 [PORT_HOP_LIMIT] 个，
     * 把实际生效的端口写回 [KEY_PORT]，状态栏、面板与「复制 API 地址」自然
     * 跟着走。关闭时维持原行为：直接报「端口被占用」让人手动处理。
     */
    const val PORT_HOP_LIMIT = 10

    fun portHop(context: Context): Boolean = prefs(context).getBoolean(KEY_PORT_HOP, false)

    fun setPortHop(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_PORT_HOP, enabled).apply()
    }
}
