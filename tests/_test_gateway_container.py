"""纯 Python 容器的接入契约：导入识别 + 三端地址/字段对齐 + 运行边界。

这个仓库不含网关实现，用户导入的外部项目只有一种能跑：

  * Python 型（ardeyouxipianyi/workbuddy2api-hub）：wb_proxy.py + dashboard.html，
    由 App 内嵌 CPython 本地托管，ZIP 可以热更新。

Go 型（misakano7545/workbuddy2api-panel）曾被「构建期编译、随包签名」地支持过，
但设备上既不能编译 Go、也不能执行下载来的 ELF，随包方案意味着上游一更新就得
重装整个 App——已整体回退。现在 Go 源码包会被 install() 明确拒绝，三端源码里
不允许再出现任何 Go 运行时（GoGateway / GoBridge / libgateway / setup-go）。

分三层钉住：
  1. 行为：真的造 ZIP 喂给 wb_hub_env.install()，看版本/端口；Go 包必须被拒。
  2. 契约：三端（Python 薄运行时 / Android Kotlin / iOS Swift）的路径、字段、
     端口、主题事件逐项对齐，不许哪一端偷偷退回硬编码。
  3. 边界：容器不携带网关源码、不携带 Go 产物；下载的可执行代码绝不进运行时。

联网用例默认关闭：WB_LIVE_GATEWAY_CONTAINER=1 时会下载真实仓库 ZIP 再跑一遍。

Run:
    python tests/_test_gateway_container.py
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
    "marker": "wb_proxy.py",
    "panel": "/",
    "health": "/health",
    "accounts": "accounts",
    "usable": "accounts_ready",
    "port": 8788,
    "repo": PYTHON_REPO,
}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_shim():
    spec = importlib.util.spec_from_file_location("wb_hub_env_container", SHIM)
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
}


class InstallDetectionTests(unittest.TestCase):
    """install() 只收 Python 网关；Go 源码包要被明确拒绝。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gw-container-")
        self.addCleanup(self.tmp.cleanup)
        self.shim = load_shim()
        self.projects = os.path.join(self.tmp.name, "projects")

    def install(self, outer, entries):
        archive = make_archive(os.path.join(self.tmp.name, "source.zip"),
                               outer, entries)
        return self.shim.install(archive, self.projects)

    def test_python_zip_is_local_and_versioned(self):
        result = self.install("workbuddy2api-hub-main", PYTHON_ENTRIES)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("python", result["kind"])
        self.assertEqual("2.0.0", result["version"])
        self.assertEqual(CONTRACT["port"], result["default_port"])
        self.assertTrue(result["has_panel"])
        self.assertEqual(3, result["files_extracted"])
        self.assertEqual("python", self.shim.project_info(result["root"])["kind"])

    def test_go_zip_is_rejected_with_an_actionable_reason(self):
        # 手机上不能编译 Go、也不能执行下载来的 ELF；随包内置方案已被回退。
        # install 必须在解压后立刻失败并清掉残留，绝不能登记一个跑不起来的项目。
        result = self.install("workbuddy2api-panel-main", GO_ENTRIES)
        self.assertFalse(result.get("ok"))
        self.assertIn("Go 网关源码包", result["error"])
        self.assertIn("Python 网关", result["error"])
        self.assertNotIn("root", result)
        self.assertEqual([], os.listdir(self.projects),
                         "被拒的 ZIP 不该在 projects/ 留下任何解压产物")

    def test_go_root_is_rejected_by_project_info_and_start(self):
        # 绕过 install 直接喂一个 Go 项目根（比如老版本留下的目录），describe()
        # 与 start() 也要给出同样的结论，而不是抛 ModuleNotFoundError。
        go_root = os.path.join(self.tmp.name, "panel")
        os.makedirs(os.path.join(go_root, "cmd", "server"))
        with open(os.path.join(go_root, "go.mod"), "w", encoding="utf-8") as fh:
            fh.write("module github.com/%s\n" % GO_REPO)
        with open(os.path.join(go_root, "cmd", "server", "main.go"), "w",
                  encoding="utf-8") as fh:
            fh.write("package main\nfunc main() {}\n")
        described = json.loads(self.shim.describe(go_root))
        self.assertFalse(described.get("ok"))
        self.assertIn("Go 网关源码包", described.get("error", ""))
        started = json.loads(self.shim.start(
            go_root, os.path.join(self.tmp.name, "data"), 8788, False))
        self.assertFalse(started.get("ok"))
        self.assertFalse(started.get("running"))
        self.assertIn("Go 网关源码包", started.get("error", ""))
        self.assertNotIn("wb_proxy", sys.modules,
                         "start() 不该为 Go 项目去 import Python 网关")

    def test_unknown_zip_names_the_python_marker_in_the_error(self):
        result = self.install("random-repo-main", {"README.md": "hello"})
        self.assertFalse(result.get("ok"))
        self.assertIn("wb_proxy.py", result["error"])


