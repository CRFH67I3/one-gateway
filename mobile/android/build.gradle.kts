// 移动端 Android 外壳的构建入口。
// 网关本体是纯标准库 Python（仓库根目录的 wb_*.py + dashboard.html），
// 这里只负责用 Chaquopy 把 CPython 打进去，外壳用 Kotlin 写。
//
// AGP 9 起 Kotlin 编译是内建的（built-in Kotlin）：不再声明
// `org.jetbrains.kotlin.android`，编译器由 AGP 自带的 KGP 提供，再显式加一遍会直接
// 报 "The 'org.jetbrains.kotlin.android' plugin is no longer required ... since AGP 9.0"。
// Kotlin 版本因此由 AGP 的运行时依赖决定（9.4 的底线是 KGP 2.2.10）；要钉更高版本
// 得走 buildscript classpath，而不是重新加插件。
plugins {
    id("com.android.application") version "9.4.1" apply false
    id("com.chaquo.python") version "17.0.0" apply false
}
