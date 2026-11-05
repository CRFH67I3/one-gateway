package com.workbuddy2api.hub

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File

/**
 * Kotlin ⇄ Python 的唯一通道。
 *
 * 这个外壳不携带网关源码：APK 里只有 Python 解释器 + 一个薄运行时
 * （`wb_hub_env`），真正的网关由用户导入仓库 ZIP 后解压到沙盒里跑。
 * 所以这里除了转发调用，还负责「项目装在哪、当前用的是哪一份」。
 */
object GatewayBridge {
    private const val MODULE = "wb_hub_env"
    private const val MARKER = "wb_proxy.py"
    private const val CACHE_ZIP = "imported-project.zip"

    fun ensurePython(context: Context) {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context.applicationContext))
        }
    }

    /** 可写数据目录：accounts/ usage/ catalog/ logs/ 与 projects/ 都在这里。 */
    fun dataDir(context: Context): File = File(context.filesDir, "data").also { it.mkdirs() }

    fun projectsDir(context: Context): File = File(dataDir(context), "projects").also { it.mkdirs() }

    /** 当前生效的项目根；指针存在但目录被删（清过数据）时返回 null。 */
    fun projectRoot(context: Context): File? {
        val path = Prefs.projectPath(context) ?: return null
        val dir = File(path)
        return if (File(dir, MARKER).isFile) dir else null
    }

    /**
     * 导入（或升级）网关项目：把选中的 ZIP 解压到 projects/<时间戳>/。
     *
     * 解压成功才切指针——中途失败时旧项目仍然可用，不会把用户卡在「两个都用不了」。
     * 返回体里带 version/root/files，界面直接展示。
     */
    fun installProject(context: Context, zip: File): JSONObject {
        val result = invoke("install", zip.absolutePath, projectsDir(context).absolutePath)
        if (result.optBoolean("ok")) {
            Prefs.setProjectPath(context, result.optString("root"))
            Prefs.setProjectVersion(context, result.optString("version"))
            Prefs.setProjectImportedAt(context, System.currentTimeMillis())
        }
        return result
    }

    fun describeProject(context: Context): JSONObject {
        val root = projectRoot(context)
            ?: return JSONObject().put("ok", false).put("error", "还没有导入网关项目")
        return invoke("describe", root.absolutePath)
    }

    fun start(context: Context, port: Int, lan: Boolean): JSONObject {
        val root = projectRoot(context) ?: return notInstalled()
        return invoke("start", root.absolutePath, dataDir(context).absolutePath, port, lan)
    }

    fun stop(context: Context): JSONObject {
        val root = projectRoot(context) ?: return notInstalled()
        return invoke("stop", root.absolutePath, dataDir(context).absolutePath)
    }

    fun status(context: Context): JSONObject {
        val root = projectRoot(context) ?: return notInstalled()
        return invoke("status", root.absolutePath, dataDir(context).absolutePath)
    }

    fun importCredential(context: Context, path: String): JSONObject {
        val root = projectRoot(context) ?: return notInstalled()
        return invoke("import_credential", root.absolutePath, dataDir(context).absolutePath, path)
    }

    fun logTail(context: Context, lines: Int): String {
        val root = projectRoot(context) ?: return "还没有导入网关项目"
        return try {
            Python.getInstance().getModule(MODULE)
                .callAttr("log_tail", root.absolutePath, dataDir(context).absolutePath, lines)
                .toString()
        } catch (t: Throwable) {
            "日志不可用: ${t.message}"
        }
    }

    /** 导入前先把 SAF 的输入流落到缓存：Python 侧只认文件路径。 */
    fun stageZip(context: Context, open: () -> java.io.InputStream?): File? = try {
        val target = File(context.cacheDir, CACHE_ZIP)
        open()?.use { input -> target.outputStream().use { output -> input.copyTo(output, 1 shl 16) } }
        if (target.length() > 0) target else null
    } catch (t: Throwable) {
        null
    }

    private fun notInstalled(): JSONObject = JSONObject()
        .put("ok", false)
        .put("running", false)
        .put("error", "还没有导入网关项目")

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