class ContractParityTests(unittest.TestCase):
    """三端契约不许各自漂移：路径、字段、端口、主题事件、源地址。"""

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
        cls.workflow_android = read(os.path.join(ROOT, ".github", "workflows",
                                                 "mobile-android.yml"))
        cls.workflow_ios = read(os.path.join(ROOT, ".github", "workflows",
                                             "mobile-ios.yml"))

    def test_python_marker_is_shared_by_shim_and_bridge(self):
        self.assertIn('MARKER = "wb_proxy.py"', self.shim)
        self.assertIn('private const val MARKER = "wb_proxy.py"', self.bridge)

    def test_panel_and_health_paths_match_on_all_platforms(self):
        # Android：panelBase/healthUrl 由 GatewayProfile 统一给出。
        for token in ('panelBase(context: Context, port: Int): String = '
                      'baseUrl(context, port) + "/"',
                      'healthUrl(context: Context, port: Int): String = '
                      'baseUrl(context, port) + "/health"'):
            self.assertIn(token, self.profile_kt, "形态路径缺失：%s" % token)
        # iOS：同一契约。
        self.assertIn('static var panelBase: String { base + "/" }',
                      self.profile_swift)
        self.assertIn('static var healthURL: String { base + "/health" }',
                      self.profile_swift)

    def test_health_fields_are_normalised_on_both_platforms(self):
        for token in ('json.optInt("accounts", 0)', 'json.optInt("accounts_ready", 0)'):
            self.assertIn(token, self.profile_kt)
        for token in ('json["accounts"] as? Int ?? 0',
                      'json["accounts_ready"] as? Int ?? 0'):
            self.assertIn(token, self.profile_swift)
        self.assertIn('normaliseHealth(json)', self.main)
        self.assertIn("GatewayProfile.normaliseHealth(json)", self.root_vc)

    def test_single_theme_channel_is_wired(self):
        # 深浅色只有一条通道：外壳把系统值用 wb:system-theme 事件派给面板。
        # Go 面板的 localStorage 注入脚本已随 Go 形态一起移除。
        for source in (self.main, self.root_vc):
            self.assertIn("wb:system-theme", source)
            self.assertNotIn("themeScript", source)
            self.assertNotIn("wb2api.theme", source)

    def test_remote_mode_exists_on_both_platforms(self):
        self.assertIn('KEY_REMOTE = "remote"', self.prefs)
        self.assertIn("settings_remote", self.strings)
        self.assertIn("static var remoteEnabled: Bool {", self.paths_swift)
        self.assertIn("远程网关接入…", self.root_vc)

    def test_two_import_entries_stay(self):
        # 两个导入入口（手动选 ZIP / 自动下载）不变；下载地址镜像优先、直连兜底。
        self.assertIn("MENU_PROJECT -> launchProjectImport()", self.main)
        self.assertIn("MENU_PROJECT_UPDATE -> startUpdate()", self.main)
        for mirror in ("v4.gh-proxy.org", "gh-proxy.com", "github.com/$REPO"):
            self.assertIn(mirror, self.bridge)
        self.assertIn(CONTRACT["repo"], self.bridge)

    def test_container_has_no_go_runtime_left(self):
        # 回退后的硬边界：三端源码与 CI 里不许再出现任何 Go 运行时。
        for source in (self.profile_kt, self.bridge, self.main, self.service,
                       self.prefs, self.profile_swift, self.root_vc,
                       self.paths_swift, self.shim, self.strings):
            for token in ("GoGateway", "GoBridge", "libgateway", "localKind",
                          "remoteKind", "isLocalGo", 'const val GO = "go"'):
                self.assertNotIn(token, source,
                                 "Go 运行时残留：%s in %s" % (token, source[:0] or "source"))
        for workflow in (self.workflow_android, self.workflow_ios):
            self.assertNotIn("setup-go", workflow)
            self.assertNotIn("build-go-gateway", workflow)

    def test_go_build_scripts_are_deleted(self):
        for path in ("mobile/android/scripts/build-go-gateway.sh",
                     "mobile/ios/scripts/build-go-gateway.sh",
                     "mobile/ios/scripts/prepare-go-bridge.py",
                     "mobile/android/app/src/main/java/com/onegateway/host/GoGateway.kt",
                     "mobile/ios/OneGateway/Sources/GoBridge.swift"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, path)),
                             "Go 构建产物应已删除：%s" % path)

    def test_default_port_is_8788_everywhere(self):
        self.assertIn("DEFAULT_PORT = 8788", self.prefs)
        self.assertIn("static let pythonDefaultPort = 8788", self.profile_swift)
        self.assertIn("DEFAULT_PORT_PYTHON = 8788", self.shim)

    def test_address_normalisation_supports_lan_and_https(self):
        # Android：无 kind 参数（只有一种形态）；IPv6 方括号后判定端口。
        self.assertIn("fun normaliseBase(raw: String): String", self.profile_kt)
        self.assertIn("substringAfterLast(']')", self.profile_kt)
        # iOS：同一契约。
        self.assertIn("static func normaliseBase(_ raw: String) -> String",
                      self.profile_swift)

    def test_empty_shell_overlay_only_when_nothing_is_available(self):
        # 真机实测过覆盖层误判：空壳 = 没开远程 且 没导入项目；两者任一成立就
        # 直接显示面板。自动启动与渲染走同一个判定。
        self.assertIn("GatewayProfile.isRemote(this) || "
                      "GatewayBridge.projectRoot(this) != null", self.main)
        self.assertIn("if (GatewayBridge.projectRoot(context) == null) {", self.service)


