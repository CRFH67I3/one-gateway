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

        // Python 解释器是原生组件，必须显式声明 ABI。
        // 3.12+ 不再提供 armeabi-v7a/x86 的构建，保留 64 位两种即可覆盖真机与模拟器。
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
        resValue("string", "app_version_name", serverVersion)
    }

    // 仓库里不放签名材料。CI 或本地提供了 keystore 就用它出正式包；
    // 没提供时退回 debug 签名，保证产物可安装（便于 sideload 测试）。
    val keystorePath = System.getenv("WB_ANDROID_KEYSTORE")
    signingConfigs {
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
        release {
            isMinifyEnabled = false
            isShrinkResources = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = if (!keystorePath.isNullOrBlank()) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
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
