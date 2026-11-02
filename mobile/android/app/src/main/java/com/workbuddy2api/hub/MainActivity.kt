package com.workbuddy2api.hub

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
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

    private val io = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private var loadedUrl: String? = null

    private data class Health(val reachable: Boolean, val accounts: Int, val usable: Int)

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

        configureWebView()
        swipe.setOnRefreshListener { webView.reload() }
        overlayAction.setOnClickListener {
            GatewayService.send(this, GatewayService.ACTION_START)
            overlayText.text = getString(R.string.overlay_starting)
            ui.postDelayed(poller, 600)
        }
        findViewById<ImageButton>(R.id.menuButton).setOnClickListener { showMenu(it) }

        if (Prefs.autoStart(this)) {
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
        if (requestCode != REQUEST_IMPORT || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        importCredential(uri)
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

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    // ---------------------------------------------------------------- 菜单
    private fun showMenu(anchor: View) {
        val menu = PopupMenu(this, anchor)
        menu.menu.add(0, MENU_TOGGLE, 0, getString(R.string.menu_toggle))
        menu.menu.add(0, MENU_RESTART, 1, getString(R.string.menu_restart))
        menu.menu.add(0, MENU_SETTINGS, 2, getString(R.string.menu_settings))
        menu.menu.add(0, MENU_IMPORT, 3, getString(R.string.menu_import))
        menu.menu.add(0, MENU_COPY, 4, getString(R.string.menu_copy))
        menu.menu.add(0, MENU_LOG, 5, getString(R.string.menu_log))
        menu.menu.add(0, MENU_ABOUT, 6, getString(R.string.menu_about))
        menu.setOnMenuItemClickListener { item: MenuItem ->
            when (item.itemId) {
                MENU_TOGGLE -> {
                    if (GatewayService.active) {
                        GatewayService.send(this, GatewayService.ACTION_STOP)
                        loadedUrl = null
                        Toast.makeText(this, R.string.toast_stopped, Toast.LENGTH_SHORT).show()
                    } else {
                        GatewayService.send(this, GatewayService.ACTION_START)
                        Toast.makeText(this, R.string.toast_starting, Toast.LENGTH_SHORT).show()
                    }
                }
                MENU_RESTART -> {
                    GatewayService.send(this, GatewayService.ACTION_RESTART)
                    Toast.makeText(this, R.string.toast_restarting, Toast.LENGTH_SHORT).show()
                }
                MENU_SETTINGS -> showSettings()
                MENU_IMPORT -> launchImport()
                MENU_COPY -> copyApiBase()
                MENU_LOG -> showLog()
                MENU_ABOUT -> showAbout()
            }
            true
        }
        menu.show()
    }

    private fun launchImport() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
        }
        startActivityForResult(intent, REQUEST_IMPORT)
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
    private fun fetchHealth(): Health {
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

    companion object {
        private const val REQUEST_IMPORT = 1001
        private const val MENU_TOGGLE = 1
        private const val MENU_RESTART = 2
        private const val MENU_SETTINGS = 3
        private const val MENU_IMPORT = 4
        private const val MENU_COPY = 5
        private const val MENU_LOG = 6
        private const val MENU_ABOUT = 7
    }
}
