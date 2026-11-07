package com.proxygateway.runtime

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
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
import androidx.core.content.ContextCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * 主界面 = 原封不动的 PC 面板。
 *
 * 移植策略：导入项目自带的看板（dashboard.html）是同源静态页 + 一堆 JSON 接口，
 * 用 WebView 直接加载 `http://127.0.0.1:<port>/` 就等于「移植了全部界面和交互」，
 * 面板以后加功能，App 不用改。原生只负责三件面板做不到的事：常驻托管、
 * 系统通知、以及文件导入等平台能力。
 */
class MainActivity : Activity() {

    private lateinit var rootView: View
    private lateinit var headerView: View
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

    /** 上一次刷外壳颜色时的系统深浅；null 表示还没刷过。 */
    private var appliedDark: Boolean? = null

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

        rootView = findViewById(R.id.root)
        headerView = findViewById(R.id.header)
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
                // 没有项目时，这个按钮的语义是「手动更新」而不是「启动」。
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

        // 界面搭完之后统一刷一次外壳配色，深浅两档都由它决定。
        applyShellColors()

        // 只对「已经导入过项目」的机器自动启动：首次安装应当先看到导入引导。
        if (Prefs.autoStart(this) && GatewayBridge.projectRoot(this) != null) {
            GatewayService.send(this, GatewayService.ACTION_START)
        }
    }

    /**
     * 把外壳（状态栏、顶栏、覆盖层、按钮、WebView 首帧底色）刷成当前系统的深浅。
     *
     * 为什么需要手动刷：清单放行了 `uiMode`，切深浅时 Activity 不再重建，XML 里那些
     * `@color/page_bg`、`@color/header_bg` 早已 inflate 成旧颜色，不会自己变；主题里的
     * `windowLightStatusBar` 同理。所以颜色只有这一处出口，`onCreate` 与
     * `onConfigurationChanged` 都走它，两档不可能走岔。
     */
    private fun applyShellColors() {
        val dark = systemDark()
        appliedDark = dark
        val page = ContextCompat.getColor(this, R.color.page_bg)
        val header = ContextCompat.getColor(this, R.color.header_bg)

        rootView.setBackgroundColor(page)
        headerView.setBackgroundColor(header)
        overlay.setBackgroundColor(page)
        statusText.setTextColor(ContextCompat.getColor(this, R.color.text_primary))
        overlayText.setTextColor(ContextCompat.getColor(this, R.color.text_secondary))

        // 底色跟顶栏，图标要跟底色反着来：深色顶栏配深色图标等于没有图标。
        window.statusBarColor = header
        window.navigationBarColor = header
        WindowInsetsControllerCompat(window, window.decorView).apply {
            isAppearanceLightStatusBars = !dark
            isAppearanceLightNavigationBars = !dark
        }

        // 按钮的外观是 drawable + color selector，同样不会随配置自动重刷。
        overlayAction.setBackgroundResource(R.drawable.bg_btn_primary)
        overlayAction.setTextColor(
            ContextCompat.getColorStateList(this, R.color.btn_label_on_primary)
        )
        overlayAuto.setBackgroundResource(R.drawable.bg_btn_secondary)
        overlayAuto.setTextColor(
            ContextCompat.getColorStateList(this, R.color.btn_label_secondary)
        )

        // 面板第一帧到达前露的就是它，与面板首屏同色才不会闪一下。
        webView.setBackgroundColor(if (dark) COLOR_WEB_BG_DARK else COLOR_WEB_BG_LIGHT)
    }

    /**
     * 切系统深浅只换色，不重建界面。
     *
     * 重建的代价是 WebView 一起销毁：面板的登录态活在页面内存里（不是 cookie），
     * 用户只是把系统调成深色，回来就得重新输密码——真机实测会掉两次登录。
     * 所以这里原地换肤，再把新值派给页面：`dashboard.html` 监听 `wb:system-theme`，
     * 收到后切 `data-theme`，页面不重载、会话不丢。
     */
    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applyShellColors()
        val dark = systemDark()
        webView.evaluateJavascript(
            "window.dispatchEvent(new CustomEvent('wb:system-theme',{detail:{dark:$dark}}))",
            null,
        )
        // 页面没有重新加载，URL 里的 sysdark 还是旧值。这里同步标记，
        // 否则状态轮询（render）会发现 loadedUrl != panelUrl 而立刻 loadUrl，
        // 等于白换肤——刚保住的登录态照样没了。
        if (loadedUrl != null) loadedUrl = panelUrl(Prefs.port(this))
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
        // 底色（页面第一帧到达之前露的那一层）统一由 applyShellColors() 设置，
        // 深浅两档只有一处出口。
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
                    // 顺手把「已加载」标记清掉。换代码时要先停网关，WebView 这一瞬间必然
                    // 撞上 ERR_CONNECTION_REFUSED 并渲染系统错误页；而 render() 只在
                    // loadedUrl != target 时才 loadUrl，标记不清就会一直停在错误页，
                    // 直到用户重启 App（真机复现过）。清掉后网关一起来 poller 就自动重载。
                    loadedUrl = null
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
        // 在后台被切了深浅再回来时，系统不一定会补一次 onConfigurationChanged
        // （Activity 已 stop 的情况下）。这里比一下标记，变了就补刷，避免顶栏
        // 停在旧配色上。
        if (appliedDark != systemDark()) applyShellColors()
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
        // 现在只剩文件选择：手动更新的 ZIP。账号导入已经回到面板里做（OAuth）。
        if (requestCode == REQUEST_PROJECT) importProject(uri)
    }

    /**
     * 手动更新：装上用户选中的那份网关 ZIP（仓库 Download ZIP 的原包）。
     *
     * 解压交给 Python 侧做：路径校验、找项目根、读版本号都在那边，Kotlin 只负责
     * 把 SAF 的输入流落到缓存文件。文件只要是个能认出项目根的 ZIP 就行——不看
     * 来源、不要凭证，换代码这件事本身没有身份概念。
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
            // 装过项目就是「更新」口径（失败也别报成安装失败）。
            applyProject(zip, update = GatewayBridge.projectRoot(this) != null)
        }
    }

    /**
     * 菜单里的「自动更新」：走与首装「自动更新」完全相同的下载/解压流程。
     *
     * 没装过项目时也算一次更新（区别只在文案），用户点它就是要最新的一份，
     * 不该被一句「还没有项目」挡回来。
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
     * 把已经落到缓存的 ZIP 装上：解压 → 提示 → 重新起服务。
     *
     * 手动导入、自动导入、菜单更新三条路径共用，避免它们在「失败文案」这类
     * 细节上跑偏。调用方必须在后台线程（内部会做解压重活）。
     *
     * 全程不重启进程：Python 侧 install() 已经会把旧项目 import 进来的模块
     * 从 sys.modules 里摘掉，所以这里直接重新 start() 加载的就是新代码。
     * （重启进程在 Android 10+ 上等于把用户丢在桌面——进程死后系统会拒绝
     * 后台启动 Activity，闹钟怎么排都没用，真机验证过。）
     *
     * @param update 已经装过项目时的覆盖升级，决定成功/失败用「更新」口径的文案。
     */
    private fun applyProject(zip: File, update: Boolean = false) {
        val result = GatewayBridge.installProject(this, zip)
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
            // 置空 loadedUrl：面板要按新版本重新载一次，否则 WebView 里还是
            // 旧项目 render 出来的那一页。
            loadedUrl = null
            GatewayService.send(this, GatewayService.ACTION_START)
            ui.postDelayed(poller, 600)
        }
    }

    private fun formatBytes(bytes: Long): String = when {
        bytes >= 1 shl 20 -> String.format(java.util.Locale.US, "%.1f MB", bytes / 1048576.0)
        bytes >= 1 shl 10 -> String.format(java.util.Locale.US, "%d KB", bytes / 1024)
        else -> "$bytes B"
    }

    /**
     * 手机现在是深色模式吗。
     *
     * 只看 `uiMode` 而不看 `resources.configuration.isNightModeActive`（API 29+），
     * 是因为 minSdk 是 26，位运算在所有支持版本上都可用。
     */
    private fun systemDark(): Boolean =
        (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
            Configuration.UI_MODE_NIGHT_YES

    /**
     * 面板地址。`sysdark=1|0` 是外壳交给页面的「系统深色」权威值：
     *
     * WebView 报的 `prefers-color-scheme` 取决于 **App 主题**而不是系统设置，我们
     * 的外壳是 `Theme.Material.Light`，手机切到深色时它仍然报 light，面板里选
     * 「跟随系统」就会一直停在浅色。把系统值直接放进 URL，页面脚本在首帧之前就能
     * 读到，不依赖任何原生注入的时序。
     */
    private fun panelUrl(port: Int): String =
        "http://127.0.0.1:$port/?sysdark=" + if (systemDark()) "1" else "0"

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    // ---------------------------------------------------------------- 菜单
    private fun showMenu(anchor: View) {
        val menu = PopupMenu(this, anchor)
        menu.menu.add(0, MENU_RESTART, 0, getString(R.string.menu_restart))
        menu.menu.add(0, MENU_SETTINGS, 1, getString(R.string.menu_settings))
        // 「手动更新」与「自动更新」做的是同一件事——换一份网关代码，区别只在
        // 源码从哪来：前者选你手上的 ZIP（离线、可装任意版本），后者联网下仓库
        // main。两条都不需要凭证，所以菜单里不再有「导入项目」这类前置动作。
        menu.menu.add(0, MENU_PROJECT, 2, getString(R.string.menu_project_import))
        menu.menu.add(0, MENU_PROJECT_UPDATE, 3, getString(R.string.menu_project_update))
        menu.menu.add(0, MENU_COPY, 4, getString(R.string.menu_copy))
        menu.menu.add(0, MENU_LOG, 5, getString(R.string.menu_log))
        menu.menu.add(0, MENU_ABOUT, 6, getString(R.string.menu_about))
        menu.menu.add(0, MENU_EXIT, 7, getString(R.string.menu_exit))
        menu.setOnMenuItemClickListener { item: MenuItem ->
            when (item.itemId) {
                MENU_RESTART -> {
                    GatewayService.send(this, GatewayService.ACTION_RESTART)
                    Toast.makeText(this, R.string.toast_restarting, Toast.LENGTH_SHORT).show()
                }
                MENU_SETTINGS -> showSettings()
                MENU_PROJECT -> launchProjectImport()
                MENU_PROJECT_UPDATE -> startUpdate()
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

    /**
     * 「关于与项目信息」：App 版本和网关落在哪一份代码上，放在同一个对话框里。
     *
     * 这两件事原来是两页，但用户打开任何一页想知道的都是「现在跑的到底是什么」，
     * 分成两处只会让人来回切。合并后正好把两个更新入口摆在按钮位上：看着版本号
     * 发现旧了，直接就地更新，不用退出去再翻菜单。
     */
    private fun showAbout() {
        val root = GatewayBridge.projectRoot(this)
        val body = buildString {
            append(getString(R.string.about_app_version, BuildConfig.VERSION_NAME))
            append("\n\n")
            if (root == null) {
                append(getString(R.string.project_none_hint))
            } else {
                val info = GatewayBridge.describeProject(this@MainActivity)
                val imported = Prefs.projectImportedAt(this@MainActivity)
                val stamp = if (imported > 0) {
                    java.text.SimpleDateFormat("yyyy-MM-dd HH:mm", java.util.Locale.getDefault())
                        .format(java.util.Date(imported))
                } else {
                    "未知"
                }
                append(getString(R.string.project_version, info.optString("version").ifBlank { "未知" }))
                append('\n')
                append(getString(R.string.project_imported, stamp))
                append('\n')
                append(getString(R.string.project_path, root.absolutePath))
                append('\n')
                append(getString(R.string.project_panel, if (info.optBoolean("has_panel")) "正常" else "缺失 dashboard.html"))
                append('\n')
                append(getString(R.string.project_data, GatewayBridge.dataDir(this@MainActivity).absolutePath))
                append("\n\n")
            }
            append(getString(R.string.about_body))
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.menu_about)
            .setMessage(body)
            // 两个按钮就是两个更新入口，措辞与菜单一致；「关闭」放 cancel 位。
            .setPositiveButton(R.string.menu_project_import) { _, _ -> launchProjectImport() }
            .setNeutralButton(R.string.menu_project_update) { _, _ -> startUpdate() }
            .setNegativeButton(android.R.string.cancel, null)
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
            // ?sysdark= 把系统深浅色带给面板：WebView 的 prefers-color-scheme 跟的是
            // App 主题（外壳是 Light），手机切深色它照样报浅色，「跟随系统」就失效了。
            // 切深浅时 Activity 不重建（清单放行了 uiMode），页面也不重载，
            // 新值由 onConfigurationChanged 派事件过去；这里只在首次加载时起作用。
            val target = panelUrl(port)
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
        private const val REQUEST_PROJECT = 1002
        // WebView 首帧底色 = 面板的 bg（浅色 #f4f6f9 / 深色 #0f1115），
        // 与 @color/page_bg 同值。面板第一帧到达前露出的就是它，两边同色才不闪。
        private const val COLOR_WEB_BG_LIGHT = 0xFFF4F6F9.toInt()
        private const val COLOR_WEB_BG_DARK = 0xFF0F1115.toInt()
        private const val MENU_RESTART = 2
        private const val MENU_SETTINGS = 3
        private const val MENU_PROJECT = 4
        private const val MENU_PROJECT_UPDATE = 11
        private const val MENU_COPY = 7
        private const val MENU_LOG = 8
        private const val MENU_ABOUT = 9
        private const val MENU_EXIT = 10
    }
}
