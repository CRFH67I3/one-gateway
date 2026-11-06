"""APK 必须只是「网关运行容器」：不含网关的任何代码，网关只能靠导入。

这条边界比功能更容易被悄悄破坏：随手把一个 `.py`、一份 `dashboard.html` 或
一个 `assets/core/` 目录放进 `app/src/main/`，Gradle 会安静地打进去，谁也不会
察觉——直到有人发现「没导入项目也能跑」或「APK 里躺着网关源码」。

所以分四层钉住：

  1. **源码树**：`app/src/main` 下不存在网关文件（`wb_*.py` / `dashboard.html` /
     `assets/core`），也没有第二份 Python 模块；
  2. **薄运行时**：`wb_hub_env` 只做「解压 ZIP、找项目根、把导入的那份跑起来」，
     不定义账号池/HTTP 处理器，也不做任何业务判断；
  3. **外壳**：Kotlin 不从 assets 读代码、不用 `file://` 加载本地页面，
     `start()` 必须先有导入的项目才动手；
  4. **产物**：本机能找到 APK 时直接翻包——没有 `*.py`、没有 `dashboard.html`、
     没有 `assets/core/`，`assets/chaquopy/app.imy` 里只有薄运行时。

Run:
    python tests/_test_android_container_purity.py
"""
import io
import os
import re
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(ROOT, "mobile", "android")
MAIN = os.path.join(ANDROID, "app", "src", "main")
JAVA = os.path.join(MAIN, "java", "com", "proxygateway", "runtime")
PY_DIR = os.path.join(MAIN, "python")
ASSETS = os.path.join(MAIN, "assets")
SHIM = os.path.join(PY_DIR, "wb_hub_env.py")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def strip_comments(source):
    """去掉 `//` 行注释。

    断言「某段代码已经不存在」时，解释为什么删掉的注释里往往就写着那几个词，
    不清掉就会把好注释判成残留（本项目已经踩过两次）。
    """
    return "\n".join(line.split("//")[0] for line in source.splitlines())


def walk_files(base):
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            yield os.path.join(dirpath, name)


class SourceTreeTests(unittest.TestCase):
    """APK 会把 app/src/main 下的东西照搬进去，所以这里先看源码树。"""

    def test_no_gateway_entry_points_under_main(self):
        banned = {"wb_proxy.py", "dashboard.html", "wb_mobile_runtime.py",
                  "wb_hub_ios.py", "wb_activity.py", "wb_pricing.py",
                  "wb_scheduler.py", "release-manifest.json"}
        found = []
        for path in walk_files(MAIN):
            rel = os.path.relpath(path, MAIN).replace("\\", "/")
            if os.path.basename(path) in banned:
                found.append(rel)
        self.assertEqual([], found, "这些文件属于网关本体，不该出现在 APK 源码树里")

    def test_no_assets_core_directory(self):
        core = os.path.join(ASSETS, "core")
        self.assertFalse(os.path.exists(core), "assets/core/ 是旧「把核心打进 APK」的残留")
        # 就算目录被截断成空壳，也不能有内容
        if os.path.isdir(ASSETS):
            self.assertEqual([], [n for n in os.listdir(ASSETS) if n != ".gitkeep"],
                             "assets/ 应当为空：APK 不带任何随包资源")

    def test_python_source_dir_holds_only_the_shim(self):
        names = set(os.listdir(PY_DIR))
        # `__pycache__` 是 Python 的字节码缓存——谁 import 谁在旁边留一份，不是
        # 「第二个模块」。但它里面只允许出现这个薄运行时的字节码：冒出别的名字，
        # 说明有别的东西也住进了这个会被整个打进 APK 的目录。
        names.discard("__pycache__")
        self.assertEqual({"wb_hub_env.py"}, names,
                         "src/main/python 只允许一个薄运行时；多一个模块就是多一份代码")
        cache = os.path.join(PY_DIR, "__pycache__")
        if os.path.isdir(cache):
            foreign = sorted(n for n in os.listdir(cache)
                             if not n.startswith("wb_hub_env."))
            self.assertEqual([], foreign, "字节码缓存里出现了别的模块")


class ShimTests(unittest.TestCase):
    """薄运行时可以「调用」网关，但不能「实现」网关。"""

    @classmethod
    def setUpClass(cls):
        cls.source = read(SHIM)
        cls.code = strip_comments(cls.source)

    def test_only_drives_an_imported_project(self):
        self.assertIn('MARKER = "wb_proxy.py"', self.source)
        # 项目根必须来自调用方传进来的路径（导入解压出来的目录），不能是包内路径
        self.assertIn("def install(zip_path, projects_dir):", self.source)
        self.assertIn("import wb_proxy as gateway", self.code)

    def test_no_business_implementation(self):
        # 这些是网关本体的骨架：账号池、HTTP 处理器、模型白名单、限流开关。
        # 薄运行时只要出现其中任何一个，就说明有代码被搬进来了。
        for marker in ("BaseHTTPRequestHandler", "ALLOWED_MODELS",
                       "BLOCK_BACKGROUND_REQUESTS", "class Account",
                       "class Pool", "def do_GET", "def do_POST",
                       "sqlite3", "urllib.request"):
            self.assertNotIn(marker, self.code, "薄运行时里不该有 %s" % marker)

    def test_runtime_surface_is_small(self):
        # 运行时动作就是外壳真正会调的这几个；新增动作要同时改这里，
        # 逼着人想一遍「这是外壳的事还是网关的事」。
        allowed = {"install", "project_info", "status", "start", "stop",
                   "log_tail", "describe", "loaded"}
        public = set(re.findall(r"^def ([a-z][a-z0-9_]*)\(", self.code, re.M))
        self.assertEqual(allowed, public)

    def test_shim_stays_thin(self):
        # 528 行是当前体量；上限留一点余量，但绝不允许它长成第二个网关。
        self.assertLess(len(self.source.splitlines()), 700,
                        "薄运行时在变厚，先确认搬进来的不是业务逻辑")
        self.assertLess(os.path.getsize(SHIM), 40 * 1024)

    def test_no_credential_import_leftover(self):
        # 「导入账号凭证」这个入口整条删掉了（手机拿不到桌面端的解密密钥），
        # Python 侧留一个能往账号池塞账号的函数，等于给这条死路留了后门。
        self.assertNotIn("import_credential", self.code)


