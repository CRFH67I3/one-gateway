import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// 版本号只有一个来源：仓库根目录 wb_proxy.py 的 server_version。
// PC 版发版时改那一处，APK 的 versionName/versionCode 自动跟随，不会漂移。
val repoRoot: File = rootProject.projectDir.parentFile.parentFile
val serverVersion: String = run {
    val source = File(repoRoot, "wb_proxy.py")
    val pattern = Regex("""server_version\s*=\s*"wb-proxy/([0-9]+(?:\.[0-9]+)*)"""")
    pattern.find(source.readText())?.groupValues?.get(1) ?: "0.0.0"
}
val versionCodeValue: Int = run {
    val parts = serverVersion.split(".").map { it.toIntOrNull() ?: 0 }
    val padded = parts + List(3 - parts.size.coerceAtMost(3)) { 0 }
    padded[0] * 10000 + padded[1] * 100 + padded[2]
}

// 版本名的最后一段用构建日期（8 位 yyyyMMdd），前两段仍跟 wb_proxy.py。
// 好处是一眼能看出这个包是哪天出的，跨天自然递增；坏处是无法手写版本名，
// 但这本来就是构建产物的标识，不该手工维护。
// versionCode 不跟着日期走：它仍由 server_version 算，覆盖安装不吃亏，
// 也不会因为同一天多次构建而产生歧义。
val buildDate: String = SimpleDateFormat("yyyyMMdd", Locale.US).format(Date())
val versionNameValue: String = run {
    val parts = serverVersion.split(".")
    if (parts.size >= 2) "${parts[0]}.${parts[1]}.$buildDate" else "$serverVersion.$buildDate"
}

android {
    namespace = "com.proxygateway.runtime"
    // 跟着本机装的最高一档平台走（当前是 Android 17 / API 37）。compileSdk 只影响
    // 「编译器能看到哪些新 API」，运行时行为由 targetSdk 决定，所以可以放心追最新；
    // 但 AGP 对它有上限，升级平台时一起升 AGP（9.4 支持到 API 37）。
    compileSdk = 37

    defaultConfig {
        applicationId = "com.proxygateway.runtime"
        // Android 8.0（API 26）。外壳离不开的两件事都在这一档变成无条件可用：
        // 通知渠道（Channel/IMPORTANCE_LOW）与 startForegroundService()。
        // 停在 24/25 就得为它们各写一套旧路径，而 8.0 以下机型跑不动
        // Chaquopy 的 Python 3.13 + 现代 WebView，实测收益为零，索性把底线抬平。
        minSdk = 26
        targetSdk = 34
        versionCode = versionCodeValue
        versionName = versionNameValue
        resValue("string", "app_version_name", versionNameValue)
    }

    // 每个 ABI 单独出包（真机 arm64-v8a / 模拟器 x86_64），包体从 35MB 降到 23MB 左右。
    //
    // 为什么不用 splits { abi { } } 或 App Bundle：Chaquopy 把解释器和标准库按 ABI 拆成
    // 三套独立资产（libpython3.13.so、assets/chaquopy/stdlib-<abi>.imy、
    // assets/chaquopy/bootstrap-native/<abi>/），splits 只过滤 lib/ 目录，assets 里另一套
    // ABI 的副本照样留在每个包里，省不掉多少。官方 FAQ 也明确说这两项 "won't help much"，
    // 推荐用 flavor dimension 逐个声明 abiFilters。
    //
    // 还有一处必须注意：ndk.abiFilters 在 defaultConfig 与 flavor 之间是取并集，
    // 所以它只能写在 flavor 里，defaultConfig 必须留空——否则两个 flavor 都会拿到两个 ABI，
    // 拆包就白做了。
    flavorDimensions += "abi"
    productFlavors {
        create("arm64") {
            dimension = "abi"
            // 后缀进 versionName（关于对话框会显示成 1.10.7-arm64），
            // 装上之后能一眼确认手里这个包是给真机还是给模拟器的。
            versionNameSuffix = "-arm64"
            ndk { abiFilters += "arm64-v8a" }
        }
        create("x64") {
            dimension = "abi"
            versionNameSuffix = "-x64"
            ndk { abiFilters += "x86_64" }
        }
    }

    // 仓库里不放签名材料。CI 或本地提供了 keystore 就用它出正式包；
    // 没提供时退回 debug 签名，保证产物可安装（便于 sideload 测试）。
    val keystorePath = System.getenv("WB_ANDROID_KEYSTORE")
    // 固定 debug 签名：CI 上把 keystore 路径直接给过来，别依赖 AGP 从
    // ~/.android 隐式找。runner 的 ANDROID_SDK_HOME / ANDROID_USER_HOME 与
    // $HOME 不一定一致，写在 $HOME/.android 下会被忽略，AGP 转而现场生成一对
    // 随机密钥——每次构建证书都不同，覆盖安装时报
    // INSTALL_FAILED_UPDATE_INCOMPATIBLE。
    // 用独立的 fixedDebug 而不是改内置 debug：内置配置由 AGP 动态创建，
    // 在 signingConfigs 块里直接改它依赖 AGP 的创建时机。
    val debugKeystorePath = System.getenv("WB_ANDROID_DEBUG_KEYSTORE")
    signingConfigs {
        if (!debugKeystorePath.isNullOrBlank()) {
            create("fixedDebug") {
                storeFile = file(debugKeystorePath)
                // AGP 约定的 debug 口令/别名，允许用环境变量覆盖。
                storePassword = System.getenv("WB_ANDROID_KEYSTORE_PASSWORD") ?: "android"
                keyAlias = System.getenv("WB_ANDROID_KEY_ALIAS") ?: "androiddebugkey"
                keyPassword = System.getenv("WB_ANDROID_KEY_PASSWORD") ?: "android"
            }
        }
        if (!keystorePath.isNullOrBlank()) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("WB_ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("WB_ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("WB_ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            // 调试包走「全量构建」：不混淆、不裁代码、不裁资源。
            // 排查问题时堆栈里的类名/行号、反编译出来的 R 资源都能直接对上，
            // 代价只是包体大一些——调试包不需要省体积。
            isMinifyEnabled = false
            isShrinkResources = false
            isDebuggable = true
            // 有固定 keystore 就用它，让每次构建的调试包签名一致。
            if (!debugKeystorePath.isNullOrBlank()) {
                signingConfig = signingConfigs.getByName("fixedDebug")
            }
        }
        release {
            // 发布包走「最小化构建」：R8 裁掉未使用的代码与成员、资源裁剪去掉
            // 没用到的资源，并做名字压缩。保留规则见 proguard-rules.pro——
            // Chaquopy 的类要整份保留，清单里的组件按名字实例化也不能改名。
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = when {
                !keystorePath.isNullOrBlank() -> signingConfigs.getByName("release")
                !debugKeystorePath.isNullOrBlank() -> signingConfigs.getByName("fixedDebug")
                else -> signingConfigs.getByName("debug")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    // AGP 8 起 BuildConfig 默认不生成；外壳要用 VERSION_NAME/VERSION_CODE 判断
    // 「包内核心是否需要重新解压」，所以显式打开（9.4 里同名开关仍然存在）。
    //
    // resValues 是 AGP 9 新拆出来的开关：`defaultConfig.resValue(...)` 生成
    // app_version_name 属于「自定义资源值」，默认关掉，打开它这一行不能省。
    buildFeatures {
        buildConfig = true
        resValues = true
    }
    // AGP 9 的内建 Kotlin 把 jvmTarget 默认对齐到 targetCompatibility（上面是 17），
    // 所以不再需要 android.kotlinOptions——那个 DSL 在内建 Kotlin 下已删除。
    packaging {
        resources.excludes += setOf(
            "META-INF/LICENSE*",
            "META-INF/NOTICE*",
            "META-INF/*.kotlin_module",
        )
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        // 不需要 pip 依赖：网关是纯标准库实现，HTTPS 根证书走 Android 自己的
        // /system/etc/security/cacerts（OpenSSL 的 SSL_CERT_DIR 认这个按
        // subject-hash 命名的目录），比塞一份会过期的 certifi 更贴合平台。
    }
}

// 容器模式：APK 只带 Python 解释器 + 薄运行时（src/main/python/wb_hub_env.py），
// 不再打包网关源码（wb_*.py / dashboard.html）。网关本体由用户在 App 内导入
// 仓库 ZIP 提供——升级网关不需要重装 APK，App 也因此不随网关版本变化而膨胀。
//
// 版本号仍只有一个来源：仓库根目录 wb_proxy.py 的 server_version，方便面板和
// App 对上号（用户导入的 ZIP 版本可能比 App 新，这是预期行为）。

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