class AndroidPortHopTests(unittest.TestCase):
    """「端口被占用时自动顺延」：默认关；开启后 +1 递增探测并写回。"""

    @classmethod
    def setUpClass(cls):
        cls.prefs = read(os.path.join(JAVA, "Prefs.kt"))
        cls.main = read(os.path.join(JAVA, "MainActivity.kt"))
        cls.service = read(os.path.join(JAVA, "GatewayService.kt"))
        cls.strings = read(STRINGS)

    def test_port_hop_is_opt_in_and_writes_the_effective_port_back(self):
        # 真机场景：8788 被 Termux 里的另一份网关占着。默认关（行为不变，
        # 报「端口被占用」让人手动处理）；开启后从预设端口 +1 递增探测，
        # 最多 10 个，实际生效端口写回 Prefs，状态栏/面板/复制地址跟着走。
        self.assertIn('KEY_PORT_HOP = "port-hop"', self.prefs)
        self.assertIn("PORT_HOP_LIMIT = 10", self.prefs)
        self.assertIn("fun portHop(context: Context): Boolean =", self.prefs)
        self.assertIn("getBoolean(KEY_PORT_HOP, false)", self.prefs)
        self.assertIn("Prefs.setPortHop(this, portHopSwitch.isChecked)", self.main)
        self.assertIn('getString(R.string.settings_port_hop)', self.main)
        self.assertIn("端口被占用时自动顺延（+1 递增，最多 10 个）", self.strings)
        # 顺延在 Service 的启动路径上统一做：探 TCP（连得上=被占），写回端口。
        self.assertIn("private fun resolvePort(context: Context): Int", self.service)
        self.assertIn("val port = resolvePort(context)", self.service)
        self.assertIn("while (tcpBusy(port) && hops < Prefs.PORT_HOP_LIMIT)", self.service)
        self.assertIn("if (port != preferred) Prefs.setPort(context, port)", self.service)
        self.assertIn('socket.connect(java.net.InetSocketAddress("127.0.0.1", port), 400)',
                      self.service)


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
        with tempfile.TemporaryDirectory(prefix="container-sync-") as tmp:
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
        self.assertIn("Python 网关", proc.stderr)


