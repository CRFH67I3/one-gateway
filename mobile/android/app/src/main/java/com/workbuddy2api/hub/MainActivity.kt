package com.workbuddy2api.hub

import android.app.Activity
import android.app.AlarmManager
import android.app.AlertDialog
import android.app.PendingIntent
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * 主界面 = 原封不动的 PC 面板。
 *
 * 移植策略：PC 版的看板（dashboard.html）本身就是同源静态页 + 一堆 JSON 接口，
 * 用 WebView 直接加载 `http://127.0.0.1:<port>/` 就等于「移植了全部界面和交互」，
 * 面板以后加功能，App 不用改。原生只负责三件面板做不到的事：常驻托管、
 * 系统通知、以及文件导入等平台能力。
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var statusText: TextView
    private lateinit var statusDot: View
    private lateinit var overlay: LinearLayout
    private lateinit var overlayText: TextView
    private lateinit var overlayAction: Button
    private lateinit var overlayAuto: Button

    private val io = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private var loadedUrl: String? = null

    /**
     * 自动导入是否正在进行。
     *
     * 它挡两件事：一是重复点击（下载 2.6MB 期间用户很容易连点），二是状态轮询
     * 把覆盖层文案改回「尚未导入」——轮询每 2.5s 跑一次，不挡住就会把进度条
     * 刷成静态提示，用户以为卡死了。
     */
    private var autoImporting = false

    /**
     * 这一轮走的是「更新」还是「首次导入」。
     *
     * 两条路径的下载、解压、重启完全一致，只有文案不同：首装说「导入」，
     * 装过之后说「更新」。用字段而不是参数传到最后，是因为进度回调分散在
     * 好几处，只有这样才能让每一处文案都跟着同一轮的口径走。
     */
    private var autoImportIsUpdate = false

    private data class Health(
        val reachable: Boolean,
        val accounts: Int,
        val usable: Int,
        val error: String = "",
    )

    private val poller = object : Runnable {
        override fun run() {
            io.execute {
                val health = fetchHealth()
                ui.post { render(health) }
            }
            ui.postDelayed(this, 2500)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        swipe = findViewById(R.id.swipe)
        statusText = findViewById(R.id.statusText)
        statusDot = findViewById(R.id.statusDot)
        overlay = findViewById(R.id.overlay)
        overlayText = findViewById(R.id.overlayText)
        overlayAction = findViewById(R.id.overlayAction)
        overlayAuto = findViewById(R.id.overlayAutoImport)

        configureWebView()
        swipe.setOnRefreshListener { webView.reload() }
        overlayAction.setOnClickListener {
            if (GatewayBridge.projectRoot(this) == null) {
                // 没有项目时，这个按钮的语义是「导入压缩包」而不是「启动」。
                launchProjectImport()
            } else {
                overlayText.text = getString(R.string.overlay_starting)
                overlayAction.text = getString(R.string.overlay_action)
                GatewayService.send(this, GatewayService.ACTION_START)
                ui.postDelayed(poller, 600)
            }
        }
        overlayAuto.setOnClickListener { startAutoImport() }
        findViewById<ImageButton>(R.id.menuButton).setOnClickListener { showMenu(it) }

        // 只对「已经导入过项目」的机器自动启动：首次安装应当先看到导入引导。
        // 重启进程安装新版本时会带上 EXTRA_AUTO_START——那时候必须起来，否则
        // 用户刚更新完（或刚导入覆盖旧版本）会看到一个「已停止」的空壳。
        val forceStart = intent?.getBooleanExtra(EXTRA_AUTO_START, false) == true
        if ((Prefs.autoStart(this) || forceStart) && GatewayBridge.projectRoot(this) != null) {
            GatewayService.send(this, GatewayService.ACTION_START)
        }
    }

    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            useWideViewPort = true
            loadWithOverviewMode = false
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            // 面板会把接口地址渲染成可复制的文本，混合内容放宽避免 http 资源被拦。
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        }
        webView.setBackgroundColor(0xFFFFFFFF.toInt())
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                swipe.isRefreshing = false
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                // 只有主文档失败才提示：面板里的子资源（图标、字体）失败不该挡屏。
                if (request?.isForMainFrame == true) {
                    overlay.visibility = View.VISIBLE
                    overlayText.text = getString(R.string.overlay_failed, error?.description ?: "")
                }
            }
        }
        installJsDialogBridge()
    }

    /**
     * 面板里大量用 `alert()` / `confirm()` 做提示与二次确认（关闭网关、删除账号、清空日志、
     * 清空 Key…）。WebView 在没有 WebChromeClient 时会把 JS 对话框直接丢掉：`confirm()`
     * 立刻返回 false，用户看到的就是「点了没反应」。这里把三个方法接到原生对话框上。
     */
    private fun installJsDialogBridge() {
        webView.webChromeClient = object : WebChromeClient() {
            override fun onJsAlert(view: WebView?, url: String?, message: String?, result: JsResult?): Boolean {
                if (result == null) return false
                showJsDialog(message.orEmpty(), null, okOnly = true) { result.confirm() }
                return true
            }

            override fun onJsConfirm(view: WebView?, url: String?, message: String?, result: JsResult?): Boolean {
                if (result == null) return false
                showJsDialog(message.orEmpty(), null, okOnly = false) { ok ->
                    if (ok) result.confirm() else result.cancel()
                }
                return true
            }

            override fun onJsPrompt(
                view: WebView?,
                url: String?,
                message: String?,
                defaultValue: String?,
                result: JsPromptResult?,
            ): Boolean {
                if (result == null) return false
                val input = EditText(this@MainActivity).apply {
                    setText(defaultValue.orEmpty())
                    setSelection(text.length)
                    setSingleLine()
                }
                showJsDialog(message.orEmpty(), input, okOnly = false) { ok ->
                    if (ok) result.confirm(input.text.toString()) else result.cancel()
                }
                return true
            }

            override fun onCreateWindow(
                view: WebView?,
                isDialog: Boolean,
                isUserGesture: Boolean,
                resultMsg: Message?,
            ): Boolean {
                // 面板的「在新窗口打开运行日志」是 target=_blank，WebView 默认不处理 = 点了没反应。
                // 手机上就用同一个 WebView 打开，不为一个链接再开一屏。
                val transport = resultMsg?.obj as? WebView.WebViewTransport ?: return false
                val capture = WebView(this@MainActivity)
                capture.webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(v: WebView?, request: WebResourceRequest?): Boolean {
                        request?.url?.let { webView.loadUrl(it.toString()) }
                        capture.post { capture.destroy() }
                        return true
                    }
                }
                transport.webView = capture
                resultMsg?.sendToTarget()
                return true
            }
        }
    }

    private fun showJsDialog(message: String, input: EditText?, okOnly: Boolean, onResult: (Boolean) -> Unit) {
        val builder = AlertDialog.Builder(this)
            .setMessage(message)
            .setPositiveButton(android.R.string.ok) { _, _ -> onResult(true) }
            .setOnCancelListener { onResult(false) }
        if (!okOnly) {
            builder.setNegativeButton(android.R.string.cancel) { _, _ -> onResult(false) }
        }
        if (input != null) builder.setView(input)
        builder.show()
    }

    override fun onResume() {
        super.onResume()
        ui.post(poller)
    }

    override fun onPause() {
        ui.removeCallbacks(poller)
        super.onPause()
    }

    override fun onDestroy() {
        ui.removeCallbacks(poller)
        io.shutdown()
        super.onDestroy()
    }

    @Deprecated("android.app.Activity 的旧式结果回调，避免为一个文件选择引入 androidx.activity")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        when (requestCode) {
            REQUEST_IMPORT -> importCredential(uri)
            REQUEST_PROJECT -> importProject(uri)
        }
    }

    /**
     * 导入网关项目压缩包（从仓库 Download ZIP 得到的原包）。
     *
     * 解压交给 Python 侧做：路径校验、找项目根、读版本号都在那边，Kotlin 只负责
     * 把 SAF 的输入流落到缓存文件。导入成功后直接重启进程——网关模块一旦被
     * import 过就留在 sys.modules 里，热替换成新版本并不可靠，重启一次最干净。
     */
    private fun importProject(uri: Uri) {
        Toast.makeText(this, R.string.project_installing, Toast.LENGTH_SHORT).show()
        io.execute {
            // 先停掉前台服务，避免解压与运行中的网关抢同一份数据目录。
            GatewayService.send(this, GatewayService.ACTION_STOP)
            Thread.sleep(300)
            val zip = GatewayBridge.stageZip(this) { contentResolver.openInputStream(uri) }
            if (zip == null) {
                ui.post {
                    Toast.makeText(
                        this,
                        getString(R.string.project_import_failed, "读取压缩包失败"),
                        Toast.LENGTH_LONG,
                    ).show()
                }
                return@execute
            }
            applyProject(zip)
        }
    }

    /**
     * 菜单里的「更新网关项目」：走与首装「自动导入」完全相同的下载/解压流程。
     *
     * 没导入过就没有「更新」可言，退回导入语义——菜单里两项都在，用户点错时
     * 期望的是「把事情办了」，而不是被一句「还没有项目」挡回来。
     */
    private fun startUpdate() {
        if (autoImporting) return
        startAutoImport(update = GatewayBridge.projectRoot(this) != null)
    }

    /**
     * 「自动导入」：直接下载仓库 main 的最新 ZIP 再装上。
     *
     * 和手动导入相比只多了「下载」这一步——解压、切指针、重启进程全部复用
     * [applyProject]，两条路径的产物完全一致，不需要在手机上再验证一遍逻辑。
     * 下载在同一个单线程 executor 上排队，跟解压天然互斥。
     *
     * @param update 已经装过项目时用「更新」的口径（文案与状态栏都换成更新）。
     */
    private fun startAutoImport(update: Boolean = false) {
        if (autoImporting) return
        autoImporting = true
        autoImportIsUpdate = update
        // 更新时面板还开着，也必须把覆盖层拉出来：下载 2.6MB 有好几秒，
        // 只靠 Toast 看不到百分比，用户会以为点了没反应。
        overlay.visibility = View.VISIBLE
        overlayAction.isEnabled = false
        overlayAuto.isEnabled = false
        overlayAuto.visibility = if (update) View.GONE else View.VISIBLE
        overlayAuto.text = getString(if (update) R.string.update_running else R.string.auto_import_running)
        overlayText.text = getString(R.string.auto_import_connecting, 1, GatewayBridge.PROJECT_ZIP_URLS.size)
        statusText.text = getString(if (update) R.string.status_updating else R.string.status_importing)
        io.execute {
            GatewayService.send(this, GatewayService.ACTION_STOP)
            Thread.sleep(300)
            val download = GatewayBridge.downloadProjectZip(
                this,
                onAttempt = { index, count ->
                    ui.post {
                        if (autoImporting) {
                            overlayText.text = getString(R.string.auto_import_connecting, index, count)
                        }
                    }
                },
                onProgress = { pct, done, total ->
                    ui.post {
                        if (!autoImporting) return@post
                        overlayText.text = when {
                            pct < 0 || total <= 0 ->
                                getString(R.string.auto_import_progress_unknown, formatBytes(done))
                            autoImportIsUpdate ->
                                getString(R.string.update_downloading, pct, formatBytes(done))
                            else ->
                                getString(R.string.auto_import_progress, pct, formatBytes(done))
                        }
                    }
                },
            )
            val zip = download.file
            if (zip == null) {
                val message = getString(
                    if (update) R.string.update_failed else R.string.auto_import_failed,
                    download.error,
                )
                ui.post { finishAutoImport(message) }
                return@execute
            }
            ui.post {
                overlayText.text = getString(
                    if (update) R.string.update_installing else R.string.auto_import_installing
                )
            }
            applyProject(zip, update)
        }
    }

    /** 自动导入/更新失败后的收尾：恢复按钮与引导文案，让用户能直接再试一次。 */
    private fun finishAutoImport(message: String?) {
        autoImporting = false
        val hasProject = GatewayBridge.projectRoot(this) != null
        overlayAction.isEnabled = true
        overlayAuto.isEnabled = true
        // 自动导入按钮只服务首装：已经装过项目时它不该再占位置（更新走菜单）。
        overlayAuto.visibility = if (hasProject) View.GONE else View.VISIBLE
        overlayAuto.text = getString(R.string.overlay_auto_import)
        autoImportIsUpdate = false
        if (message != null) {
            if (hasProject) {
                // 更新失败：旧项目还在、面板还能用，把覆盖层收起来只弹一句原因，
                // 别让一个失败的提示挡住用户继续干活。
                overlay.visibility = View.GONE
                loadedUrl = null
            } else {
                overlayText.text = getString(R.string.overlay_no_project)
            }
            Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        }
    }

    /**
     * 把已经落到缓存的 ZIP 装上：解压 → 提示 → 重启。
     *
     * 手动导入、自动导入、菜单更新三条路径共用，避免它们在「成功后是否重启」
     * 「失败文案」这类细节上跑偏。调用方必须在后台线程（内部会做解压重活）。
     *
     * @param update 已经装过项目时的覆盖升级，决定成功/失败用「更新」口径的文案。
     */
    private fun applyProject(zip: File, update: Boolean = false) {
        val result = GatewayBridge.installProject(this, zip)
        // 解释器调用留在后台线程：进 UI 线程前就把「要不要重启」问清楚。
        val needsRestart = result.optBoolean("ok") && GatewayBridge.gatewayLoadedInProcess()
        ui.post {
            if (!result.optBoolean("ok")) {
                val message = getString(
                    if (update) R.string.update_failed else R.string.project_import_failed,
                    result.optString("error"),
                )
                finishAutoImport(message)
                return@post
            }
            val version = result.optString("version").ifBlank { "未知版本" }
            val files = result.optInt("files_extracted", 0)
            Toast.makeText(
                this,
                getString(
                    if (update) R.string.update_ok else R.string.project_import_ok,
                    version,
                    files,
                ),
                Toast.LENGTH_LONG,
            ).show()
            finishAutoImport(null)
            if (needsRestart) {
                // 覆盖升级：旧网关模块和它的线程还留在解释器里，只能重启进程。
                restartApp()
            } else {
                // 首次安装：进程里没有旧模块，不重启——重启会把用户甩回桌面，
                // 看起来像导入完就闪退了。直接起服务，覆盖层会自己切到面板。
                loadedUrl = null
                GatewayService.send(this, GatewayService.ACTION_START)
                ui.postDelayed(poller, 600)
            }
        }
    }

    private fun formatBytes(bytes: Long): String = when {
        bytes >= 1 shl 20 -> String.format(java.util.Locale.US, "%.1f MB", bytes / 1048576.0)
        bytes >= 1 shl 10 -> String.format(java.util.Locale.US, "%d KB", bytes / 1024)
        else -> "$bytes B"
    }

    /**
     * 让新导入的项目在干净的进程里生效（旧模块不能可靠地热替换）。
     *
     * 进程一死，就没有任何线程能把界面再拉到前台了，所以「回来」这件事必须交给
     * 系统：用一个 PendingIntent 闹钟在 600ms 后重新拉起 MainActivity。600ms 是
     * 给旧进程退出留的时间，太短会在新进程起来前又被系统判定为上一个实例。
     * 拉起时带 EXTRA_AUTO_START：刚装完新版本，无论「打开时自动启动」开着没有，
     * 都该把网关跑起来。
     */
    private fun restartApp() {
        val intent = Intent(this, MainActivity::class.java).addFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        ).putExtra(EXTRA_AUTO_START, true)
        val pending = PendingIntent.getActivity(
            this,
            RESTART_REQUEST,
            intent,
            PendingIntent.FLAG_CANCEL_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val alarms = getSystemService(Context.ALARM_SERVICE) as AlarmManager
        alarms.set(AlarmManager.RTC, System.currentTimeMillis() + 600, pending)
        finishAffinity()
        ui.postDelayed({
            android.os.Process.killProcess(android.os.Process.myPid())
        }, 250)
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    private fun importCredential(uri: Uri) {
        Toast.makeText(this, R.string.import_running, Toast.LENGTH_SHORT).show()
        io.execute {
            val target = File(cacheDir, "imported-credential.json")
            val ok = try {
                contentResolver.openInputStream(uri)?.use { input ->
                    target.outputStream().use { output -> input.copyTo(output) }
                }
                true
            } catch (t: Throwable) {
                false
            }
            val message = if (!ok) {
                getString(R.string.import_failed, "读取文件失败")
            } else {
                // 账号池在服务进程里，先确保服务已启动再导入。
                GatewayService.send(this, GatewayService.ACTION_START)
                Thread.sleep(400)
                val result = GatewayBridge.importCredential(this, target.absolutePath)
                if (result.optBoolean("ok")) {
                    getString(R.string.import_ok, result.optInt("accounts", 0))
                } else {
                    getString(R.string.import_failed, result.optString("error"))
                }
            }
            ui.post {
                Toast.makeText(this, message, Toast.LENGTH_LONG).show()
                webView.reload()
            }
        }
    }

    // ---------------------------------------------------------------- 菜单
    private fun showMenu(anchor: View) {
        val menu = PopupMenu(this, anchor)
        menu.menu.add(0, MENU_RESTART, 0, getString(R.string.menu_restart))
        menu.menu.add(0, MENU_SETTINGS, 1, getString(R.string.menu_settings))
        // 「导入」与「更新」是两件事：前者选本地 ZIP（离线、可装任意版本），
        // 后者联网下仓库 main（一键升级）。合在一个入口里，用户每次都得先想
        // 「我要的是哪一个」，而且「更新」还得先自己去找下载地址。
        menu.menu.add(0, MENU_PROJECT, 2, getString(R.string.menu_project_import))
        menu.menu.add(0, MENU_PROJECT_UPDATE, 3, getString(R.string.menu_project_update))
        menu.menu.add(0, MENU_PROJECT_INFO, 4, getString(R.string.menu_project_info))
        menu.menu.add(0, MENU_IMPORT, 5, getString(R.string.menu_import))
        menu.menu.add(0, MENU_COPY, 6, getString(R.string.menu_copy))
        menu.menu.add(0, MENU_LOG, 7, getString(R.string.menu_log))
        menu.menu.add(0, MENU_ABOUT, 8, getString(R.string.menu_about))
        menu.menu.add(0, MENU_EXIT, 9, getString(R.string.menu_exit))
        menu.setOnMenuItemClickListener { item: MenuItem ->
            when (item.itemId) {
                MENU_RESTART -> {
                    GatewayService.send(this, GatewayService.ACTION_RESTART)
                    Toast.makeText(this, R.string.toast_restarting, Toast.LENGTH_SHORT).show()
                }
                MENU_SETTINGS -> showSettings()
                MENU_PROJECT -> launchProjectImport()
                MENU_PROJECT_UPDATE -> startUpdate()
                MENU_PROJECT_INFO -> showProjectInfo()
                MENU_IMPORT -> launchImport()
                MENU_COPY -> copyApiBase()
                MENU_LOG -> showLog()
                MENU_ABOUT -> showAbout()
                MENU_EXIT -> exitApp()
            }
            true
        }
        menu.show()
    }

    /**
     * 退出：停掉网关与前台服务，然后关掉 App。
     *
     * 菜单里不再单列「启动/停止」：停止网关仍可从通知栏的「停止」按钮做（那才是
     * 「想让它别跑」的入口），启动则由覆盖层的「启动网关」按钮或「打开 App 时自动
     * 启动」负责。菜单里的「退出」语义是「我要走了」——所以要连网关一起停掉，
     * 否则前台服务会把 App 进程留在后台，通知栏也一直挂着，用户以为退出了其实没退。
     */
    private fun exitApp() {
        GatewayService.send(this, GatewayService.ACTION_STOP)
        loadedUrl = null
        Toast.makeText(this, R.string.toast_exited, Toast.LENGTH_SHORT).show()
        finishAffinity()
    }

    private fun launchImport() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
        }
        startActivityForResult(intent, REQUEST_IMPORT)
    }

    /**
     * 选一个网关项目压缩包。
     *
     * type 故意留成通配：不同文件管理器给 .zip 报的 MIME 并不统一
     * （application/zip、application/x-zip-compressed、octet-stream 都见过），
     * 限定成 zip 会让一部分机型里文件直接变灰选不中。选中后由 Python 侧校验，
     * 不是 ZIP 会明确报错，比「选不中」好排查。
     */
    private fun launchProjectImport() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
        }
        startActivityForResult(intent, REQUEST_PROJECT)
    }

    private fun showProjectInfo() {
        val root = GatewayBridge.projectRoot(this)
        if (root == null) {
            AlertDialog.Builder(this)
                .setTitle(R.string.menu_project_info)
                .setMessage(R.string.project_none_hint)
                .setPositiveButton(R.string.project_import_action) { _, _ -> launchProjectImport() }
                .setNegativeButton(android.R.string.cancel, null)
                .show()
            return
        }
        val info = GatewayBridge.describeProject(this)
        val imported = Prefs.projectImportedAt(this)
        val stamp = if (imported > 0) {
            java.text.SimpleDateFormat("yyyy-MM-dd HH:mm", java.util.Locale.getDefault())
                .format(java.util.Date(imported))
        } else {
            "未知"
        }
        val body = buildString {
            append(getString(R.string.project_version, info.optString("version").ifBlank { "未知" }))
            append('\n')
            append(getString(R.string.project_imported, stamp))
            append('\n')
            append(getString(R.string.project_path, root.absolutePath))
            append('\n')
            append(getString(R.string.project_panel, if (info.optBoolean("has_panel")) "正常" else "缺失 dashboard.html"))
            append('\n')
            append(getString(R.string.project_data, GatewayBridge.dataDir(this@MainActivity).absolutePath))
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.menu_project_info)
            .setMessage(body)
            .setPositiveButton(R.string.project_update_action) { _, _ -> startUpdate() }
            .setNegativeButton(android.R.string.ok, null)
            .show()
    }

    private fun copyApiBase() {
        val port = Prefs.port(this)
        val text = "http://127.0.0.1:$port/v1"
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("api-base", text))
        Toast.makeText(this, getString(R.string.toast_copied, text), Toast.LENGTH_LONG).show()
    }

    private fun showSettings() {
        val padding = (16 * resources.displayMetrics.density).toInt()
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding, padding, 0)
        }

        val portLabel = TextView(this).apply { text = getString(R.string.settings_port) }
        val portInput = EditText(this).apply {
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
            setText(Prefs.port(this@MainActivity).toString())
        }
        val lanSwitch = Switch(this).apply {
            text = getString(R.string.settings_lan)
            isChecked = Prefs.lan(this@MainActivity)
        }
        val autoSwitch = Switch(this).apply {
            text = getString(R.string.settings_autostart)
            isChecked = Prefs.autoStart(this@MainActivity)
        }
        val hint = TextView(this).apply {
            text = getString(R.string.settings_hint)
            textSize = 12f
            setPadding(0, padding / 2, 0, 0)
        }
        container.addView(portLabel)
        container.addView(portInput)
        container.addView(lanSwitch)
        container.addView(autoSwitch)
        container.addView(hint)

        AlertDialog.Builder(this)
            .setTitle(R.string.menu_settings)
            .setView(ScrollView(this).apply { addView(container) })
            .setPositiveButton(android.R.string.ok) { _, _ ->
                val port = portInput.text.toString().toIntOrNull()
                if (port == null || port !in 1024..65535) {
                    Toast.makeText(this, R.string.settings_bad_port, Toast.LENGTH_LONG).show()
                    return@setPositiveButton
                }
                val changed = port != Prefs.port(this) || lanSwitch.isChecked != Prefs.lan(this)
                Prefs.setPort(this, port)
                Prefs.setLan(this, lanSwitch.isChecked)
                Prefs.setAutoStart(this, autoSwitch.isChecked)
                if (changed) {
                    loadedUrl = null
                    GatewayService.send(this, GatewayService.ACTION_RESTART)
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun showLog() {
        val text = GatewayBridge.logTail(this, 400).ifBlank { getString(R.string.log_empty) }
        val view = TextView(this).apply {
            this.text = text
            textSize = 11f
            setPadding(24, 16, 24, 16)
            setTextIsSelectable(true)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.menu_log)
            .setView(ScrollView(this).apply { addView(view) })
            .setPositiveButton(android.R.string.ok, null)
            .show()
    }

    private fun showAbout() {
        AlertDialog.Builder(this)
            .setTitle(R.string.app_name)
            .setMessage(getString(R.string.about_body, BuildConfig.VERSION_NAME))
            .setPositiveButton(android.R.string.ok, null)
            .show()
    }

    // ---------------------------------------------------------------- 状态
    /**
     * 探活。**先问自己的运行环境，再看端口通不通。**
     *
     * 只看 `127.0.0.1:<port>/health` 是不够的：端口被别的进程占着时（典型场景是
     * 用户在同一台手机的 Termux 里也跑着一份网关），HTTP 探活会探到**别人**，
     * 界面显示「运行中」、WebView 载入别人的面板，而本 App 的网关其实根本没起来。
     * 运行环境（`GatewayService.lastStatus`）才是「我们这份有没有在跑」的唯一真源。
     */
    private fun fetchHealth(): Health {
        val state = GatewayService.lastStatus
        if (state == null || !state.optBoolean("running")) {
            return Health(false, 0, 0, state?.optString("error").orEmpty())
        }
        val port = Prefs.port(this)
        val url = URL("http://127.0.0.1:$port/health")
        val connection = url.openConnection() as HttpURLConnection
        return try {
            connection.connectTimeout = 1500
            connection.readTimeout = 1500
            connection.requestMethod = "GET"
            if (connection.responseCode != 200) return Health(false, 0, 0)
            val body = connection.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)
            Health(true, json.optInt("accounts", 0), json.optInt("accounts_ready", 0))
        } catch (t: Throwable) {
            Health(false, 0, 0)
        } finally {
            connection.disconnect()
        }
    }

    private fun render(health: Health) {
        val port = Prefs.port(this)

        // 自动导入/更新期间，覆盖层与状态栏都归进度文案管：轮询每 2.5s 跑一次，
        // 若在这里改写，会分别造成「进度被打断」「覆盖层被藏起来」「网关还没停干净
        // 的瞬间把面板载进来」三种错觉。等收尾（finishAutoImport）之后再交给轮询。
        if (autoImporting) return

        // 没导入项目时，这个 App 只是一个空壳：不提示「已停止」，而是直接把
        // 「导入压缩包」摆在最显眼的位置——这是首次安装后唯一该做的事。
        if (GatewayBridge.projectRoot(this) == null) {
            statusDot.setBackgroundResource(R.drawable.dot_idle)
            statusText.text = getString(R.string.status_no_project)
            overlay.visibility = View.VISIBLE
            overlayAuto.visibility = View.VISIBLE
            overlayText.text = getString(R.string.overlay_no_project)
            overlayAction.text = getString(R.string.overlay_import_project)
            overlayAction.isEnabled = true
            return
        }
        overlayAction.text = getString(R.string.overlay_action)
        // 项目已导入：自动导入只服务首次安装，装好之后就不再占位置。
        overlayAuto.visibility = View.GONE

        if (health.reachable) {
            statusDot.setBackgroundResource(R.drawable.dot_running)
            statusText.text = getString(R.string.status_running, port, health.usable, health.accounts)
            overlay.visibility = View.GONE
            val target = "http://127.0.0.1:$port/"
            if (loadedUrl != target) {
                loadedUrl = target
                webView.loadUrl(target)
            }
        } else {
            statusDot.setBackgroundResource(R.drawable.dot_idle)
            if (health.error.isNotBlank()) {
                // 起不来时把原因摆出来。只说「已停止」用户不知道下一步做什么，
                // 而端口被占用（同一台手机上还跑着另一份网关）是最常见的一种。
                statusText.text = getString(R.string.status_failed, health.error)
                overlay.visibility = View.VISIBLE
                overlayText.text = getString(R.string.overlay_start_failed, health.error)
            } else {
                statusText.text = if (GatewayService.active) {
                    getString(R.string.status_starting)
                } else {
                    getString(R.string.status_stopped)
                }
                if (loadedUrl == null) {
                    overlay.visibility = View.VISIBLE
                    overlayText.text = getString(R.string.overlay_idle)
                }
            }
        }
    }

    companion object {
        private const val REQUEST_IMPORT = 1001
        private const val REQUEST_PROJECT = 1002
        private const val RESTART_REQUEST = 2001
        /** 重启进程拉起界面时带上：刚装完新版本，必须把网关跑起来。 */
        private const val EXTRA_AUTO_START = "com.workbuddy2api.hub.AUTO_START"
        private const val MENU_RESTART = 2
        private const val MENU_SETTINGS = 3
        private const val MENU_PROJECT = 4
        private const val MENU_PROJECT_UPDATE = 11
        private const val MENU_PROJECT_INFO = 5
        private const val MENU_IMPORT = 6
        private const val MENU_COPY = 7
        private const val MENU_LOG = 8
        private const val MENU_ABOUT = 9
        private const val MENU_EXIT = 10
    }
}
