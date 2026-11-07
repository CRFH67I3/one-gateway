package com.onegateway.host

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.Executors

/**
 * 承载网关的前台服务。
 *
 * 平台适配点：Android 会冻结后台进程里的网络服务，桌面版「开个窗口一直跑」的形态
 * 在这里必须换成前台服务 + 常驻通知，否则息屏几分钟后所有请求就断了。服务本身
 * 不做业务，只负责跑 Python 并把状态显示在通知里。
 */
class GatewayService : Service() {

    companion object {
        const val ACTION_START = "com.onegateway.host.action.START"
        const val ACTION_STOP = "com.onegateway.host.action.STOP"
        const val ACTION_RESTART = "com.onegateway.host.action.RESTART"

        private const val CHANNEL_ID = "gateway"
        private const val NOTIFICATION_ID = 8788

        /** UI 侧读它决定按钮语义，避免再去问 Python。 */
        @Volatile
        var active: Boolean = false
            private set

        @Volatile
        var lastStatus: JSONObject? = null

        fun send(context: Context, action: String) {
            val intent = Intent(context, GatewayService::class.java).setAction(action)
            if (action == ACTION_STOP) {
                context.stopService(intent)
            } else {
                // minSdk 26 起 startForegroundService() 无条件可用。
                context.startForegroundService(intent)
            }
        }
    }

    private val worker = Executors.newSingleThreadExecutor()

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 前台服务必须在 5 秒内 startForeground，否则系统直接 ANR 掉；Python 那边
        // 首次 bootstrap 要扫账号、拉目录，所以先把占位通知挂上再说。
        startForegroundSafe(getString(R.string.notif_starting))

        when (intent?.action) {
            ACTION_STOP -> {
                worker.execute { GatewayBridge.stop(applicationContext); active = false }
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_RESTART -> worker.execute {
                GatewayBridge.stop(applicationContext)
                launch()
            }
            else -> worker.execute { launch() }
        }
        return START_STICKY
    }

    private fun launch() {
        val context = applicationContext
        // 远程接入不托管任何本地进程：Go 网关跑在别处（电脑 / 服务器 / Termux），
        // 容器只负责显示面板与探活，前台服务不应该占着一条常驻通知。
        if (GatewayProfile.isRemote(context)) {
            active = false
            lastStatus = null
            postNotification(
                getString(R.string.notif_remote_title),
                getString(R.string.notif_remote_text,
                    GatewayProfile.baseUrl(context, Prefs.port(context))),
            )
            return
        }
        // 空壳状态下没什么可启动的：不弹「启动失败」，直接告诉用户下一步该做什么。
        // Go 本机模式是例外——二进制随包签名在 jniLibs 里，不需要任何导入的
        // 项目 ZIP 就能跑，不该被「还没有导入网关项目」挡住。
        if (!GatewayProfile.isLocalGo(context) && GatewayBridge.projectRoot(context) == null) {
            active = false
            postNotification(
                getString(R.string.notif_no_project_title),
                getString(R.string.notif_no_project),
            )
            return
        }
        val port = Prefs.port(context)
        val lan = Prefs.lan(context)
        val state = GatewayBridge.start(context, port, lan)
        lastStatus = state
        active = state.optBoolean("running", false)
        if (!active) {
            val reason = state.optString("error").ifBlank { getString(R.string.notif_failed) }
            postNotification(getString(R.string.notif_failed), reason)
            return
        }
        val accounts = state.optInt("accounts", 0)
        val usable = state.optInt("usable", 0)
        val local = "http://127.0.0.1:$port/v1"
        val detail = buildString {
            append(local)
            append('\n')
            append(getString(R.string.notif_accounts, usable, accounts))
            if (lan) {
                val ip = lanAddress()
                if (ip != null) {
                    append('\n')
                    append("LAN: http://$ip:$port/v1")
                }
            }
        }
        postNotification(getString(R.string.notif_running, port), detail)
    }

    override fun onDestroy() {
        active = false
        worker.execute { GatewayBridge.stop(applicationContext) }
        worker.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startForegroundSafe(text: String) {
        val notification = buildNotification(getString(R.string.notif_title), text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun postNotification(title: String, text: String) {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, buildNotification(title, text))
    }

    private fun buildNotification(title: String, text: String) =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_gateway)
            .setContentTitle(title)
            .setContentText(text.lineSequence().firstOrNull() ?: text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(openPanelIntent())
            .addAction(0, getString(R.string.action_stop), stopIntent())
            .build()

    private fun openPanelIntent(): PendingIntent = PendingIntent.getActivity(
        this, 0, Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun stopIntent(): PendingIntent = PendingIntent.getService(
        this, 1,
        Intent(this, GatewayService::class.java).setAction(ACTION_STOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun createChannel() {
        // minSdk 26 起通知渠道必定存在，不用再判断 SDK_INT。
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notif_channel),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.notif_channel_desc)
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    /** 局域网模式下通知里要给出可直接填进客户端的地址。 */
    private fun lanAddress(): String? = try {
        NetworkInterface.getNetworkInterfaces().asSequence()
            .filter { it.isUp && !it.isLoopback }
            .flatMap { it.inetAddresses.asSequence() }
            .filterIsInstance<Inet4Address>()
            .firstOrNull { it.isSiteLocalAddress }
            ?.hostAddress
    } catch (t: Throwable) {
        null
    }
}
