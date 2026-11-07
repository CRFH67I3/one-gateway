package com.onegateway.host

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Kotlin ⇄ Python 的唯一通道。
 *
 * 这个外壳不携带网关源码：APK 里只有 Python 解释器 + 一个薄运行时
 * （`wb_hub_env`），真正的网关由用户导入仓库 ZIP 后解压到沙盒里跑。
 * 所以这里除了转发调用，还负责「项目装在哪、当前用的是哪一份」。
 *
 * 导入层认两种形态，见 [GatewayProfile]：Python 型本地托管；Go 型只识别、不执行，
 * 外壳自动切到远程接入。两个导入入口（手动选 ZIP / 自动下载）共用同一套 install。
 */
object GatewayBridge {
    private const val MODULE = "wb_hub_env"
    private const val MARKER = "wb_proxy.py"
    private const val CACHE_ZIP = "imported-project.zip"

    /**
     * 自动导入默认拉取的「外部网关项目」仓库（Python 型）。
     *
     * 本仓库只有容器外壳，不含任何网关源码；`main` 分支的 ZIP 才是要装进沙盒
     * 的那份网关。换网关源只改这一行；不想联网就用首装界面的「手动更新」
     * 选一份本地 ZIP，两份入口走的是同一个 install 流程。
     */
    private const val REPO = "ardeyouxipianyi/workbuddy2api-hub"
    private const val BRANCH = "main"

    /**
     * 自动导入按顺序尝试的下载地址。
     *
     * 手机直连 github.com 下载 archive 在国内经常超时，所以首选 gh-proxy 镜像；
     * 镜像也会挂（限流、Cloudflare 502），于是再退到第二个镜像、最后直连。
     * 谁先下完算谁的——镜像与直连拿的是同一份 `main` 快照。
     */
    val PROJECT_ZIP_URLS: List<String> = listOf(
        "https://v4.gh-proxy.org/https://github.com/$REPO/archive/refs/heads/$BRANCH.zip",
        "https://gh-proxy.com/https://github.com/$REPO/archive/refs/heads/$BRANCH.zip",
        "https://github.com/$REPO/archive/refs/heads/$BRANCH.zip",
    )

    /**
     * 「自动更新」按设置里选的网关源给出下载地址。
     *
     * Python 型是默认的 [PROJECT_ZIP_URLS]；Go 型换成另一个外部仓库的 main 快照，
     * 依然是「镜像优先、直连兜底」的同样三个地址。两份 ZIP 都走同一个
     * installProject()，能不能在手机里本地跑由形态决定，下载这一步没有区别。
     */
    fun sourceZipUrls(kind: String): List<String> {
        if (kind != GatewayProfile.GO) return PROJECT_ZIP_URLS
        val repo = GatewayProfile.GO_REPO
        val branch = GatewayProfile.GO_BRANCH
        return listOf(
            "https://v4.gh-proxy.org/https://github.com/$repo/archive/refs/heads/$branch.zip",
            "https://gh-proxy.com/https://github.com/$repo/archive/refs/heads/$branch.zip",
            "https://github.com/$repo/archive/refs/heads/$branch.zip",
        )
    }

