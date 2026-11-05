# release 打开 R8（debug 不混淆，这里只影响发布包）。
#
# 三条底线：
#   1. Chaquopy 的类由解释器在运行时按名字查找，整份保留；
#   2. 清单里的组件（HubApp / MainActivity / GatewayService）由系统按类名实例化，
#      不能被改名，也不能被裁掉成员（生命周期方法由框架回调）；
#   3. 外壳其余类只保名字，成员照常裁剪——出问题时堆栈仍然可读，
#      但没被引用到的代码该省还是省。

-keep class com.chaquo.python.** { *; }

-keep class com.workbuddy2api.hub.HubApp { *; }
-keep class com.workbuddy2api.hub.MainActivity { *; }
-keep class com.workbuddy2api.hub.GatewayService { *; }

# 让崩溃栈、日志里的类名与方法名保持原样（不参与改名，但仍可被裁剪）。
-keepnames class com.workbuddy2api.hub.**

# 网关是纯 Python（标准库），Python 侧不 import 任何 Java 类，
# 所以不需要为「Python 调用 Java」额外保留什么；这里只关掉随之而来的警告。
-dontwarn org.jetbrains.annotations.**
