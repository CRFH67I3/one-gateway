"""安卓最低支持版本钉在 Android 8.0（API 26）。

外壳有两处地基是 8.0 才无条件可用的：常驻通知的**通知渠道**
（`NotificationChannel` / `IMPORTANCE_LOW`）和**前台服务启动**
（`startForegroundService()`）。minSdk = 24 时这两处都得写「新 API + 旧路径」的分叉，
而 24/25 的机器跑不动 Chaquopy 的 Python 3.13 与现代 WebView，分叉实际永远走不到。
底线抬到 26 之后分叉就该删掉——留着是死代码，还会让后来人以为还在兼容 7.x。

这个套件把三件事钉住：

  1. `build.gradle.kts` 的 `minSdk` 就是 26，别被悄悄改回 24；
  2. SDK 三档的关系没乱（compileSdk >= targetSdk >= minSdk），清单里也没有
     另一份 `uses-sdk` 去覆盖构建脚本；
  3. `GatewayService` 里针对 API 26 的旧分支已经清干净（包含「有分叉就必须还在用」
     的反向保护：宁可让这条测试红，也不要留一段没人走的代码）。

Run: python tests/_test_android_min_sdk.py
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(ROOT, "mobile", "android")
APP_GRADLE = os.path.join(ANDROID, "app", "build.gradle.kts")
MANIFEST = os.path.join(ANDROID, "app", "src", "main", "AndroidManifest.xml")
JAVA = os.path.join(ANDROID, "app", "src", "main", "java", "com", "proxygateway", "runtime")
README = os.path.join(ROOT, "mobile", "README.md")

MIN_SDK = 26


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def sdk_of(source, name):
    """取出 `name = 26` 这种赋值的数字，找不到返回 None。"""
    match = re.search(r"\b%s\s*=\s*(\d+)" % name, source)
    return int(match.group(1)) if match else None


def strip_comments(source):
    """去掉 Kotlin 行注释，只留会编译的代码。

    断言「某个判断没了」时不能把注释也算进去：解释「这里为什么不判版本」的注释本身
    就会提到 SDK_INT，直接用原文断言会把好注释当成死代码（这个套件第一版就栽在这）。
    """
    return "\n".join(re.sub(r"//.*$", "", line) for line in source.splitlines())


class GradleSdkLevels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gradle = read(APP_GRADLE)

    def test_min_sdk_is_26(self):
        value = sdk_of(self.gradle, "minSdk")
        self.assertIsNotNone(value, "build.gradle.kts 里找不到 minSdk")
        self.assertEqual(value, MIN_SDK,
                         "minSdk 应为 Android 8.0（%d），实为 %s；外壳的通知渠道与 "
                         "startForegroundService 都按这一档写死" % (MIN_SDK, value))

    def test_sdk_levels_are_ordered(self):
        compile_sdk = sdk_of(self.gradle, "compileSdk")
        target_sdk = sdk_of(self.gradle, "targetSdk")
        min_sdk = sdk_of(self.gradle, "minSdk")
        self.assertIsNotNone(compile_sdk, "找不到 compileSdk")
        self.assertIsNotNone(target_sdk, "找不到 targetSdk")
        self.assertGreaterEqual(compile_sdk, target_sdk,
                                "compileSdk 不能低于 targetSdk")
        self.assertGreaterEqual(target_sdk, min_sdk,
                                "targetSdk 不能低于 minSdk")

    def test_manifest_does_not_override_sdk(self):
        manifest = read(MANIFEST)
        self.assertNotIn("<uses-sdk", manifest,
                         "清单里再写一份 uses-sdk 会和 build.gradle.kts 抢值，"
                         "最低版本必须只有一个来源")


class NoDeadApi26Branches(unittest.TestCase):
    """分叉删干净了：minSdk 26 之后 `SDK_INT >= O` 恒真。"""

    @classmethod
    def setUpClass(cls):
        cls.raw = read(os.path.join(JAVA, "GatewayService.kt"))
        cls.service = strip_comments(cls.raw)

    def test_foreground_start_is_unconditional(self):
        self.assertIn("context.startForegroundService(intent)", self.service,
                      "服务启动要走 startForegroundService()")
        self.assertNotIn("context.startService(intent)", self.service,
                         "minSdk 26 起旧路径走不到，不该还留着 startService() 兜底")

    def test_no_obsolete_version_guard(self):
        self.assertIsNone(
            re.search(r"Build\.VERSION\.SDK_INT\s*[<>]=?\s*Build\.VERSION_CODES\.O\b",
                      self.service),
            "minSdk 已经是 26（= VERSION_CODES.O），这类判断恒为真，应删除")

    def test_notification_channel_has_no_early_return(self):
        body = re.search(r"private fun createChannel\(\) \{(.*?)\n    \}", self.service, re.S)
        self.assertIsNotNone(body, "找不到 createChannel()")
        self.assertNotIn("SDK_INT", body.group(1),
                         "8.0 以上通知渠道必定存在，不需要再判断版本")


class ReadmeStatesTheFloor(unittest.TestCase):
    def test_docs_mention_android_8(self):
        text = read(README)
        self.assertIn("minSdk = 26", text, "构建章节要写明最低版本")
        self.assertIn("Android 8.0", text, "用系统版本名说清楚，别只留数字")


if __name__ == "__main__":
    unittest.main(verbosity=2)
