package com.workbuddy2api.hub

import android.content.Context
import android.content.SharedPreferences

/**
 * 外壳配置。与 PC 版的 settings.json 分开存：
 * 这里只有「移动端怎么跑」的开关（端口、是否监听局域网、是否随 App 启动），
 * 账号、API Key、面板密码等业务配置仍然由网关自己写在 accounts/ 里，
 * 这样换外壳、换设备都不影响已导入的账号。
 */
object Prefs {
    private const val FILE = "hub-shell"
    private const val KEY_PORT = "port"
    private const val KEY_LAN = "lan"
    private const val KEY_AUTOSTART = "autostart"
    private const val KEY_BUNDLE_STAMP = "bundle-stamp"

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

    fun bundleStamp(context: Context): String? = prefs(context).getString(KEY_BUNDLE_STAMP, null)

    fun setBundleStamp(context: Context, stamp: String) {
        prefs(context).edit().putString(KEY_BUNDLE_STAMP, stamp).apply()
    }
}
