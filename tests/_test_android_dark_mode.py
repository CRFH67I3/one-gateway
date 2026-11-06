"""Android 面板里「跟随系统」必须真的跟着手机的系统深浅色。

真机现象：手机切成深色模式，面板菜单里选的是「跟随系统」，面板却一直是浅色。

根因不在 `resolve()`——它读 `prefers-color-scheme` 的那两行本身没错。问题在
**Android WebView 的这个媒体查询跟的是 App 主题，不是系统设置**：外壳是
`Theme.Material.Light`，手机深色时它照样报 `light`，于是「跟随系统」永远解析成
浅色。修法要三层一起改，少一层就退回原样，所以这里逐层钉住：

  1. 外壳（Kotlin）：读 `uiMode` 判断系统深浅，把结果拼进面板地址 `?sysdark=1|0`；
     系统切换会重建 Activity，地址因此重新取值，不需要额外监听。
  2. 页面（dashboard.html）：首帧脚本认这个参数并**缓存**（面板里有几处
     `history.replaceState` 会重写 URL，每次现读会把参数弄丢），媒体查询降级为兜底；
     另外认 `wb:system-theme` 事件，供原生运行中推送。
  3. 原生外壳那一圈（values-night 资源）：状态栏、顶栏、WebView 底色一起沉下去，
     否则深色面板顶上会顶一条白。

Run: python tests/_test_android_dark_mode.py
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(ROOT, "mobile", "android")
JAVA = os.path.join(ANDROID, "app", "src", "main", "java", "com", "workbuddy2api", "hub")
RES = os.path.join(ANDROID, "app", "src", "main", "res")
DASHBOARD = os.path.join(ROOT, "dashboard.html")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class ShellPassesSystemDark(unittest.TestCase):
    """第 1 层：Kotlin 外壳读系统深浅色并带给页面。"""

    @classmethod
    def setUpClass(cls):
        cls.src = read(os.path.join(JAVA, "MainActivity.kt"))

    def test_reads_ui_night_mode(self):
        self.assertIn("Configuration.UI_MODE_NIGHT_MASK", self.src,
                      "外壳必须读 uiMode 判断系统深浅，别指望 WebView 的媒体查询")
        self.assertIn("Configuration.UI_MODE_NIGHT_YES", self.src)

    def test_panel_url_carries_sysdark(self):
        body = re.search(r"private fun panelUrl\(port: Int\): String =(.*?)(?:\n\n|\Z)", self.src, re.S)
        self.assertIsNotNone(body, "找不到 panelUrl()，面板地址的拼装方式变了")
        text = body.group(1)
        self.assertIn("?sysdark=", text, "面板地址必须带 ?sysdark=1|0，页面才有系统值可读")
        self.assertIn("systemDark()", text, "sysdark 的值要来自系统深浅，不能写死")

    def test_render_loads_that_url(self):
        self.assertIn("val target = panelUrl(port)", self.src,
                      "render() 加载面板时要走 panelUrl()，否则参数根本发不出去")
        self.assertNotIn('val target = "http://127.0.0.1:$port/"', self.src,
                         "旧的裸地址会让面板拿不到系统深浅色")

    def test_webview_background_follows_system(self):
        self.assertRegex(self.src, r"setBackgroundColor\(if \(systemDark\(\)\)",
                         "WebView 首帧底色要跟着系统走，深色下不能先闪一块白")
        self.assertIn("COLOR_WEB_BG_DARK", self.src)

    def test_activity_is_recreated_on_night_change(self):
        manifest = read(os.path.join(ANDROID, "app", "src", "main", "AndroidManifest.xml"))
        activity = re.search(r"<activity[^>]*MainActivity[^>]*>", manifest, re.S)
        self.assertIsNotNone(activity)
        self.assertNotIn("uiMode", activity.group(0),
                         "configChanges 里不能有 uiMode：要让它重建 Activity，"
                         "否则面板地址不会重新取系统值，切深色后界面停在旧主题")


class PanelHonoursNativeValue(unittest.TestCase):
    """第 2 层：页面认外壳给的值，媒体查询只是兜底。"""

    @classmethod
    def setUpClass(cls):
        cls.html = read(DASHBOARD)

    def test_reads_sysdark_from_url(self):
        self.assertIn("get('sysdark')", self.html, "页面脚本要读 ?sysdark=")
        self.assertRegex(self.html, r"var NATIVE_DARK = \(function\(\)",
                         "参数要在脚本开头读一次并缓存：replaceState 会重写 URL")

    def test_resolve_prefers_native(self):
        self.assertIn("function systemPrefersDark()", self.html)
        self.assertIn("return systemPrefersDark() ? 'dark' : 'light';", self.html,
                      "resolve('system') 要经过 systemPrefersDark()，不能直接读 mq.matches")

    def test_media_query_still_fallback(self):
        self.assertIn("return !!(mq && mq.matches);", self.html,
                      "浏览器/桌面面板没有 sysdark 参数，必须继续回落到媒体查询")

    def test_live_event(self):
        self.assertIn("wb:system-theme", self.html,
                      "原生运行中切换系统深浅时用 wb:system-theme 推送，页面要接")


class NativeShellFollowsSystem(unittest.TestCase):
    """第 3 层：原生那一圈（状态栏 / 顶栏 / 导航栏）也跟着沉下去。"""

    @classmethod
    def setUpClass(cls):
        cls.light_colors = read(os.path.join(RES, "values", "colors.xml"))
        cls.night_colors = read(os.path.join(RES, "values-night", "colors.xml"))
        cls.light_theme = read(os.path.join(RES, "values", "themes.xml"))
        cls.night_theme = read(os.path.join(RES, "values-night", "themes.xml"))
        cls.layout = read(os.path.join(RES, "layout", "activity_main.xml"))

    @staticmethod
    def color_names(xml):
        return set(re.findall(r'<color name="([^"]+)"', xml))

    def test_night_palette_covers_every_shell_color(self):
        light = self.color_names(self.light_colors)
        night = self.color_names(self.night_colors)
        # 布局与主题里引用到的颜色，深色档都得有一份，否则夜间会漏出浅色块。
        used = set(re.findall(r"@color/(\w+)", self.layout + self.light_theme))
        used.discard("colorPrimary")
        missing = sorted(used - night)
        self.assertEqual(missing, [], "这些颜色在 values-night 里缺失，深色下会露出浅色：%s" % missing)
        self.assertTrue(night <= light, "values-night 只覆盖已有颜色名，不能凭空新增（%s）"
                        % sorted(night - light))

    def test_night_theme_is_dark(self):
        self.assertNotIn("Theme.Material.Light", self.night_theme,
                         "深色档不能继承 Light 主题")
        self.assertIn('name="android:windowLightStatusBar">false', self.night_theme,
                      "深色底上状态栏图标要转成浅色，否则看不见")
        self.assertIn("@color/header_bg", self.night_theme)

    def test_light_theme_unchanged(self):
        self.assertIn("Theme.Material.Light", self.light_theme)
        self.assertIn('name="android:windowLightStatusBar">true', self.light_theme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