class ShellTests(unittest.TestCase):
    """外壳只负责托管进程与平台能力。"""

    @classmethod
    def setUpClass(cls):
        cls.bridge = read(os.path.join(JAVA, "GatewayBridge.kt"))
        cls.activity = read(os.path.join(JAVA, "MainActivity.kt"))
        cls.service = read(os.path.join(JAVA, "GatewayService.kt"))

    def test_shell_never_reads_code_from_assets(self):
        for name, source in (("GatewayBridge", self.bridge),
                             ("MainActivity", self.activity),
                             ("GatewayService", self.service)):
            code = strip_comments(source)
            self.assertNotIn("assets.open(", code, name)
            self.assertNotIn("file://", code, name)
            self.assertNotIn("loadDataWithBaseURL", code, name)

    def test_start_requires_an_imported_project(self):
        start = self.bridge[self.bridge.index("fun start("):]
        start = start[: start.index("\n    fun stop(")]
        self.assertIn("projectRoot(context) ?: return notInstalled()", start)
        self.assertIn("fun notInstalled()", self.bridge)

    def test_bridge_only_calls_the_documented_runtime_actions(self):
        calls = set(re.findall(r'invoke\("([a-z_]+)"', self.bridge))
        calls |= set(re.findall(r'callAttr\("([a-z_]+)"', self.bridge))
        self.assertLessEqual(calls, {"install", "describe", "start", "stop",
                                     "status", "log_tail"})
        # 所有调用都走同一个 module 常量，没有第二个 Python 入口
        self.assertEqual(1, self.bridge.count('const val MODULE = "wb_hub_env"'))


class GradleTests(unittest.TestCase):
    """构建脚本不能再把仓库根目录塞给 Gradle。"""

    @classmethod
    def setUpClass(cls):
        cls.gradle = read(os.path.join(ANDROID, "app", "build.gradle.kts"))

    def test_no_extra_source_or_asset_dirs(self):
        for marker in ("srcDirs", "sourceSets", "assets.srcDirs", "java.srcDirs"):
            self.assertNotIn(marker, self.gradle,
                             "构建脚本不该改写源码/资源目录（会绕开容器边界）")

    def test_repo_root_is_only_read_for_the_version(self):
        # 允许读 wb_proxy.py 的 server_version（版本号单一来源），
        # 但只允许这一处，且必须是读文件而不是拷文件。
        self.assertEqual(1, self.gradle.count("File(repoRoot,"))
        self.assertIn('File(repoRoot, "wb_proxy.py")', self.gradle)

    def test_no_pip_requirements(self):
        # 网关是纯标准库实现；一旦开始装 pip 包，就意味着有依赖被搬进 APK。
        block = self.gradle[self.gradle.index("chaquopy {"):]
        self.assertNotIn("pip {", block)
        self.assertNotIn("install(", block)


class ApkTests(unittest.TestCase):
    """有产物就直接翻包；没有产物（CI 的单元测试作业）就跳过。"""

    @classmethod
    def setUpClass(cls):
        base = os.path.join(ANDROID, "app", "build", "outputs", "apk")
        candidates = []
        for path in walk_files(base) if os.path.isdir(base) else []:
            if path.endswith(".apk"):
                candidates.append(path)
        cls.apk = max(candidates, key=os.path.getmtime) if candidates else ""

    def setUp(self):
        if not self.apk:
            self.skipTest("本机没有 APK 产物：先跑一次 assembleRelease")

    def _names(self):
        with zipfile.ZipFile(self.apk) as zf:
            return zf.namelist()

    def test_apk_has_no_gateway_files(self):
        bad = [n for n in self._names()
               if n.startswith("assets/core")
               or n.endswith(".py")
               or n.endswith("dashboard.html")
               or os.path.basename(n) in ("wb_proxy.py", "wb_proxy.pyc")]
        self.assertEqual([], bad, "APK 里出现了网关文件")

    def test_app_imy_only_has_the_shim(self):
        with zipfile.ZipFile(self.apk) as zf:
            blob = zf.read("assets/chaquopy/app.imy")
        with zipfile.ZipFile(io.BytesIO(blob)) as inner:
            names = [n for n in inner.namelist() if not n.endswith("/")]
        self.assertEqual(["wb_hub_env.pyc"], names)

    def test_no_pip_packages_bundled(self):
        with zipfile.ZipFile(self.apk) as zf:
            for name in ("assets/chaquopy/requirements-common.imy",
                         "assets/chaquopy/requirements-arm64-v8a.imy",
                         "assets/chaquopy/requirements-x86_64.imy"):
                if name not in zf.namelist():
                    continue
                with zipfile.ZipFile(io.BytesIO(zf.read(name))) as inner:
                    self.assertEqual([], inner.namelist(), "%s 不该有依赖" % name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
