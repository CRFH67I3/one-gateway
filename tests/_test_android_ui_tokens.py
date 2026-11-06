"""外壳的按钮只有一套写法，配色和面板是同一套 token。

真机上出现过的一幕：首装界面里「手动更新」是系统默认的灰块、「自动更新」是一行
裸文字，一个页面两种控件，看着像两个人各写一半拼起来的。根因是两处各写各的：

  - `overlayAction` 没有任何 style，落回 `Widget.Material.Button` 的默认外观
    （灰底 + `backgroundTint=colorButtonNormal`，还会随主题变色）；
  - `overlayAutoImport` 用 `borderlessButtonStyle` + 单独指定的 13sp 和次要色。

现在两个按钮都从 `Hub.Button.Primary` / `Hub.Button.Secondary` 取外观，几何
（高度、最小宽度、圆角、字号、左右留白）只在 `Hub.Button` 里定义一次。

配色同理。面板有两套主题：旧的 `:root`（暖米 + 陶土）和新界面
`html[data-ui="next"]`（冷灰 + 靛蓝）。**手机打开面板看到的是新界面那套**，所以
外壳对齐的是 next 的 token，不是 `:root`：

    外壳                      next 浅色    next 深色
    @color/page_bg            --bg         --bg
    @color/header_bg          --panel      --panel
    @color/text_primary       --fg         --fg
    @color/text_secondary     --dim        --dim
    @color/colorPrimary       --accent     --

两边各写一套颜色，同一屏上就是「两个 App 拼起来」。面板换肤时这组测试会红。

Run: python tests/_test_android_ui_tokens.py
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(ROOT, "mobile", "android")
RES = os.path.join(ANDROID, "app", "src", "main", "res")
JAVA = os.path.join(ANDROID, "app", "src", "main", "java", "com", "workbuddy2api", "hub")
DASHBOARD = os.path.join(ROOT, "dashboard.html")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def colors_of(path):
    """`<color name="x">#RRGGBB</color>` → {x: '#RRGGBB'}（统一大写）。"""
    source = read(path)
    return {
        m.group(1): m.group(2).upper()
        for m in re.finditer(r'<color name="([^"]+)">\s*(#[0-9A-Fa-f]{6,8})\s*</color>', source)
    }


def style_body(xml, name):
    match = re.search(
        r'<style name="%s"(?:\s+parent="[^"]*")?\s*>(.*?)</style>' % re.escape(name),
        xml, re.S)
    return match.group(1) if match else None


def panel_tokens():
    """面板新界面（`html[data-ui="next"]`）两套主题的 CSS 变量。

    手机打开面板渲染的是这一套；`:root` 那套只在旧界面下生效，拿它当基准会让外壳
    跟真机上看到的颜色对不上（暖米 + 陶土 vs 冷灰 + 靛蓝）。两块的结尾都是单独
    一行 `}`，块内不再嵌套花括号，所以非贪婪到第一个 `\\n}` 即可。
    """
    html = read(DASHBOARD)
    light = re.search(r'html\[data-ui="next"\]\{(.*?)\n\}', html, re.S)
    dark = re.search(r'html\[data-ui="next"\]\[data-theme="dark"\]\{(.*?)\n\}', html, re.S)
    if not light or not dark:
        raise AssertionError(
            "面板里找不到 html[data-ui=\"next\"] 的变量块——新界面改名了？"
            "外壳配色是照着它对齐的，改名后要一起改")
    return light.group(1), dark.group(1)


def token(block, name):
    match = re.search(r"--%s:\s*(#[0-9A-Fa-f]{6})" % re.escape(name), block)
    if not match:
        raise AssertionError("面板变量 --%s 找不到，别在断言里写死颜色" % name)
    return match.group(1).upper()


class ButtonsShareOneComponent(unittest.TestCase):
    """两个按钮必须长得像同一个组件里的两个变体。"""

    @classmethod
    def setUpClass(cls):
        cls.layout = read(os.path.join(RES, "layout", "activity_main.xml"))
        cls.styles = read(os.path.join(RES, "values", "styles.xml"))
        cls.dimens = read(os.path.join(RES, "values", "dimens.xml"))

    def button_tag(self, view_id):
        match = re.search(r"<Button\b[^>]*@\+id/%s[^>]*/>" % view_id, self.layout, re.S)
        self.assertIsNotNone(match, "布局里找不到按钮 %s" % view_id)
        return match.group(0)

    def test_both_buttons_use_the_shared_styles(self):
        self.assertIn('style="@style/Hub.Button.Primary"', self.button_tag("overlayAction"))
        self.assertIn('style="@style/Hub.Button.Secondary"', self.button_tag("overlayAutoImport"))

    def test_layout_does_not_restyle_buttons(self):
        """外观不许在布局里各写一份——灰块与裸文字就是这么分叉出来的。"""
        for view_id in ("overlayAction", "overlayAutoImport"):
            tag = self.button_tag(view_id)
            for attr in ("android:textSize", "android:textColor", "android:background",
                         "android:layout_height", "android:minWidth"):
                self.assertNotIn(attr, tag,
                                 "%s 又在外观上单独写了 %s，应该只从 Hub.Button 取"
                                 % (view_id, attr))

    def test_variants_only_differ_in_fill(self):
        base = style_body(self.styles, "Hub.Button")
        self.assertIsNotNone(base, "styles.xml 里没有 Hub.Button")
        # 几何只写一次
        for attr in ("android:layout_height", "android:minWidth", "android:textSize",
                     "android:paddingStart", "android:paddingEnd"):
            self.assertIn(attr, base, "Hub.Button 要定义 %s，让两个变体共用" % attr)
        self.assertIn("@dimen/button_height", base)
        self.assertIn("@dimen/button_min_width", base)

        for name, drawable in (("Hub.Button.Primary", "@drawable/bg_btn_primary"),
                               ("Hub.Button.Secondary", "@drawable/bg_btn_secondary")):
            body = style_body(self.styles, name)
            self.assertIsNotNone(body, "styles.xml 里没有 %s" % name)
            self.assertIn(drawable, body, "%s 要用统一的按钮底：%s" % (name, drawable))
            for attr in ("android:textSize", "android:layout_height", "android:minWidth"):
                self.assertNotIn(attr, body,
                                 "%s 不许另写 %s：两个变体只能差填充方式" % (name, attr))

    def test_platform_tint_is_cleared(self):
        """Borderless 仍带着主题的 backgroundTint，不关掉会把自绘背景染成灰色。"""
        self.assertIn('android:backgroundTint">@null', self.styles,
                      "Hub.Button 必须清掉 backgroundTint，否则圆角背景会被染成系统灰")

    def test_geometry_comes_from_dimens(self):
        for name in ("button_height", "button_min_width", "button_radius", "button_gap"):
            self.assertIn('name="%s"' % name, self.dimens,
                          "按钮几何要集中放在 dimens.xml：%s" % name)
        for drawable in ("bg_btn_primary", "bg_btn_secondary"):
            body = read(os.path.join(RES, "drawable", drawable + ".xml"))
            self.assertIn("@dimen/button_radius", body,
                          "%s 的圆角要跟按钮统一取 @dimen/button_radius" % drawable)
            # 置灰态也得有背景，否则禁用时按钮会「消失」
            self.assertIn('android:state_enabled="false"', body,
                          "%s 缺少禁用态：导入过程中按钮会置灰" % drawable)


class ShellPaletteMatchesPanel(unittest.TestCase):
    """外壳的配色 = 面板的 token，换肤时两边一起改。"""

    MAPPING = {
        "page_bg": "bg",
        "header_bg": "panel",
        "text_primary": "fg",
        "text_secondary": "dim",
        "colorPrimary": "accent",
    }

    @classmethod
    def setUpClass(cls):
        cls.light = colors_of(os.path.join(RES, "values", "colors.xml"))
        cls.night = colors_of(os.path.join(RES, "values-night", "colors.xml"))
        cls.panel_light, cls.panel_dark = panel_tokens()

    def assert_palette(self, shell, panel, label):
        for color_name, css_var in self.MAPPING.items():
            if color_name not in shell:
                continue  # 深色档沿用浅色档的 accent 是有意的
            self.assertEqual(
                shell[color_name], token(panel, css_var),
                "%s 的 @color/%s 与面板 --%s 不一致：外壳和面板会呈现两套配色"
                % (label, color_name, css_var))

    def test_light_matches_panel(self):
        self.assert_palette(self.light, self.panel_light, "浅色档")

    def test_night_matches_panel_dark(self):
        self.assert_palette(self.night, self.panel_dark, "深色档")

    def test_night_covers_every_button_color(self):
        button_colors = {name for name in self.light if name.startswith("button_")}
        self.assertTrue(button_colors, "浅色档里没有按钮配色")
        self.assertEqual(sorted(button_colors - set(self.night)), [],
                         "这些按钮配色在 values-night 里缺失，深色下会露出浅色块")
        self.assertEqual(sorted(set(self.night) - set(self.light)), [],
                         "values-night 只覆盖已有颜色名，不能凭空新增")

    def test_webview_first_frame_uses_page_bg(self):
        """WebView 首帧底色必须等于 page_bg，否则面板第一帧前会闪一块别的颜色。"""
        source = read(os.path.join(JAVA, "MainActivity.kt"))
        light = re.search(r"COLOR_WEB_BG_LIGHT\s*=\s*0x([0-9A-Fa-f]{8})", source)
        dark = re.search(r"COLOR_WEB_BG_DARK\s*=\s*0x([0-9A-Fa-f]{8})", source)
        self.assertIsNotNone(light, "找不到 COLOR_WEB_BG_LIGHT")
        self.assertIsNotNone(dark, "找不到 COLOR_WEB_BG_DARK")
        self.assertEqual("#" + light.group(1)[2:].upper(), self.light["page_bg"])
        self.assertEqual("#" + dark.group(1)[2:].upper(), self.night["page_bg"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
