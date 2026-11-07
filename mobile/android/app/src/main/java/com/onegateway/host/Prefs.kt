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
    private const val KEY_PROJECT_KIND = "project-kind"
    private const val KEY_REMOTE = "remote"
    private const val KEY_REMOTE_BASE = "remote-base"
    private const val KEY_REMOTE_KIND = "remote-kind"
    private const val KEY_SOURCE = "update-source"

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

    // ---------------------------------------------------------------- 双形态接入
    /** 导入项目是哪一种网关；决定 projectRoot() 用哪个入口文件复查。 */
    fun projectKind(context: Context): String =
        prefs(context).getString(KEY_PROJECT_KIND, GatewayProfile.PYTHON) ?: GatewayProfile.PYTHON

    fun setProjectKind(context: Context, kind: String) {
        prefs(context).edit().putString(KEY_PROJECT_KIND, kind).apply()
    }

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

    /** 远程网关的形态；默认是 Go，因为 Python 型通常直接本地托管。 */
    fun remoteKind(context: Context): String =
        prefs(context).getString(KEY_REMOTE_KIND, GatewayProfile.GO) ?: GatewayProfile.GO

    fun setRemoteKind(context: Context, kind: String) {
        prefs(context).edit().putString(KEY_REMOTE_KIND, kind).apply()
    }

    /** 「自动更新」下载哪一份仓库 ZIP；默认仍是 Python 网关。 */
    fun source(context: Context): String =
        prefs(context).getString(KEY_SOURCE, GatewayProfile.PYTHON) ?: GatewayProfile.PYTHON

    fun setSource(context: Context, kind: String) {
        prefs(context).edit().putString(KEY_SOURCE, kind).apply()
    }
}
