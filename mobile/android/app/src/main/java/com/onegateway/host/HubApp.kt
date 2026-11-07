package com.onegateway.host

import android.app.Application

/**
 * 进程入口。
 *
 * CPython 的初始化放在 Application 里：前台服务与 Activity 谁先起来都能直接用，
 * 避免「先点通知进 Activity 结果 Python 还没初始化」这类竞态。
 */
class HubApp : Application() {
    override fun onCreate() {
        super.onCreate()
        GatewayBridge.ensurePython(this)
    }
}
