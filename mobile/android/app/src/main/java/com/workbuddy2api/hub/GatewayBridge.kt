package com.workbuddy2api.hub

import android.content.Context
import android.content.res.AssetManager
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File

/**
 * Kotlin ⇄ Python 的唯一通道。
 *
 * 约定：Python 侧统一返回 JSON 字符串，Kotlin 侧只做解析与展示，不参与任何网关
 * 业务判断——这样 PC 版升级时外壳不用跟着改。
 */
object GatewayBridge {
    private const val MODULE = "wb_hub_android"
    private const val ASSET_CORE = "core"
    private const val BUNDLE_DIR = "core-bundle"

    fun ensurePython(context: Context) {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context.applicationContext))
        }
    }

    /** 可写数据目录：accounts/ usage/ catalog/ logs/ core/ 全在这里。 */
    fun dataDir(context: Context): File =
        File(context.filesDir, "data").also { it.mkdirs() }

    /**
     * 把 APK 里随包携带的网关核心解到 filesDir。
     *
     * APK 的 assets 是只读的，而网关要按 `__file__` 旁边找 dashboard.html、
     * 写 catalog/，所以先落一份可读写副本；版本变化时才重新解压。
     */
    fun extractCore(context: Context): File {
        val target = File(context.filesDir, BUNDLE_DIR)
        val stamp = "v" + BuildConfig.VERSION_CODE
        if (Prefs.bundleStamp(context) == stamp && File(target, "wb_proxy.py").isFile) {
            return target
        }
        target.deleteRecursively()
        target.mkdirs()
        extractAssetDir(context.assets, ASSET_CORE, "", target)
        Prefs.setBundleStamp(context, stamp)
        return target
    }

    private fun extractAssetDir(assets: AssetManager, assetPath: String, relDir: String, root: File) {
        val names = assets.list(assetPath) ?: return
        for (name in names) {
            val childAsset = "$assetPath/$name"
            val rel = if (relDir.isEmpty()) name else "$relDir/$name"
            val children = assets.list(childAsset) ?: emptyArray()
            if (children.isEmpty()) {
                val out = File(root, rel)
                out.parentFile?.mkdirs()
                assets.open(childAsset).use { input ->
                    out.outputStream().use { output -> input.copyTo(output, 1 shl 16) }
                }
            } else {
                extractAssetDir(assets, childAsset, rel, root)
            }
        }
    }

    fun start(context: Context, port: Int, lan: Boolean): JSONObject =
        invoke("start", extractCore(context).absolutePath, dataDir(context).absolutePath, port, lan)

    fun stop(context: Context): JSONObject =
        invoke("stop", extractCore(context).absolutePath, dataDir(context).absolutePath)

    fun status(context: Context): JSONObject =
        invoke("status", extractCore(context).absolutePath, dataDir(context).absolutePath)

    fun importCredential(context: Context, path: String): JSONObject =
        invoke(
            "import_credential",
            extractCore(context).absolutePath,
            dataDir(context).absolutePath,
            path,
        )

    fun logTail(context: Context, lines: Int): String = try {
        Python.getInstance().getModule(MODULE)
            .callAttr("log_tail", extractCore(context).absolutePath, dataDir(context).absolutePath, lines)
            .toString()
    } catch (t: Throwable) {
        "日志不可用: ${t.message}"
    }

    private fun invoke(name: String, vararg args: Any): JSONObject = try {
        val raw = Python.getInstance().getModule(MODULE).callAttr(name, *args).toString()
        JSONObject(if (raw.isBlank()) "{}" else raw)
    } catch (t: Throwable) {
        JSONObject()
            .put("ok", false)
            .put("running", false)
            .put("error", t.message ?: t.javaClass.simpleName)
    }
}
