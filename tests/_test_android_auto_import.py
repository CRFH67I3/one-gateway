"""Android「自动导入最新版」：首装界面一键下载仓库 main 并导入。

这个功能横跨三层，任何一层写错用户都看不到结果，所以分三组钉住：

  1. 外壳接线（Kotlin/XML）：首次安装覆盖层里有「自动导入」按钮、点了会走
     `downloadProjectZip` → `installProject`，且下载地址首选用户指定的
     gh-proxy 镜像（镜像挂了才退直连）；
  2. 导入契约（Python）：下载到的 GitHub archive ZIP 多套一层 `repo-main/`，
     `wb_hub_env.install()` 必须能找到里层的项目根，并拒绝 zip-slip；
  3. 真实链路（联网，默认跳过）：按 Kotlin 里同样的地址真的下一次，解压并
     读出 `server_version`——这一步在 CI 里由 `WB_LIVE_IMPORT=1` 打开。

Run:
    python tests/_test_android_auto_import.py            # 离线三组（第 3 组跳过）
    WB_LIVE_IMPORT=1 python tests/_test_android_auto_import.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(ROOT, "mobile", "android")
JAVA = os.path.join(ANDROID, "app", "src", "main", "java", "com", "proxygateway", "runtime")
PY_RUNTIME_DIR = os.path.join(ANDROID, "app", "src", "main", "python")

sys.path.insert(0, PY_RUNTIME_DIR)

# 不许在源码目录留 __pycache__：这是 app/src/main/python，Gradle 会把整个目录
# 打进 APK（Chaquopy 的 app.imy）。Python 默认 import 时就会在旁边写 .pyc，
# 于是「跑一遍测试」会顺手往发布物里塞一份构建脏东西。
sys.dont_write_bytecode = True

import wb_hub_env  # noqa: E402

PRIMARY_URL = (
    "https://v4.gh-proxy.org/https://github.com/"
    "ardeyouxipianyi/workbuddy2api-hub/archive/refs/heads/main.zip"
)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def make_archive(zip_path, entries, outer="workbuddy2api-hub-main"):
    """造一份和 GitHub `archive/refs/heads/main.zip` 同构的包。

    真包的每个条目都带 `repo-main/` 前缀，这一层是导入逻辑最容易踩空的地方，
    所以离线用例必须照着真包的样子造。
    """
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            arc = name if outer is None else "%s/%s" % (outer, name)
            zf.writestr(arc, body)
    return zip_path


class ShellWiringTests(unittest.TestCase):
    """Kotlin / XML / strings 的结构断言。"""

    def setUp(self):
        self.main = read(os.path.join(JAVA, "MainActivity.kt"))
        self.bridge = read(os.path.join(JAVA, "GatewayBridge.kt"))
        self.layout = read(os.path.join(ANDROID, "app", "src", "main", "res",
                                        "layout", "activity_main.xml"))
        self.strings = read(os.path.join(ANDROID, "app", "src", "main", "res",
                                        "values", "strings.xml"))

    def test_overlay_has_auto_import_button_below_import(self):
        layout = self.layout
        self.assertIn('android:id="@+id/overlayAutoImport"', layout)
        self.assertIn('android:text="@string/overlay_auto_import"', layout)
        # 位置要求：必须排在「导入网关项目压缩包」那个按钮之后（下方）。
        self.assertLess(layout.index('@+id/overlayAction'),
                        layout.index('@+id/overlayAutoImport'))

    def test_button_label_and_states_exist(self):
        self.assertIn("overlay_auto_import", self.strings)
        self.assertIn("自动导入", self.strings)
        for name in ("auto_import_connecting", "auto_import_progress",
                     "auto_import_installing", "auto_import_failed"):
            self.assertIn('name="%s"' % name, self.strings)

    def test_auto_import_is_wired_to_download(self):
        self.assertIn("overlayAuto.setOnClickListener { startAutoImport() }", self.main)
        self.assertIn("GatewayBridge.downloadProjectZip(", self.main)
        # 下载完必须复用同一套安装逻辑（解压 → 切指针 → 重启）。
        self.assertIn("applyProject(zip, update)", self.main)
        self.assertIn("GatewayBridge.installProject(", self.main)

    def test_primary_url_points_at_external_gateway_repo(self):
        self.assertIn("v4.gh-proxy.org", self.bridge)
        self.assertIn("ardeyouxipianyi/workbuddy2api-hub", self.bridge)
        self.assertIn("refs/heads/$BRANCH", self.bridge)
        # 首选必须是镜像，直连只能是兜底（国内直连 archive 基本超时）。
        urls = re.search(r"PROJECT_ZIP_URLS[\s\S]{0,600}?\)\n", self.bridge)
        self.assertIsNotNone(urls)
        block = urls.group(0)
        self.assertLess(block.index("v4.gh-proxy.org"), block.index("github.com/$REPO"))

    def test_download_guards(self):
        # 弱网必须有超时，否则会永远停在 0%。
        self.assertIn("connectTimeout", self.bridge)
        self.assertIn("readTimeout", self.bridge)
        # 镜像的错误页也会 200，所以要验 ZIP 魔数。
        self.assertIn("isZip(", self.bridge)
        # 进度回调不能逐块刷 UI。
        self.assertIn("lastPct", self.bridge)

    def test_install_never_restarts_process(self):
        """装完新版本不能重启进程：进程一死，Android 会拒绝后台拉起界面。"""
        self.assertNotIn("killProcess", self.main)
        self.assertNotIn("restartApp", self.main)
        # 装完直接起服务，覆盖层才会自己切到面板
        self.assertIn("GatewayService.ACTION_START", self.main)
        # 面板要按新版本重新载一次
        apply_body = self.main[self.main.index("private fun applyProject("):]
        apply_body = apply_body[: apply_body.index("\n    }\n")]
        self.assertIn("loadedUrl = null", apply_body)

    def test_python_purges_old_modules_on_install(self):
        """换代码靠 Python 侧摘 sys.modules，不是靠重启进程。"""
        runtime = read(os.path.join(PY_RUNTIME_DIR, "wb_hub_env.py"))
        self.assertIn("def _purge_gateway_modules(", runtime)
        self.assertIn("sys.modules.pop(", runtime)
        # 旧项目目录也不能留在 sys.path 里，否则新版本删掉的模块会被「复活」
        self.assertIn("def _prune_sys_path(", runtime)
        install_body = runtime[runtime.index("def install("):]
        install_body = install_body[: install_body.index("\ndef ")]
        self.assertIn("_purge_gateway_modules(projects_dir)", install_body)

    def test_status_comes_from_our_own_runtime(self):
        """端口被别人占着时不能假「运行中」——状态必须以运行环境为准。"""
        health_body = self.main[self.main.index("private fun fetchHealth()"):]
        check_at = health_body.index("GatewayService.lastStatus")
        http_at = health_body.index("openConnection()")
        self.assertLess(check_at, http_at, "必须先确认是自己的网关在跑，再去探 /health")
        self.assertIn('name="status_failed"', self.strings)
        self.assertIn('name="overlay_start_failed"', self.strings)


class MenuSplitTests(unittest.TestCase):
    """右上角菜单：手动更新与自动更新是两个独立入口，更新复用自动导入的下载流程。"""

    def setUp(self):
        self.main = read(os.path.join(JAVA, "MainActivity.kt"))
        self.strings = read(os.path.join(ANDROID, "app", "src", "main", "res",
                                        "values", "strings.xml"))

    def test_two_menu_items(self):
        self.assertIn('name="menu_project_import"', self.strings)
        self.assertIn('name="menu_project_update"', self.strings)
        self.assertIn("MENU_PROJECT_UPDATE", self.main)
        # 两个入口都在菜单里，且「自动更新」排在「手动更新」之后
        self.assertLess(self.main.index("MENU_PROJECT, 2"),
                        self.main.index("MENU_PROJECT_UPDATE, 3"))

    def test_menu_wording(self):
        # 用户看到的两个词就是「手动更新」「自动更新」：换一份代码的两条路，
        # 不存在「先导入项目」这种前置动作。
        self.assertIn('name="menu_project_import">手动更新<', self.strings)
        self.assertIn('name="menu_project_update">自动更新<', self.strings)
        # 旧文案必须彻底消失，否则菜单里会出现两种叫法
        for stale in ("导入网关项目压缩包", "更新网关项目（联网下载）", "导入/更新"):
            self.assertNotIn(stale, self.strings)
        # 首装覆盖层的两个按钮同口径：手动更新 / 自动更新
        self.assertIn("手动更新（选择压缩包）", self.strings)
        self.assertIn("自动更新（联网下载）", self.strings)

    def test_manual_update_needs_no_credential(self):
        # 手动更新只做三件事：读选中 ZIP 的输入流 → 解压 → 换指针。
        # 任何「凭证/许可证/激活码」式的校验都不该出现在这条路径上。
        body = self.main[self.main.index("private fun importProject("):]
        body = body[: body.index("\n    }\n")]
        self.assertIn("GatewayBridge.stageZip(", body)
        self.assertIn("applyProject(zip", body)
        for word in ("凭证", "credential", "Credential", "license", "License"):
            self.assertNotIn(word, body)
        # 安装入口本身也不带身份参数：ZIP 进、结果出
        bridge = read(os.path.join(JAVA, "GatewayBridge.kt"))
        install = bridge[bridge.index("fun installProject("):]
        install = install[: install.index("\n    fun ")]
        self.assertNotIn("credential", install.lower())

    def test_no_credential_import_entry(self):
        # 手机端拿不到桌面客户端的解密密钥，导入 .info 本来就不成立：
        # 入口删掉，登录统一走面板的 OAuth。
        self.assertNotIn("menu_import", self.strings)
        self.assertNotIn("导入账号凭证", self.strings)
        for stale in ("MENU_IMPORT", "launchImport", "importCredential",
                      "REQUEST_IMPORT", "R.string.import_"):
            self.assertNotIn(stale, self.main, stale)
        bridge = read(os.path.join(JAVA, "GatewayBridge.kt"))
        self.assertNotIn("fun importCredential", bridge)

    def test_update_uses_auto_import_flow(self):
        body = self.main[self.main.index("private fun startUpdate()"):]
        body = body[: body.index("\n    }\n")]
        self.assertIn("startAutoImport(update = ", body)
        # 只保留一条下载实现：更新不得自己再写一遍下载逻辑
        self.assertEqual(self.main.count("GatewayBridge.downloadProjectZip("), 1)

    def test_update_shares_one_install_path(self):
        # 解压 → 切指针 → 重启只有一份实现，三条入口共用
        self.assertEqual(self.main.count("GatewayBridge.installProject("), 1)
        self.assertEqual(self.main.count("private fun applyProject("), 1)
        self.assertIn("applyProject(zip, update)", self.main)

    def test_update_wording_is_used_while_updating(self):
        for name in ("update_running", "update_failed", "update_ok",
                     "update_installing", "update_downloading",
                     "status_updating", "status_importing"):
            self.assertIn('name="%s"' % name, self.strings)

    def test_polling_does_not_overwrite_progress(self):
        render = self.main[self.main.index("private fun render(health: Health)"):]
        # 自动导入/更新期间，render 必须在碰覆盖层之前就返回
        self.assertLess(render.index("if (autoImporting) return"),
                        render.index("overlay.visibility"))

    def test_update_failure_returns_to_panel(self):
        body = self.main[self.main.index("private fun finishAutoImport("):]
        body = body[: body.index("\n    }\n")]
        # 更新失败时旧项目还在：收掉覆盖层，别把还能用的面板挡死
        self.assertIn("overlay.visibility = View.GONE", body)
        self.assertIn("hasProject", body)


class AboutMergeTests(unittest.TestCase):
    """「项目信息」并进「关于」：一页里同时看到 App 版本和跑的是哪份网关。"""

    def setUp(self):
        self.main = read(os.path.join(JAVA, "MainActivity.kt"))
        self.strings = read(os.path.join(ANDROID, "app", "src", "main", "res",
                                        "values", "strings.xml"))
        self.about = self.main[self.main.index("private fun showAbout("):]
        self.about = self.about[: self.about.index("\n    }\n")]

    def test_project_info_is_gone_as_separate_entry(self):
        self.assertNotIn('name="menu_project_info"', self.strings)
        self.assertNotIn("MENU_PROJECT_INFO", self.main)
        self.assertNotIn("showProjectInfo", self.main)
        # 菜单里只剩一项，名字点明它同时是项目信息
        self.assertIn('name="menu_about">关于与项目信息<', self.strings)

    def test_about_shows_both_app_and_project(self):
        # App 版本来自 BuildConfig，网关版本等来自 describeProject / Prefs
        self.assertIn("BuildConfig.VERSION_NAME", self.about)
        for key in ("project_version", "project_imported", "project_path",
                    "project_panel", "project_data"):
            self.assertIn("R.string.%s" % key, self.about, key)
        self.assertIn("GatewayBridge.describeProject(", self.about)
        self.assertIn("GatewayBridge.dataDir(", self.about)

    def test_about_offers_both_update_paths(self):
        # 看着版本号发现旧了，就地更新：两个按钮就是两个更新入口
        self.assertIn("setPositiveButton(R.string.menu_project_import", self.about)
        self.assertIn("setNeutralButton(R.string.menu_project_update", self.about)
        self.assertIn("launchProjectImport()", self.about)
        self.assertIn("startUpdate()", self.about)

    def test_about_without_project_explains_both_paths(self):
        self.assertIn("R.string.project_none_hint", self.about)
        hint = self.strings[self.strings.index('name="project_none_hint"'):]
        hint = hint[: hint.index("</string>")]
        self.assertIn("手动更新", hint)
        self.assertIn("自动更新", hint)
        self.assertIn("不需要凭证", hint)


class WebViewRecoveryTests(unittest.TestCase):
    """换代码会先停网关，WebView 必然撞一次连接拒绝——得能自己爬起来。"""

    def setUp(self):
        self.main = read(os.path.join(JAVA, "MainActivity.kt"))

    def test_main_frame_error_clears_loaded_url(self):
        start = self.main.index("override fun onReceivedError(")
        body = self.main[start:self.main.index("        installJsDialogBridge()", start)]
        self.assertIn("loadedUrl = null", body)
        # 只对主文档重置：子资源失败不该触发整页重载
        self.assertIn("request?.isForMainFrame == true", body)


class BuildVariantTests(unittest.TestCase):
    """debug 全量构建、release 最小化构建。"""

    def setUp(self):
        self.gradle = read(os.path.join(ANDROID, "app", "build.gradle.kts"))
        self.rules = read(os.path.join(ANDROID, "app", "proguard-rules.pro"))
        self.workflow = read(os.path.join(ROOT, ".github", "workflows", "mobile-android.yml"))

    def _block(self, name):
        body = self.gradle[self.gradle.index("%s {" % name):]
        return body[: body.index("\n        }")]

    def test_debug_is_full_build(self):
        debug = self._block("debug")
        self.assertIn("isMinifyEnabled = false", debug)
        self.assertIn("isShrinkResources = false", debug)

    def test_release_is_minified(self):
        release = self._block("release")
        self.assertIn("isMinifyEnabled = true", release)
        self.assertIn("isShrinkResources = true", release)
        self.assertIn("proguard-rules.pro", release)

    def test_rules_keep_what_r8_would_break(self):
        # Chaquopy 的类由解释器按名字查找，必须整份保留
        self.assertIn("-keep class com.chaquo.python.** { *; }", self.rules)
        # 清单里的三个组件由系统按类名实例化
        for cls in ("HubApp", "MainActivity", "GatewayService"):
            self.assertIn("com.proxygateway.runtime.%s { *; }" % cls, self.rules)
        # 外壳其余类只保名字：不能再用「整包 { *; }」把最小化废掉
        self.assertNotIn("-keep class com.proxygateway.runtime.** { *; }", self.rules)

    def test_workflow_builds_both_variants(self):
        self.assertIn(":app:assembleDebug", self.workflow)
        self.assertIn(":app:assembleRelease", self.workflow)


class InstallContractTests(unittest.TestCase):
    """`wb_hub_env.install()` 面对真包结构时的行为。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wb-autoimport-")
        self.projects = os.path.join(self.tmp, "projects")
        self.zip = os.path.join(self.tmp, "project.zip")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _install(self, entries, outer="workbuddy2api-hub-main"):
        make_archive(self.zip, entries, outer)
        return wb_hub_env.install(self.zip, self.projects)

    def test_finds_root_inside_github_archive_layer(self):
        result = self._install({
            "wb_proxy.py": 'server_version = "wb-proxy/9.9.9"\n',
            "dashboard.html": "<html>panel</html>",
            "wb_accounts.py": "x = 1\n",
        })
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["version"], "9.9.9")
        self.assertTrue(result["has_panel"])
        self.assertTrue(result["root"].endswith("workbuddy2api-hub-main"))
        self.assertEqual(result["files_extracted"], 3)
        # 解压到 projects/<时间戳>/，不是 projects/ 本身
        self.assertTrue(result["dest"].startswith(self.projects))

    def test_rejects_zip_without_gateway_entry(self):
        result = self._install({"README.md": "# nope\n"})
        self.assertFalse(result["ok"])
        self.assertIn("wb_proxy.py", result["error"])

    def test_rejects_bad_zip(self):
        with open(self.zip, "wb") as fh:
            fh.write(b"this is not a zip at all")
        result = wb_hub_env.install(self.zip, self.projects)
        self.assertFalse(result["ok"])
        self.assertIn("ZIP", result["error"])

    def test_zip_slip_entries_are_defused(self):
        result = self._install({
            "wb_proxy.py": 'server_version = "wb-proxy/1.2.3"\n',
            "dashboard.html": "<html></html>",
        })
        # 手工追加一条 ../evil.txt（make_archive 只造正常条目）
        with zipfile.ZipFile(self.zip, "a") as zf:
            zf.writestr("../../evil.txt", "pwned")
        result = wb_hub_env.install(self.zip, self.projects)
        self.assertTrue(result["ok"], result)
        escaped = os.path.join(self.tmp, "evil.txt")
        self.assertFalse(os.path.exists(escaped))
        # 条目本身仍被解出来，只是落在项目目录内部
        self.assertTrue(os.path.exists(os.path.join(result["dest"], "evil.txt")))

    def test_macosx_noise_is_skipped(self):
        result = self._install({
            "wb_proxy.py": 'server_version = "wb-proxy/1.0.0"\n',
            "__MACOSX/._wb_proxy.py": "junk",
        })
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files_extracted"], 1)

    def test_two_imports_do_not_collide(self):
        first = self._install({"wb_proxy.py": 'server_version = "wb-proxy/1.0.0"\n'})
        second = self._install({"wb_proxy.py": 'server_version = "wb-proxy/1.0.1"\n'})
        self.assertTrue(first["ok"] and second["ok"])
        self.assertNotEqual(first["dest"], second["dest"])
        # 旧版本目录还在：升级失败也能回退，这是 install 的设计要求。
        self.assertTrue(os.path.isdir(first["dest"]))

    def test_loaded_flags_whether_gateway_is_imported(self):
        """首装时 `wb_proxy` 不在 sys.modules → 外壳据此决定不重启进程。"""
        self.assertNotIn("wb_proxy", sys.modules)
        payload = json.loads(wb_hub_env.loaded())
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["loaded"])

    def test_port_conflict_is_explained(self):
        """端口被占用要给「怎么解决」，不是一句 listen failed。"""
        import errno

        busy = OSError(errno.EADDRINUSE, "Address already in use")
        message = wb_hub_env._listen_error(busy, 8788)
        self.assertIn("8788", message)
        self.assertIn("占用", message)
        # 换端口是这里唯一能让用户自己解决的动作，必须出现在提示里。
        self.assertIn("端口设置", message)

        other = wb_hub_env._listen_error(OSError(errno.EACCES, "Permission denied"), 80)
        self.assertIn("80", other)
        self.assertNotIn("端口设置", other)


