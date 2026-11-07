"""容器同时兼容两种外部网关：导入识别 + 三端接入契约。

这个仓库不含网关实现，但用户能导入两种完全不同的外部项目：

  * Python 型（ardeyouxipianyi/workbuddy2api-hub）：wb_proxy.py + dashboard.html，
    能在 App 内由 CPython 本地托管；
  * Go 型（misakano7545/workbuddy2api-panel）：go.mod + cmd/server，单二进制。
    Android 10+ 禁止从应用私有目录执行未签名 ELF，iOS 只允许运行签名进包的
    代码，所以它**只能远程接入**——网关跑在电脑 / 服务器 / 本机 Termux，容器
    只负责显示 `/panel/` 面板与探活 `/healthz`。

「尽最大可能兼容」的落点是：两种 ZIP 都能被同一个 install() 认出来、两个导入
入口与交互不变、三端（Python 薄运行时 / Android Kotlin / iOS Swift）共用同一套
地址与字段契约、深浅色两套主题脚本互不干扰、健康字段归一化。

分三层钉住：
  1. 行为：真的造两份同构 ZIP 喂给 wb_hub_env.install()，看形态/版本/端口；
  2. 契约：三端源码里的路径、字段、主题键、状态码逐项对齐，不许哪一端偷偷
     退回「127.0.0.1/health」的硬编码；
  3. 边界：Go 型只被识别、绝不被本地执行；容器仍然保持「零网关源码」。

联网用例默认关闭：WB_LIVE_DUAL_COMPAT=1 时会下载两个真实仓库的 ZIP 再跑一遍。

Run:
    python tests/_test_dual_gateway_compat.py
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import zipfile

# 薄运行时所在目录会被 Chaquopy 整个打进 APK；import 时别在旁边留 .pyc。
sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "mobile", "android", "app", "src", "main", "java",
                    "com", "onegateway", "host")
SWIFT = os.path.join(ROOT, "mobile", "ios", "OneGateway", "Sources")
SHIM = os.path.join(ROOT, "mobile", "android", "app", "src", "main", "python",
                    "wb_hub_env.py")
STRINGS = os.path.join(ROOT, "mobile", "android", "app", "src", "main", "res",
                       "values", "strings.xml")

PYTHON_REPO = "ardeyouxipianyi/workbuddy2api-hub"
GO_REPO = "misakano7545/workbuddy2api-panel"

#: 唯一一份预期契约；三端源码与安装行为都要跟它对得上。
CONTRACT = {
    "python": {
        "marker": "wb_proxy.py", "panel": "/", "health": "/health",
        "accounts": "accounts", "usable": "accounts_ready", "port": 8788,
        "repo": PYTHON_REPO, "local": True,
    },
    "go": {
        "marker": "go.mod", "panel": "/panel/", "health": "/healthz",
        "accounts": "total", "usable": "healthy", "port": 7863,
        "repo": GO_REPO, "local": False,
    },
}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_shim():
    spec = importlib.util.spec_from_file_location("wb_hub_env_dual", SHIM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_archive(path, outer, entries):
    """造一份和 GitHub `archive/refs/heads/<branch>.zip` 同构的包。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr("%s/%s" % (outer, name), body)
    return path


PYTHON_ENTRIES = {
    "wb_proxy.py": '# a very small stand-in\nserver_version = "wb-proxy/2.0.0"\n',
    "dashboard.html": "<html data-ui='next'><body>panel</body></html>",
    "wb_usage_io.py": "# companion module\n",
}

GO_ENTRIES = {
    "go.mod": "module github.com/%s\n\ngo 1.26.10\n" % GO_REPO,
    "cmd/server/main.go": "package main\nfunc main() {}\n",
    "internal/panel/index.html": '<html data-theme="dark">panel</html>',
    "internal/panel/theme.js": "// reads wb2api.theme\n",
    "config.example.json": json.dumps({"listen": "127.0.0.1:7863"}),
}