class IosContainerTests(unittest.TestCase):
    """iOS 侧容器契约：桥接头只剩 CPython，端口顺延与 Android 同语义。"""

    @classmethod
    def setUpClass(cls):
        cls.core_paths = read(os.path.join(SWIFT, "CorePaths.swift"))
        cls.project = read(os.path.join(ROOT, "mobile", "ios", "project.yml"))
        cls.header = read(os.path.join(SWIFT, "OneGateway-Bridging-Header.h"))
        cls.root_vc = read(os.path.join(SWIFT, "RootViewController.swift"))

    def test_bridging_header_only_exposes_cpython(self):
        self.assertIn('#include <Python/Python.h>', self.header)
        self.assertNotIn("libgateway", self.header)

    def test_xcode_links_nothing_go(self):
        self.assertNotIn("-lgateway", self.project)
        self.assertNotIn("GoGateway", self.project)

    def test_no_local_kind_menu_is_left(self):
        self.assertNotIn("本机网关形态", self.root_vc)
        self.assertNotIn("showLocalKindSettings", self.root_vc)
        self.assertNotIn("switchToLocal(kind:", self.root_vc)

    def test_port_hop_is_opt_in_on_ios_too(self):
        # 与 Android 同一开关语义：默认关；开启后 effectivePort() 从请求端口
        # 开始 POSIX connect 探测，被占 +1 递增（上限 10），生效值写回
        # Settings.port。Python 启动与重启两个路径都要走它。
        self.assertIn('private static let portHopKey = "hub.portHop"', self.core_paths)
        self.assertIn("static let portHopLimit = 10", self.core_paths)
        self.assertIn("static var portHop: Bool {", self.core_paths)
        self.assertIn("static func effectivePort(from requested: Int? = nil) -> Int",
                      self.core_paths)
        self.assertIn("guard portHop else { return preferred }", self.core_paths)
        self.assertIn("if candidate != preferred { port = candidate }", self.core_paths)
        # 回环探测：连接 127.0.0.1，连得上 = 被占。
        self.assertIn("enum PortProbe", self.core_paths)
        self.assertIn("INADDR_LOOPBACK", self.core_paths)
        self.assertIn("connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))",
                      self.core_paths)
        for token in ("Settings.effectivePort(from: port)",      # restartGateway
                      "Settings.effectivePort()",                # Python 启动
                      "开启端口自动顺延（被占时 +1）"):
            self.assertIn(token, self.root_vc)


class LiveArchiveTests(unittest.TestCase):
    """可选：真下仓库 ZIP 再跑一次识别。默认离线，CI 不联网。"""

    def setUp(self):
        if os.environ.get("WB_LIVE_GATEWAY_CONTAINER") != "1":
            self.skipTest("设置 WB_LIVE_GATEWAY_CONTAINER=1 才会下载真实仓库 ZIP")

    def test_real_python_archive_is_recognised_and_go_is_rejected(self):
        shim = load_shim()
        sources = (
            ("python", "workbuddy2api-hub-main", PYTHON_REPO, True),
            ("go", "workbuddy2api-panel-main", GO_REPO, False),
        )
        with tempfile.TemporaryDirectory(prefix="container-live-") as tmp:
            for tag, outer, repo, should_pass in sources:
                url = ("https://v4.gh-proxy.org/https://github.com/%s/"
                       "archive/refs/heads/main.zip" % repo)
                archive = os.path.join(tmp, tag + ".zip")
                request = urllib.request.Request(
                    url, headers={"User-Agent": "OneGateway-container"})
                with urllib.request.urlopen(request, timeout=180) as response:
                    data = response.read()
                self.assertEqual(b"PK", data[:2], "%s 下载到的不是 ZIP" % repo)
                with open(archive, "wb") as fh:
                    fh.write(data)
                result = shim.install(archive, os.path.join(tmp, "projects"))
                if should_pass:
                    self.assertTrue(result.get("ok"), "%s: %s" % (repo, result))
                    self.assertEqual("python", result["kind"])
                    self.assertEqual(CONTRACT["port"], result["default_port"])
                else:
                    self.assertFalse(result.get("ok"))
                    self.assertIn("Go 网关源码包", result.get("error", ""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