class ModulePurgeTests(unittest.TestCase):
    """更新网关 = 原地换代码：install() 必须把旧项目的模块真的摘掉。

    这条是「不重启进程」方案的地基。真机上进程一死，Android 就拒绝后台拉起界面
    （日志里的 `Abort background activity starts`），所以只能靠摘 sys.modules；
    摘不干净的话，用户点完更新会发现版本号没变。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wb-purge-")
        self.projects = os.path.join(self.tmp, "projects")
        self.zip = os.path.join(self.tmp, "project.zip")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        sys.modules.pop("wb_proxy", None)
        projects = os.path.abspath(self.projects)
        sys.path[:] = [p for p in sys.path if not os.path.abspath(p).startswith(projects)]

    def _install_version(self, version):
        make_archive(self.zip, {"wb_proxy.py": 'VERSION = "%s"\n' % version})
        return wb_hub_env.install(self.zip, self.projects)

    def test_update_reloads_module_from_new_project(self):
        first = self._install_version("1.0.0")
        self.assertTrue(first["ok"], first)
        sys.path.insert(0, first["root"])
        import wb_proxy
        self.assertEqual(wb_proxy.VERSION, "1.0.0")

        second = self._install_version("2.0.0")
        self.assertTrue(second["ok"], second)
        # 旧项目 import 进来的模块必须被摘掉
        self.assertIn("wb_proxy", second["purged"])
        self.assertNotIn("wb_proxy", sys.modules)

        sys.path.insert(0, second["root"])
        import wb_proxy as reloaded
        self.assertEqual(reloaded.VERSION, "2.0.0")

    def test_prune_drops_stale_project_paths(self):
        first = self._install_version("1.0.0")
        second = self._install_version("2.0.0")
        sys.path.insert(0, first["root"])
        sys.path.insert(1, second["root"])
        wb_hub_env._prune_sys_path(second["root"], self.projects)
        entries = [os.path.abspath(p) for p in sys.path]
        self.assertNotIn(os.path.abspath(first["root"]), entries)
        self.assertIn(os.path.abspath(second["root"]), entries)

    def test_runtime_module_survives(self):
        """wb_hub_env 自己不在 projects 目录里，绝不能被顺手摘掉。"""
        result = self._install_version("1.0.0")
        self.assertTrue(result["ok"], result)
        self.assertNotIn("wb_hub_env", result["purged"])

    def test_stdio_is_not_double_wrapped_after_update(self):
        """更新会再 configure 一次：日志只能写一遍，不能在旧 Tee 外面再套一层。"""
        import io

        tmp = tempfile.mkdtemp(prefix="wb-stdio-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        saved = (sys.stdout, sys.stderr)
        self.addCleanup(lambda: (
            setattr(sys, "stdout", saved[0]),
            setattr(sys, "stderr", saved[1]),
            setattr(wb_hub_env, "_STDIO_ORIGINAL", None),
        ))
        log = os.path.join(tmp, "gateway.log")
        sys.stdout = io.StringIO()
        wb_hub_env._STDIO_ORIGINAL = None
        wb_hub_env._bind_stdio(log)
        wb_hub_env._bind_stdio(log)
        print("after-update-marker")
        written = [line for line in open(log, encoding="utf-8").read().splitlines()
                   if line.strip() == "after-update-marker"]
        self.assertEqual(len(written), 1, "同一行被写了 %d 遍：Tee 被嵌套了" % len(written))

    def test_start_reconfigures_when_project_changes(self):
        """只摘模块不够：sys.path 里还挂着旧项目，import 会又拿到旧代码。"""
        runtime = read(os.path.join(PY_RUNTIME_DIR, "wb_hub_env.py"))
        body = runtime[runtime.index("def start(project, data, port, lan):"):]
        body = body[: body.index("\ndef ")]
        self.assertRegex(body, r"switched = _CORE is not None")
        self.assertIn("_configure(project, data)", body)
        self.assertIn("_BOOTED = False", body)


@unittest.skipUnless(os.environ.get("WB_LIVE_IMPORT") == "1",
                     "联网用例，设 WB_LIVE_IMPORT=1 打开")
class LiveDownloadTests(unittest.TestCase):
    """按 App 里同样的首选地址真下一次，验证镜像当前可用且包结构没变。"""

    def test_download_and_install(self):
        import urllib.request

        tmp = tempfile.mkdtemp(prefix="wb-live-")
        try:
            request = urllib.request.Request(
                PRIMARY_URL, headers={"User-Agent": "ProxyGatewayRuntime-Android"})
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
            self.assertGreater(len(body), 100 * 1024, "下载内容太小，可能是错误页")
            self.assertEqual(body[:2], b"PK")

            zip_path = os.path.join(tmp, "live.zip")
            with open(zip_path, "wb") as fh:
                fh.write(body)
            result = wb_hub_env.install(zip_path, os.path.join(tmp, "projects"))
            self.assertTrue(result["ok"], result)
            self.assertRegex(result["version"], r"^\d+\.\d+")
            self.assertTrue(result["has_panel"])
            print("    live: %.1f MB, version=%s, files=%d"
                  % (len(body) / 1048576.0, result["version"], result["files_extracted"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