class InstallDetectionTests(unittest.TestCase):
    """同一个 install() 必须认出两种形态，且不改变 Python 型的既有行为。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dual-compat-")
        self.addCleanup(self.tmp.cleanup)
        self.shim = load_shim()
        self.projects = os.path.join(self.tmp.name, "projects")

    def install(self, outer, entries):
        archive = make_archive(os.path.join(self.tmp.name, "source.zip"),
                               outer, entries)
        return self.shim.install(archive, self.projects)

    def test_python_zip_is_still_local_and_versioned(self):
        result = self.install("workbuddy2api-hub-main", PYTHON_ENTRIES)
        self.assertTrue(result.get("ok"), result)
        want = CONTRACT["python"]
        self.assertEqual(want["marker"], "wb_proxy.py")
        self.assertEqual("python", result["kind"])
        self.assertEqual("2.0.0", result["version"])
        self.assertEqual(want["port"], result["default_port"])
        self.assertTrue(result["has_panel"])
        self.assertEqual(3, result["files_extracted"])
        self.assertEqual("python", self.shim.project_info(result["root"])["kind"])

    def test_go_zip_is_recognised_with_its_own_port(self):
        result = self.install("workbuddy2api-panel-main", GO_ENTRIES)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("go", result["kind"])
        self.assertEqual(CONTRACT["go"]["port"], result["default_port"])
        self.assertEqual("workbuddy2api-panel", result["version"])
        self.assertTrue(result["has_panel"], "Go 面板在 internal/panel/index.html")
        self.assertEqual("go", self.shim.project_info(result["root"])["kind"])

    def test_go_port_comes_from_config_not_a_hardcoded_guess(self):
        entries = dict(GO_ENTRIES)
        entries["config.example.json"] = json.dumps({"listen": "0.0.0.0:9999"})
        result = self.install("workbuddy2api-panel-main", entries)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(9999, result["default_port"])

    def test_unknown_zip_names_both_markers_in_the_error(self):
        result = self.install("random-repo-main", {"README.md": "hello"})
        self.assertFalse(result.get("ok"))
        self.assertIn("wb_proxy.py", result["error"])
        self.assertIn("go.mod", result["error"])

    def test_go_project_is_never_imported_locally(self):
        result = self.install("workbuddy2api-panel-main", GO_ENTRIES)
        state = json.loads(self.shim.start(
            result["root"], os.path.join(self.tmp.name, "data"), 8788, False))
        self.assertFalse(state.get("ok"))
        self.assertFalse(state.get("running"))
        self.assertIn("远程接入", state.get("error", ""))
        self.assertNotIn("wb_proxy", sys.modules,
                         "start() 不该为 Go 型项目去 import Python 网关")

    def test_describe_reports_the_kind(self):
        result = self.install("workbuddy2api-panel-main", GO_ENTRIES)
        described = json.loads(self.shim.describe(result["root"]))
        self.assertEqual("go", described.get("kind"))


class ContractParityTests(unittest.TestCase):
    """三端契约不许各自漂移：路径、字段、主题、状态码、源地址。"""

    @classmethod
    def setUpClass(cls):
        cls.profile_kt = read(os.path.join(JAVA, "GatewayProfile.kt"))
        cls.bridge = read(os.path.join(JAVA, "GatewayBridge.kt"))
        cls.main = read(os.path.join(JAVA, "MainActivity.kt"))
        cls.service = read(os.path.join(JAVA, "GatewayService.kt"))
        cls.prefs = read(os.path.join(JAVA, "Prefs.kt"))
        cls.profile_swift = read(os.path.join(SWIFT, "GatewayProfile.swift"))
        cls.root_vc = read(os.path.join(SWIFT, "RootViewController.swift"))
        cls.paths_swift = read(os.path.join(SWIFT, "CorePaths.swift"))
        cls.shim = read(SHIM)
        cls.strings = read(STRINGS)
        cls.sync_core = read(os.path.join(ROOT, "mobile", "ios", "scripts",
                                          "sync-core.sh"))

    def test_both_markers_are_shared_by_shim_and_bridge(self):
        for token in ("wb_proxy.py", "go.mod", "cmd/server/main.go",
                      "internal/panel/index.html"):
            self.assertIn(token, self.shim)
        self.assertIn('const val GO_MARKER = "go.mod"', self.profile_kt)
        self.assertIn("GatewayProfile.GO_MARKER", self.bridge)

    def test_panel_and_health_paths_match_on_all_platforms(self):
        for source in (self.profile_kt, self.profile_swift):
            for token in ('"/panel/"', '"/healthz"', '"/health"'):
                self.assertIn(token, source, "形态路径缺失：%s" % token)
        # 旧硬编码必须消失：探活与面板地址只能由 profile 拼出来。
        self.assertNotIn('URL("http://127.0.0.1:$port/health")', self.main)
        self.assertNotIn('"http://127.0.0.1:\\(Settings.port)/health"', self.root_vc)
        self.assertIn("GatewayProfile.healthUrl(", self.main)
        self.assertIn("GatewayProfile.healthURL", self.root_vc)
        self.assertIn("GatewayProfile.panelBase(this, port)", self.main)
        self.assertIn("GatewayProfile.panelBase", self.root_vc)

    def test_health_fields_are_normalised_on_both_platforms(self):
        for source in (self.profile_kt, self.profile_swift):
            for token in ('"total"', '"healthy"', '"accounts"',
                          '"accounts_ready"'):
                self.assertIn(token, source)
        self.assertIn("normaliseHealth(", self.main)
        self.assertIn("normaliseHealth(", self.root_vc)
        # Go 的 /healthz 在池不可服务时回 503，响应体仍带数据，要当「可达」。
        for source in (self.profile_kt, self.profile_swift, self.main):
            self.assertIn("503", source)
        self.assertIn("errorStream", self.main)

    def test_both_theme_channels_are_wired(self):
        # Python 面板：URL sysdark + wb:system-theme 事件；Go 面板：localStorage
        # ['wb2api.theme'] + applyTheme() 刷 data-theme。
        self.assertIn("?sysdark=", self.main)
        self.assertIn("wb:system-theme", self.main)
        self.assertIn("wb2api.theme", self.profile_kt)
        self.assertIn("applyTheme()", self.profile_kt)
        self.assertIn("wb2api.theme", self.profile_swift)
        self.assertIn("applyTheme()", self.profile_swift)
        self.assertIn("GatewayProfile.themeScript(dark)", self.main)
        self.assertIn("GatewayProfile.themeScript(dark: systemDark)", self.root_vc)
        self.assertIn("onPageFinished", self.main)
        self.assertIn("didFinish navigation", self.root_vc)

    def test_remote_mode_exists_on_both_platforms(self):
        self.assertIn("fun remote(context: Context): Boolean", self.prefs)
        self.assertIn("fun isRemote(context: Context)", self.profile_kt)
        self.assertIn("GatewayProfile.isRemote(context)", self.service)
        self.assertIn("var remoteEnabled: Bool", self.paths_swift)
        self.assertIn("static var remote: Bool", self.profile_swift)
        self.assertIn("if GatewayProfile.remote {", self.root_vc)
        # 远程模式不启动本地进程：Android 前台服务与 iOS CPython 都要短路。
        self.assertIn("GatewayService.lastStatus", self.main)
        self.assertNotIn("Runtime.getRuntime().exec", self.main)

    def test_two_import_entries_and_two_sources_stay(self):
        self.assertIn('name="menu_project_import"', self.strings)
        self.assertIn('name="menu_project_update"', self.strings)
        self.assertIn("launchProjectImport()", self.main)
        self.assertIn("startUpdate()", self.main)
        self.assertIn(PYTHON_REPO, self.bridge)
        self.assertIn(GO_REPO, self.profile_kt)
        self.assertIn("fun sourceZipUrls(kind: String)", self.bridge)
        self.assertIn("GatewayBridge.sourceZipUrls(Prefs.source(this))", self.main)

    def test_go_zip_switches_the_shell_to_remote_access(self):
        self.assertIn('result.optString("kind").ifBlank', self.bridge)
        self.assertIn("Prefs.setRemote(context, true)", self.bridge)
        self.assertIn("Prefs.setRemoteKind(context, GatewayProfile.GO)", self.bridge)
        self.assertIn('name="project_go_remote_hint"', self.strings)

    def test_ios_build_diagnoses_a_go_core_zip(self):
        self.assertIn('os.path.basename(n) == "go.mod"', self.sync_core)
        self.assertIn("远程网关接入", self.sync_core)

    def test_container_never_executes_an_external_binary(self):
        # 「兼容」不等于「把 Go 二进制塞进 App 里跑」：容器仍然只装载 Python 型。
        for source in (self.shim, self.main, self.service, self.root_vc):
            for forbidden in ("subprocess", "os.execv", "posix_spawn",
                              "Runtime.getRuntime().exec", "dlopen("):
                self.assertNotIn(forbidden, source)

    def test_default_ports_are_the_ones_the_upstream_projects_use(self):
        # Python 端口由 Prefs.DEFAULT_PORT 单一来源提供，Go 端口三端各留一份常量
        # 以便离线拼地址（导入时仍以 config.example.json 的 listen 为准）。
        self.assertIn("DEFAULT_PORT = 8788", self.prefs)
        self.assertIn("pythonDefaultPort = 8788", self.profile_swift)
        self.assertIn("Prefs.DEFAULT_PORT", self.profile_kt)
        for source in (self.profile_kt, self.profile_swift):
            self.assertIn("7863", source)
        self.assertIn("DEFAULT_PORT_GO = 7863", self.shim)

    def test_address_normalisation_supports_lan_and_https(self):
        for source in (self.profile_kt, self.profile_swift):
            self.assertIn("normaliseBase", source)
            self.assertIn("https://", source)
        self.assertIn('name="settings_remote"', self.strings)
        self.assertIn("远程网关接入…", self.root_vc)


class IosSyncCoreTests(unittest.TestCase):
    """iOS 构建脚本里的真实抽取逻辑：Python 核心照旧，Go 包给明确诊断。"""

    @classmethod
    def setUpClass(cls):
        cls.script = read(os.path.join(ROOT, "mobile", "ios", "scripts",
                                       "sync-core.sh"))
        match = re.search(r"<<'PY'\n(.*?)\nPY\n", cls.script, re.S)
        if not match:
            raise AssertionError("sync-core.sh 里的抽取脚本不见了")
        cls.extractor = match.group(1)

    def run_extractor(self, outer, entries):
        with tempfile.TemporaryDirectory(prefix="dual-sync-") as tmp:
            archive = make_archive(os.path.join(tmp, "core.zip"), outer, entries)
            dest = os.path.join(tmp, "app")
            os.makedirs(dest)
            proc = subprocess.run(
                [sys.executable, "-c", self.extractor, archive, dest],
                capture_output=True, text=True, timeout=30)
            return proc, sorted(os.listdir(dest))

    def test_extractor_is_valid_python(self):
        compile(self.extractor, "sync-core.sh:extractor", "exec")

    def test_python_core_is_still_extracted(self):
        proc, files = self.run_extractor("workbuddy2api-hub-main", PYTHON_ENTRIES)
        self.assertEqual(0, proc.returncode, proc.stderr)
        for name in ("wb_proxy.py", "wb_usage_io.py", "dashboard.html"):
            self.assertIn(name, files)

    def test_go_zip_fails_with_an_actionable_diagnosis(self):
        proc, files = self.run_extractor("workbuddy2api-panel-main", GO_ENTRIES)
        self.assertNotEqual(0, proc.returncode)
        self.assertEqual([], files, "Go 源码不该被当核心抽进 iOS 包")
        self.assertIn("Go 网关源码包", proc.stderr)
        self.assertIn("远程网关接入", proc.stderr)


class LiveArchiveTests(unittest.TestCase):
    """可选：真下两份仓库 ZIP 再跑一次识别。默认离线，CI 不联网。"""

    def setUp(self):
        if os.environ.get("WB_LIVE_DUAL_COMPAT") != "1":
            self.skipTest("设置 WB_LIVE_DUAL_COMPAT=1 才会下载真实仓库 ZIP")

    def test_real_archives_are_both_recognised(self):
        shim = load_shim()
        sources = (
            ("python", "workbuddy2api-hub-main", PYTHON_REPO, "python"),
            ("go", "workbuddy2api-panel-main", GO_REPO, "go"),
        )
        with tempfile.TemporaryDirectory(prefix="dual-live-") as tmp:
            for tag, outer, repo, expected in sources:
                url = ("https://v4.gh-proxy.org/https://github.com/%s/"
                       "archive/refs/heads/main.zip" % repo)
                archive = os.path.join(tmp, tag + ".zip")
                request = urllib.request.Request(
                    url, headers={"User-Agent": "OneGateway-dual-compat"})
                with urllib.request.urlopen(request, timeout=180) as response:
                    data = response.read()
                self.assertEqual(b"PK", data[:2], "%s 下载到的不是 ZIP" % repo)
                with open(archive, "wb") as fh:
                    fh.write(data)
                result = shim.install(archive, os.path.join(tmp, "projects"))
                self.assertTrue(result.get("ok"), "%s: %s" % (repo, result))
                self.assertEqual(expected, result["kind"])
                self.assertEqual(CONTRACT[expected]["port"],
                                 result["default_port"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
