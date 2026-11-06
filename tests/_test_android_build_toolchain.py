"""Android 构建工具链的口径：compileSdk 追最新平台，AGP / Gradle / Chaquopy 一路对齐。

`compileSdk` 不是随便填的数字：它只能 ≤ 当前 AGP 声称支持的最高 API 级别。追到最新平台
（当前 Android 17 / API 37）就必须同时把 AGP 抬到能认它的版本（9.2 起支持 37），
AGP 又反过来要求最低 Gradle 版本（9.4 → Gradle 9.6），Chaquopy 也得跟着换支持 AGP 9 的
大版本（17.x）。这四者任何一处单独动都会在配置阶段或打包阶段炸，所以在这里一次钉全：

  1. `compileSdk` = 37，且 AGP 版本足够新（9.2+），别出现「平台升了、AGP 没升」；
  2. AGP 9 的两处必改项：**不再声明** `org.jetbrains.kotlin.android`（内建 Kotlin），
     以及 `defaultConfig.resValue` 需要的 `buildFeatures { resValues = true }`；
  3. CI 用的 Gradle 版本 ≥ AGP 的最低要求（9.4.1 → 9.6.0），否则 CI 上直接配置失败；
  4. Chaquopy 是 17.x（16.x 只支持到 AGP 8.13）。

Run: python tests/_test_android_build_toolchain.py
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(ROOT, "mobile", "android")
ROOT_GRADLE = os.path.join(ANDROID, "build.gradle.kts")
APP_GRADLE = os.path.join(ANDROID, "app", "build.gradle.kts")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "mobile-android.yml")
README = os.path.join(ROOT, "mobile", "README.md")

# AGP 9.2 起才支持 API 37；9.4 要求 Gradle ≥ 9.6、build-tools 36。
MIN_AGP_FOR_API_37 = (9, 2)
MIN_GRADLE_FOR_AGP_94 = (9, 6)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def version_tuple(text):
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def plugin_version(source, plugin_id):
    """取出 `id("x") version "1.2.3"` 里的版本号。"""
    match = re.search(r'id\("%s"\)\s*version\s*"([^"]+)"' % re.escape(plugin_id), source)
    return version_tuple(match.group(1)) if match else None


def sdk_of(source, name):
    match = re.search(r"\b%s\s*=\s*(\d+)" % name, source)
    return int(match.group(1)) if match else None


def strip_comments(source):
    """去掉 `//` 行注释，只留真正会被 Gradle 执行的内容。

    断言「某个声明已经删掉」时不能连注释一起匹配：解释「为什么不再声明 kotlin-android」
    的注释本身就写着那个插件名，直接拿原文断言会把好注释判成违规。
    """
    return "\n".join(re.sub(r"//.*$", "", line) for line in source.splitlines())


class CompileSdkTracksLatestPlatform(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = read(ROOT_GRADLE)
        cls.app = read(APP_GRADLE)
        cls.agp = plugin_version(cls.root, "com.android.application")

    def test_agp_is_declared(self):
        self.assertIsNotNone(self.agp, "根 build.gradle.kts 里找不到 AGP 版本")

    def test_compile_sdk_is_37(self):
        value = sdk_of(self.app, "compileSdk")
        self.assertEqual(value, 37,
                         "compileSdk 应跟随最新平台（Android 17 / API 37），实为 %s" % value)

    def test_agp_supports_that_platform(self):
        self.assertGreaterEqual(
            self.agp[:2], MIN_AGP_FOR_API_37,
            "AGP %s 认不了 API 37：compileSdk 会报 unsupported，至少要 %s.x"
            % (".".join(map(str, self.agp)), ".".join(map(str, MIN_AGP_FOR_API_37))))

    def test_target_sdk_stays_deliberate(self):
        # compileSdk 追最新只影响编译期可见 API；targetSdk 会改变运行时行为
        # （权限、后台限制、通知…），必须显式给值，不能靠 AGP 默认。
        self.assertEqual(sdk_of(self.app, "targetSdk"), 34,
                         "targetSdk 要显式写死；AGP 9 在缺省时会自动取 compileSdk")


class BuiltInKotlinMigration(unittest.TestCase):
    """AGP 9 的内建 Kotlin：插件要去掉，resValues 要显式打开。"""

    @classmethod
    def setUpClass(cls):
        cls.root = strip_comments(read(ROOT_GRADLE))
        cls.app = strip_comments(read(APP_GRADLE))

    def test_kotlin_android_plugin_removed(self):
        for name, text in (("根", self.root), ("app", self.app)):
            self.assertNotIn("org.jetbrains.kotlin.android", text,
                             "%s build.gradle.kts 仍声明 kotlin-android 插件：AGP 9 已内建 "
                             "Kotlin，再声明会在配置阶段直接失败" % name)

    def test_kotlin_options_dsl_removed(self):
        self.assertNotIn("kotlinOptions", self.app,
                         "android.kotlinOptions 在内建 Kotlin 下已删除，改用 "
                         "kotlin { compilerOptions { } }（jvmTarget 默认对齐 targetCompatibility）")

    def test_res_values_enabled(self):
        features = re.search(r"buildFeatures\s*\{(.*?)\n    \}", self.app, re.S)
        self.assertIsNotNone(features, "找不到 buildFeatures 块")
        self.assertRegex(features.group(1), r"resValues\s*=\s*true",
                         "defaultConfig.resValue(app_version_name) 需要 resValues = true，"
                         "AGP 9 默认关闭该开关")

    def test_build_config_still_enabled(self):
        features = re.search(r"buildFeatures\s*\{(.*?)\n    \}", self.app, re.S)
        self.assertRegex(features.group(1), r"buildConfig\s*=\s*true",
                         "外壳要靠 BuildConfig.VERSION_NAME 判断包内核心是否要重解压")


class GradleAndChaquopyMatchAgp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = read(ROOT_GRADLE)
        cls.workflow = read(WORKFLOW)

    def test_ci_gradle_new_enough_for_agp(self):
        match = re.search(r'gradle-version:\s*"([^"]+)"', self.workflow)
        self.assertIsNotNone(match, "CI 里找不到 gradle-version")
        version = version_tuple(match.group(1))
        self.assertGreaterEqual(
            version, MIN_GRADLE_FOR_AGP_94,
            "CI 的 Gradle %s 低于 AGP 9.4 的最低要求 %s"
            % (match.group(1), ".".join(map(str, MIN_GRADLE_FOR_AGP_94))))

    def test_chaquopy_17(self):
        version = plugin_version(self.root, "com.chaquo.python")
        self.assertIsNotNone(version, "找不到 Chaquopy 版本")
        self.assertGreaterEqual(version[0], 17,
                                "Chaquopy %s 只支持到 AGP 8.13，AGP 9 需要 17.x"
                                % ".".join(map(str, version)))

    def test_docs_describe_the_toolchain(self):
        text = read(README)
        for needle in ("compileSdk = 37", "AGP 9.4.1", "Gradle 9.6", "platform 37"):
            self.assertIn(needle, text, "构建文档要写明 %s，否则升级时没人知道基线" % needle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
