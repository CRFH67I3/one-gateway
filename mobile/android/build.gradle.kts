// 移动端 Android 外壳的构建入口。
// 网关本体是纯标准库 Python（仓库根目录的 wb_*.py + dashboard.html），
// 这里只负责用 Chaquopy 把 CPython 打进去，外壳用 Kotlin 写。
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "1.9.25" apply false
    id("com.chaquo.python") version "16.1.0" apply false
}
