package com.onegateway.host

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * 随包内置的 Go 网关进程管理。
 *
 * 为什么不是「下载 ZIP 后 exec」：Android 10+（targetSdk>=29）禁止从可写的
 * 应用私有目录 execve()，官方要求「只加载嵌入 APK 的二进制代码」。所以 Go 网关
 * 在**构建期**从外部仓库源码交叉编译成 PIE ELF，以 `libgateway.so` 之名放进
 * `jniLibs/<abi>/`，由安装器解压到只读的 `/data/app/.../lib/<abi>/`（标签
 * `apk_data_file`，SELinux 允许执行）。运行时从这里启动，代码随 APK 签名，
 * 完全不走「下载可执行代码」这条路。
 *
 * 代价也说清楚：Go 网关版本与 App 版本绑定，两个更新入口对 Go 型只负责确认形态
 * 与刷新配置，不能替换二进制（替换就得重新签名安装 APK）。Python 型仍可热更新。
 *
 * 进程生命周期挂在 [GatewayService] 上：前台服务的 ACTION_START / STOP /
 * RESTART 都会经过 [GatewayBridge]，由它按形态分流到这里或 CPython。
 */
object GoGateway {
    private const val LIB_NAME = "libgateway.so"
    private const val LOG_NAME = "gateway.log"

    @Volatile private var process: Process? = null
    @Volatile private var lastError: String = ""
    private val lock = Any()

    /** 安装器解压出来的只读二进制；没有它说明这个包不是带 Go 网关的构建。 */
    fun binary(context: Context): File =
        File(context.applicationInfo.nativeLibraryDir, LIB_NAME)

    fun available(context: Context): Boolean = binary(context).isFile

    private fun workDir(context: Context): File =
        File(context.filesDir, "go-gateway").apply { mkdirs() }

    private fun logFile(context: Context): File = File(workDir(context), LOG_NAME)

    /** 首次启动生成一份随机 API Key，之后固定，避免每次重启客户端都要改配置。 */
    private fun apiKey(context: Context): String {
        val f = File(workDir(context), "api_key.txt")
        if (f.isFile) {
            val saved = f.readText().trim()
            if (saved.isNotEmpty()) return saved
        }
        val fresh = UUID.randomUUID().toString().replace("-", "")
        f.writeText(fresh)
        return fresh
    }

    /**
     * 生成沙盒配置。
     *
     * 上游默认把账号/状态写在 `./auths`、`./data/state.json` 这类相对路径上，
     * 手机里必须改成绝对路径并预先建目录；自更新器与定时外连任务一律关掉——
     * 它会尝试下载替换自身二进制，在 App 内既不可能也不安全。
     */
    private fun writeConfig(context: Context, port: Int, lan: Boolean): File {
        val dir = workDir(context)
        File(dir, "auths").mkdirs()
        File(dir, "data").mkdirs()
        File(dir, "data/request-logs").mkdirs()

        val cfg = JSONObject()
            .put("listen", (if (lan) "0.0.0.0" else "127.0.0.1") + ":" + port)
            .put("api_key", apiKey(context))
            .put("auth_dir", File(dir, "auths").absolutePath)
            .put("state_file", File(dir, "data/state.json").absolutePath)
            // 自更新器在 App 内没有意义：二进制是随包签名的，不能被运行时替换。
            .put("update", JSONObject().put("auto_enabled", false))
            // 后台定时任务会持续外连、耗电且依赖服务器侧账号，手机上默认关。
            .put("schedule", JSONObject().put("balance_refresh_enabled", false))
            .put("alerting", JSONObject().put("enabled", false))
            .put("watchdog", JSONObject().put("enabled", false))
            .put("logging", JSONObject().put("request_archive_enabled", false))
            .put("admin", JSONObject().put("audit_enabled", false))
            .put("metrics", JSONObject().put("enabled", false))

        val f = File(dir, "config.json")
        f.writeText(cfg.toString(2))
        return f
    }

    fun start(context: Context, port: Int, lan: Boolean): JSONObject {
        val bin = binary(context)
        if (!bin.isFile) {
            lastError = "此安装包未内置 Go 网关（缺少 libgateway.so）"
            return state(false, lastError)
        }
        stop(context)
        val dir = workDir(context)
        val cfg = writeConfig(context, port, lan)
        return try {
            val p = ProcessBuilder(bin.absolutePath, "-config", cfg.absolutePath)
                .directory(dir)
                .redirectErrorStream(true)
                .start()
            synchronized(lock) { process = p }

            // Go 的 log 走 stderr/stdout；合并后落到文件，供菜单「查看启动日志」读。
            Thread({
                try {
                    p.inputStream.bufferedReader().use { reader ->
                        while (true) {
                            val line = reader.readLine() ?: break
                            synchronized(lock) { logFile(context).appendText(line + "\n") }
                        }
                    }
                } catch (_: Throwable) {
                    // 进程被杀时管道断开是正常收尾，不需要报错。
                }
            }, "go-gateway-log").apply { isDaemon = true }.start()

            var ready = false
            for (i in 0 until 40) {
                if (probe(port) != null) {
                    ready = true
                    break
                }
                if (!alive(p)) break
                Thread.sleep(250)
            }
            if (ready) {
                lastError = ""
                state(true, "")
            } else {
                lastError = if (alive(p)) {
                    "Go 网关已启动，但端口 $port 还没就绪"
                } else {
                    "Go 网关进程启动后退出，请查看启动日志"
                }
                state(false, lastError)
            }
        } catch (t: Throwable) {
            lastError = t.message ?: t.javaClass.simpleName
            state(false, lastError)
        }
    }

    fun stop(context: Context): JSONObject {
        val p = synchronized(lock) {
            val current = process
            process = null
            current
        }
        if (p != null) {
            try {
                p.destroy()
                p.waitFor(3, TimeUnit.SECONDS)
            } catch (_: Throwable) {
                // 已经退出的进程再 destroy 会抛异常，忽略即可。
            }
        }
        return state(false, "")
    }

    fun status(context: Context): JSONObject {
        val p = synchronized(lock) { process }
        val running = p != null && alive(p!!)
        return state(running, if (running) "" else lastError)
    }

    fun logTail(context: Context, lines: Int): String {
        val f = logFile(context)
        if (!f.isFile) return "Go 网关还没有产生日志"
        val all = try {
            f.readLines()
        } catch (t: Throwable) {
            return "日志不可用: ${t.message}"
        }
        return all.takeLast(lines.coerceAtLeast(1)).joinToString("\n")
            .ifBlank { "Go 网关还没有产生日志" }
    }

    private fun alive(p: Process): Boolean = try {
        p.exitValue()
        false
    } catch (_: IllegalThreadStateException) {
        true
    }

    /** 探活：Go 的 /healthz 在池不可服务时返回 503，仍然算「进程活着」。 */
    private fun probe(port: Int): JSONObject? {
        var conn: HttpURLConnection? = null
        return try {
            val path = GatewayProfile.healthPath(GatewayProfile.GO)
            conn = URL("http://127.0.0.1:$port$path").openConnection() as HttpURLConnection
            conn.connectTimeout = 700
            conn.readTimeout = 700
            val code = conn.responseCode
            if (code != 200 && code != 503) return null
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (body.isBlank()) JSONObject() else JSONObject(body)
        } catch (_: Throwable) {
            null
        } finally {
            conn?.disconnect()
        }
    }

    private fun state(running: Boolean, error: String): JSONObject {
        val result = JSONObject().put("ok", true).put("running", running)
        if (error.isNotEmpty()) result.put("error", error)
        return result
    }
}
