"""常驻通知发不出去时，网关会「显示运行中却不工作」。

真机现象：装好 App、网关正常起来，锁屏放一段时间后打开浏览器访问端口，
连接能建立、`/health` 却一直不返回；App 顶栏仍写着「运行中」。`ps` 里进程
状态是 `D`（被冻结），socket 停在 `CLOSE_WAIT`。

根因是 Android 13 把通知改成了运行时权限：清单里声明 `POST_NOTIFICATIONS`
只是「允许申请」，不主动弹窗就等于用户拒绝。`startForeground()` 那张常驻
通知被静默丢弃后，服务名义上还是前台，MIUI 这类 ROM 会按普通后台应用处理
并冻结进程——端口还在监听，请求却没人处理。

这里钉三件事，少一件都会退回「后台失联」：

  1. 清单声明了 `POST_NOTIFICATIONS`（否则 `requestPermissions` 直接失败）；
  2. `MainActivity` 在 TIRAMISU+ 上真的去要，且已授权时不再打扰；
  3. 菜单里有「后台保活设置」，能把用户送到系统的应用详情页去开自启动 /
     省电策略——各 ROM 叫法不同，App 只能送到门口。

Run: python tests/_test_android_keepalive.py
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(ROOT, "mobile", "android")
JAVA = os.path.join(ANDROID, "app", "src", "main", "java", "com", "onegateway", "host")
RES = os.path.join(ANDROID, "app", "src", "main", "res")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class NotificationPermissionIsRequested(unittest.TestCase):
    """第 1、2 层：清单放行 + 运行时主动要。"""

    @classmethod
    def setUpClass(cls):
        cls.manifest = read(os.path.join(ANDROID, "app", "src", "main", "AndroidManifest.xml"))
        cls.src = read(os.path.join(JAVA, "MainActivity.kt"))

    def test_manifest_declares_post_notifications(self):
        self.assertIn("android.permission.POST_NOTIFICATIONS", self.manifest,
                      "不声明的话 requestPermissions() 会被系统直接拒掉")

    def test_asks_only_on_tiramisu_and_up(self):
        body = re.search(r"private fun requestNotificationPermissionIfNeeded\(\)\s*\{(.*?)\n    \}",
                         self.src, re.S)
        self.assertIsNotNone(body, "找不到 requestNotificationPermissionIfNeeded()")
        text = body.group(1)
        self.assertIn("Build.VERSION_CODES.TIRAMISU", text,
                      "POST_NOTIFICATIONS 是 API 33 才有的，低版本要直接返回")
        self.assertIn("POST_NOTIFICATIONS", text)

    def test_skips_when_already_granted(self):
        body = re.search(r"private fun requestNotificationPermissionIfNeeded\(\)\s*\{(.*?)\n    \}",
                         self.src, re.S).group(1)
        self.assertIn("PERMISSION_GRANTED", body,
                      "已授权还弹窗会被用户当成骚扰，也让首屏多一次无意义的点击")
        self.assertRegex(body, r"if \(granted\) return",
                         "授权后要在弹窗前就返回")

    def test_called_from_oncreate(self):
        body = re.search(r"override fun onCreate\(.*?\n    \}", self.src, re.S)
        self.assertIsNotNone(body, "找不到 onCreate")
        self.assertIn("requestNotificationPermissionIfNeeded()", body.group(0),
                      "只在方法里定义不调用，通知照样是拒的")


class KeepAliveEntryExists(unittest.TestCase):
    """第 3 层：把用户送到能开自启动 / 省电策略的地方。"""

    @classmethod
    def setUpClass(cls):
        cls.src = read(os.path.join(JAVA, "MainActivity.kt"))
        cls.strings = read(os.path.join(RES, "values", "strings.xml"))

    def test_menu_entry_declared(self):
        self.assertIn("MENU_KEEPALIVE", self.src, "菜单里没有保活入口常量")
        self.assertIn("R.string.menu_keepalive", self.src, "菜单项没有挂到文案上")
        self.assertIn('name="menu_keepalive"', self.strings)

    def test_entry_opens_app_details(self):
        body = re.search(r"private fun openAppDetails\(\)\s*\{(.*?)\n    \}", self.src, re.S)
        self.assertIsNotNone(body, "没有 openAppDetails()，点了菜单没地方去")
        text = body.group(1)
        self.assertIn("ACTION_APPLICATION_DETAILS_SETTINGS", text,
                      "各家 ROM 的自启动/省电策略都挂在应用详情页，只能跳这里")
        self.assertIn("packageName", text, "要带上自己的包名，否则打开的是应用列表")


if __name__ == "__main__":
    unittest.main(verbosity=2)