    /** 一次自动导入的下载结果：成功给文件，失败给「每个地址各自为什么失败」。 */
    data class Download(val file: File?, val error: String)

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
        val marker = if (Prefs.projectKind(context) == GatewayProfile.GO) {
            GatewayProfile.GO_MARKER
        } else {
            MARKER
        }
        return if (File(dir, marker).isFile) dir else null
    }

    /**
     * 导入（或升级）网关项目：把选中的 ZIP 解压到 projects/<时间戳>/。
     *
     * 解压成功才切指针——中途失败时旧项目仍然可用，不会把用户卡在「两个都用不了」。
     * 返回体里带 version/root/files/kind，界面直接展示。
     *
     * Go 型只登记、不本地执行：装好就自动切到远程接入并填好默认端口，用户只需
     * 确认运行网关的那台设备地址。两个导入入口走的是同一个 install，交互不变。
     */
    fun installProject(context: Context, zip: File): JSONObject {
        val result = invoke("install", zip.absolutePath, projectsDir(context).absolutePath)
        if (result.optBoolean("ok")) {
            val kind = result.optString("kind").ifBlank { GatewayProfile.PYTHON }
            Prefs.setProjectKind(context, kind)
            Prefs.setProjectPath(context, result.optString("root"))
            Prefs.setProjectVersion(context, result.optString("version"))
            Prefs.setProjectImportedAt(context, System.currentTimeMillis())
            // 后续「自动更新」跟着当前导入的形态走，别把 Go 用户又拉回 Python 源。
            Prefs.setSource(context, kind)
            if (kind == GatewayProfile.GO) {
                Prefs.setRemote(context, true)
                Prefs.setRemoteKind(context, GatewayProfile.GO)
                if (Prefs.remoteBase(context).isBlank()) {
                    val port = result.optInt("default_port", GatewayProfile.GO_DEFAULT_PORT)
                    Prefs.setRemoteBase(context, "127.0.0.1:$port")
                }
            }
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

    /**
     * 自动导入：直接从仓库下载最新 ZIP 到缓存，后续走和手动导入完全相同的
     * [installProject]。下载与解压都不在主线程。
     *
     * 每个地址单独计时：连接 15s、读 30s。手机在弱网下「连上了但一个字节都
     * 不来」很常见，没有超时就会一直卡在「正在下载 0%」，用户只能杀进程。
     *
     * @param onAttempt 开始尝试第 index 个地址（1 基）时回调，用于把「换镜像了」显示出来。
     * @param onProgress (百分比，-1 表示拿不到总长度；已下载字节；总字节或 -1)。
     */
    fun downloadProjectZip(
        context: Context,
        urls: List<String> = PROJECT_ZIP_URLS,
        onAttempt: (Int, Int) -> Unit = { _, _ -> },
        onProgress: (Int, Long, Long) -> Unit = { _, _, _ -> },
    ): Download {
        val target = File(context.cacheDir, CACHE_ZIP)
        val failures = ArrayList<String>()
        urls.forEachIndexed { index, url ->
            onAttempt(index + 1, urls.size)
            try {
                fetch(url, target, onProgress)
                if (isZip(target)) {
                    return Download(target, "")
                }
                failures.add("#${index + 1} 下载到的不是 ZIP")
            } catch (t: Throwable) {
                val reason = t.message?.takeIf { it.isNotBlank() } ?: t.javaClass.simpleName
                failures.add("#${index + 1} $reason")
            }
            // 失败留下的半截文件必须清掉：下一次尝试的进度会从 0 开始重算。
            target.delete()
        }
        return Download(null, failures.joinToString("；").ifBlank { "无法连接下载地址" })
    }

    private fun fetch(url: String, target: File, onProgress: (Int, Long, Long) -> Unit) {
        val connection = openFollowing(url)
        try {
            val total = connection.contentLengthLong
            var done = 0L
            var lastPct = Int.MIN_VALUE
            connection.inputStream.use { input ->
                target.outputStream().use { output ->
                    val buffer = ByteArray(1 shl 16)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        done += read
                        val pct = if (total > 0) ((done * 100) / total).toInt() else -1
                        // 每 1% 才回调一次：2.6MB 的包逐块回调会把主线程刷爆。
                        if (pct != lastPct) {
                            lastPct = pct
                            onProgress(pct, done, total)
                        }
                    }
                }
            }
            if (done <= 0L) throw IOException("下载内容为空")
            onProgress(100, done, if (total > 0) total else done)
        } finally {
            connection.disconnect()
        }
    }

    /**
     * 手动跟随重定向。
     *
     * 不用 `instanceFollowRedirects`：它只在同协议间跟随，而 gh-proxy 会把
     * 请求最终 302 到预签名的对象存储地址，个别节点还会 http/https 混跳。
     */
    private fun openFollowing(rawUrl: String): HttpURLConnection {
        var current = rawUrl
        var hops = 0
        while (true) {
            val connection = URL(current).openConnection() as HttpURLConnection
            connection.connectTimeout = 15_000
            connection.readTimeout = 30_000
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("User-Agent", "OneGateway-Android")
            connection.setRequestProperty("Accept", "application/zip,application/octet-stream,*/*")
            val code = connection.responseCode
            if (code in 300..399) {
                val location = connection.getHeaderField("Location")
                connection.disconnect()
                if (location.isNullOrBlank()) throw IOException("重定向缺少 Location（HTTP $code）")
                if (hops++ >= 5) throw IOException("重定向次数过多（HTTP $code）")
                current = if (location.startsWith("http")) {
                    location
                } else {
                    URL(URL(current), location).toString()
                }
                continue
            }
            if (code != HttpURLConnection.HTTP_OK) {
                connection.disconnect()
                throw IOException("HTTP $code")
            }
            return connection
        }
    }

    /** 只看文件头两字节：镜像的「错误页」也可能返回 200。 */
    private fun isZip(file: File): Boolean {
        if (file.length() < 1024) return false
        return try {
            file.inputStream().use { input ->
                val head = ByteArray(2)
                input.read(head) == 2 && head[0] == 'P'.code.toByte() && head[1] == 'K'.code.toByte()
            }
        } catch (t: Throwable) {
            false
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
