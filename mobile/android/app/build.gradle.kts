import java.io.File

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
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

android {
    namespace = "com.workbuddy2api.hub"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.workbuddy2api.hub"
        minSdk = 24
        targetSdk = 34
        versionCode = versionCodeValue
        versionName = serverVersion
        resValue("string", "app_version_name", serverVersion)
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
            // 后缀进 versionName（关于对话框会显示成 1.10.4-arm64），
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
            // 有固定 keystore 就用它，让每次构建的调试包签名一致。
            if (!debugKeystorePath.isNullOrBlank()) {
                signingConfig = signingConfigs.getByName("fixedDebug")
            }
        }
        release {
            isMinifyEnabled = false
            isShrinkResources = false
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
    // 「包内核心是否需要重新解压」，所以显式打开。
    buildFeatures {
        buildConfig = true
    }
    kotlinOptions {
        jvmTarget = "17"
    }
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

// 把仓库根目录的网关核心（只读、单一来源）同步进 APK 的 assets/core。
// 运行期由外壳解压到 filesDir，再交给 Python 侧同步到可写数据目录。
val syncGatewayCore = tasks.register<Sync>("syncGatewayCore") {
    from(repoRoot) {
        include("wb_*.py")
        include("dashboard.html")
    }
    from(File(repoRoot, "mobile/shared")) {
        include("wb_mobile_runtime.py")
    }
    into(layout.projectDirectory.dir("src/main/assets/core"))
}

tasks.matching { it.name == "preBuild" }.configureEach {
    dependsOn(syncGatewayCore)
}
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach {
    dependsOn(syncGatewayCore)
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
