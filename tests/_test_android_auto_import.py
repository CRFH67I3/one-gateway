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
JAVA = os.path.join(ANDROID, "app", "src", "main", "java", "com", "workbuddy2api", "hub")
PY_RUNTIME_DIR = os.path.join(ANDROID, "app", "src", "main", "python")

sys.path.insert(0, PY_RUNTIME_DIR)

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
        self.assertIn("applyProject(zip)", self.main)
        self.assertIn("GatewayBridge.installProject(", self.main)

    def test_primary_url_is_the_mirror_the_user_asked_for(self):
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

    def test_first_install_does_not_restart_process(self):
        """首装导入完不重启进程：重启会把用户甩回桌面，像「导入完就闪退」。"""
        self.assertIn("gatewayLoadedInProcess()", self.main)
        self.assertIn("needsRestart", self.main)
        # restartApp 只能在「进程里已有旧网关」那条分支里被调用。
        lines = self.main.splitlines()
        call_index = next(i for i, line in enumerate(lines) if line.strip() == "restartApp()")
        guard = next(i for i, line in enumerate(lines) if "if (needsRestart)" in line)
        self.assertLess(guard, call_index)
        # 首次安装分支要直接把服务拉起来，覆盖层才会切到面板。
        self.assertIn("GatewayService.ACTION_START", self.main)

    def test_restart_uses_alarm_to_come_back(self):
        """进程自杀后只有系统持有的 PendingIntent 能把界面拉回来。"""
        body = self.main[self.main.index("private fun restartApp()"):]
        body = body[: body.index("\n    }\n")]
        self.assertIn("PendingIntent.getActivity", body)
        self.assertIn("AlarmManager", body)


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


@unittest.skipUnless(os.environ.get("WB_LIVE_IMPORT") == "1",
                     "联网用例，设 WB_LIVE_IMPORT=1 打开")
class LiveDownloadTests(unittest.TestCase):
    """按 App 里同样的首选地址真下一次，验证镜像当前可用且包结构没变。"""

    def test_download_and_install(self):
        import urllib.request

        tmp = tempfile.mkdtemp(prefix="wb-live-")
        try:
            request = urllib.request.Request(
                PRIMARY_URL, headers={"User-Agent": "WorkBuddyHub-Android"})
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
