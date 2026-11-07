"""容器同时兼容两种外部网关：导入识别 + 三端接入契约 + 本机运行边界。

这个仓库不含网关实现，但用户能导入两种完全不同的外部项目：

  * Python 型（ardeyouxipianyi/workbuddy2api-hub）：wb_proxy.py + dashboard.html，
    由 App 内嵌 CPython 本地托管，ZIP 可以热更新；
  * Go 型（misakano7545/workbuddy2api-panel）：go.mod + cmd/server，单二进制。
    Android 10+ 禁止从可写的应用私有目录 exec 未签名 ELF，iOS 只允许运行
    签名进包的代码，所以它**在构建期**从外部源码编译、随安装包签名：
    Android 是 jniLibs/<abi>/libgateway.so（GoGateway 从 nativeLibraryDir 起进程），
    iOS 是 -buildmode=c-archive 的 libgateway.a（GoBridge 在进程内起 goroutine）。
    ZIP 只用来确认形态与版本，不能替换二进制；Go 版本与 App 版本绑定。

「尽最大可能兼容」的落点是：两种 ZIP 都能被同一个 install() 认出来、两个导入
入口与交互不变、三端（Python 薄运行时 / Android Kotlin / iOS Swift）共用同一套
地址与字段契约、深浅色两套主题脚本互不干扰、健康字段归一化。远程接入仍保留，
但从「Go 的唯一路径」降级为可选连接方式。

分三层钉住：
  1. 行为：真的造两份同构 ZIP 喂给 wb_hub_env.install()，看形态/版本/端口；
  2. 契约：三端源码里的路径、字段、主题键、状态码逐项对齐，不许哪一端偷偷
     退回「127.0.0.1/health」的硬编码；
  3. 边界：下载的可执行代码绝不进运行时；跑的是随包签名的那一份。源码 ZIP
     只能被 CPython 薄运行时识别，绝不交给它执行。

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
        # 现在两端都在本机运行：构建期编译成随包签名的二进制，ZIP 只用于识别形态。
        "repo": GO_REPO, "local": True,
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

    def test_go_project_is_never_run_by_cpython(self):
        # Go 型现在也在手机里跑，但跑的是随包签名的二进制，不是 CPython。
        # 薄运行时依旧必须拒绝它，否则 import wb_proxy 会抛看不懂的错误。
        result = self.install("workbuddy2api-panel-main", GO_ENTRIES)
        state = json.loads(self.shim.start(
            result["root"], os.path.join(self.tmp.name, "data"), 8788, False))
        self.assertFalse(state.get("ok"))
        self.assertFalse(state.get("running"))
        self.assertIn("GoGateway", state.get("error", ""))
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

    def test_go_zip_keeps_the_shell_in_local_mode(self):
        # 导入 Go ZIP 不再把用户踢到远程接入：本机运行随包内置的二进制。
        self.assertIn('result.optString("kind").ifBlank', self.bridge)
        self.assertIn("Prefs.setRemote(context, false)", self.bridge)
        self.assertIn("Prefs.setRemoteKind(context, GatewayProfile.GO)", self.bridge)
        self.assertIn("GatewayProfile.isLocalGo(context)", self.bridge)
        self.assertIn("GoGateway.start(context, port, lan)", self.bridge)
        self.assertIn('name="project_go_remote_hint"', self.strings)
        self.assertIn("随安装包签名的内置 Go 网关", self.strings)

    def test_local_kind_can_be_switched_without_importing_a_zip(self):
        # 两端都要能直接选「本机跑 Python 还是 Go」：Go 是随包签名的二进制，
        # 不该为了切形态先下载一份 2MB 源码 ZIP。
        self.assertIn("fun localKind(context: Context)", self.prefs)
        self.assertIn("Prefs.setLocalKind(context, kind)", self.bridge)
        self.assertIn("Prefs.localKind(context) == GO", self.profile_kt)
        self.assertIn('name="settings_local_kind"', self.strings)
        self.assertIn("localKindSwitch", self.main)
        self.assertIn("var localKind: String", self.paths_swift)
        self.assertIn("Settings.localKind == go", self.profile_swift)
        self.assertIn("本机网关形态", self.root_vc)
        # 没有导入任何 ZIP 时，前台服务也要允许起内置 Go 网关。
        self.assertIn("!GatewayProfile.isLocalGo(context) && GatewayBridge.projectRoot(context) == null",
                      self.service)

    def test_import_still_switches_the_local_kind(self):
        # 兼容旧行为：导入一份形态明确的 ZIP 仍然等价于切换本机形态。
        self.assertIn("Prefs.setProjectKind(context, kind)", self.bridge)
        self.assertIn("Prefs.setLocalKind(context, kind)", self.bridge)

    def test_ios_build_diagnoses_a_go_core_zip(self):
        # Go 源码包不是 Python 核心；诊断要指向构建期的 c-archive，而不是「远程接入」。
        self.assertIn('os.path.basename(n) == "go.mod"', self.sync_core)
        self.assertIn("build-go-gateway.sh", self.sync_core)
        self.assertIn("本机网关形态", self.sync_core)
        self.assertNotIn("连到已运行的 Go 实例", self.sync_core)

    def test_container_never_executes_a_downloaded_binary(self):
        # 平台硬限制：下载/解压到可写目录的 ELF 永远不能被 exec。
        # 薄运行时、界面、前台服务都不碰进程；只有 GoGateway 能从只读的
        # nativeLibraryDir 启动随包签名的那一份。
        for source in (self.shim, self.main, self.service, self.root_vc,
                       read(os.path.join(SWIFT, "GoBridge.swift"))):
            for forbidden in ("subprocess", "os.execv", "posix_spawn",
                              "Runtime.getRuntime().exec", "dlopen("):
                self.assertNotIn(forbidden, source)
        go = read(os.path.join(JAVA, "GoGateway.kt"))
        self.assertIn("nativeLibraryDir", go)
        self.assertIn('ProcessBuilder(bin.absolutePath, "-config"', go)
        self.assertNotIn("projectsDir", go)
        self.assertNotIn("cacheDir", go)

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


class LocalGoRuntimeTests(unittest.TestCase):
    """Android：Go 网关随包签名，构建期编译、运行时只从只读目录启动。"""

    @classmethod
    def setUpClass(cls):
        cls.script = read(os.path.join(ROOT, "mobile", "android", "scripts",
                                       "build-go-gateway.sh"))
        cls.gradle = read(os.path.join(ROOT, "mobile", "android", "app",
                                       "build.gradle.kts"))
        cls.workflow = read(os.path.join(ROOT, ".github", "workflows",
                                         "mobile-android.yml"))
        cls.go = read(os.path.join(JAVA, "GoGateway.kt"))
        cls.bridge = read(os.path.join(JAVA, "GatewayBridge.kt"))
        cls.gitignore = read(os.path.join(ROOT, ".gitignore"))

    def test_build_script_cross_compiles_both_abis(self):
        for token in ("CGO_ENABLED=0", "GOOS=android", "GOARCH=\"$arch\"",
                      "./cmd/server", "libgateway.so", "arm64-v8a", "x86_64"):
            self.assertIn(token, self.script)
        self.assertIn("build_abi arm64-v8a arm64 b7", self.script)
        self.assertIn("build_abi x86_64 amd64 3e", self.script)

    def test_build_script_verifies_pie_elf(self):
        # Android 只接受 PIE；e_type=2 (ET_EXEC) 会在运行时被内核拒绝。
        self.assertIn("e_type", self.script)
        self.assertIn("PIE", self.script)

    def test_x86_64_uses_ndk_external_linking(self):
        # Go 的内部链接器不给 android/amd64 产 PIE：
        # "android/amd64 requires external (cgo) linking, but cgo is not enabled"
        # 实测 CI 在第二个 ABI 上挂过一次，这里把外部链接路径钉住。
        for token in ("CGO_ENABLED=1", "ndk_clang", "ANDROID_NDK_HOME",
                      "ANDROID_NDK_LATEST_HOME", "linux-android26-clang",
                      "x86_64", "CC=\"$cc\""):
            self.assertIn(token, self.script)
        self.assertIn("ndk_clang x86_64", self.script)

    def test_gradle_extracts_native_libs(self):
        # 不解压就没有可 exec 的文件路径，nativeLibraryDir 会是空的。
        self.assertIn("useLegacyPackaging = true", self.gradle)
        self.assertIn("jniLibs", self.gradle)

    def test_ci_builds_the_binary_before_assembling(self):
        self.assertIn("setup-go@v5", self.workflow)
        self.assertIn("build-go-gateway.sh", self.workflow)
        self.assertLess(
            self.workflow.index("Build embedded Go gateway binaries"),
            self.workflow.index("- name: Build APK"))
        self.assertIn("libgateway.so", self.workflow)

    def test_binary_is_never_checked_in(self):
        for path in ("mobile/android/app/src/main/jniLibs/",
                     "mobile/ios/OneGateway/GoGateway/*.a",
                     "mobile/ios/OneGateway/GoGateway/*.h"):
            self.assertIn(path, self.gitignore)

    def test_runtime_config_uses_sandbox_paths_and_no_self_update(self):
        for token in ("nativeLibraryDir", "auth_dir", "state_file", "api_key",
                      "listen", "auto_enabled", "balance_refresh_enabled"):
            self.assertIn(token, self.go)
        self.assertIn('File(dir, "data").mkdirs()', self.go)
        self.assertIn("File(dir, \"auths\").mkdirs()", self.go)

    def test_service_lifecycle_is_routed_to_the_go_process(self):
        for action in ("GoGateway.start", "GoGateway.stop", "GoGateway.status",
                       "GoGateway.logTail"):
            self.assertIn(action, self.bridge)


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
        self.assertIn("build-go-gateway.sh", proc.stderr)
        self.assertIn("本机网关形态", proc.stderr)
        self.assertNotIn("远程网关接入", proc.stderr)


#: 与上游 cmd/server/main.go 同构的最小样本，覆盖脚本的全部五处替换目标。
#: 上游一改结构，下面这个用例会先于 macOS CI 报出来。
SYNTHETIC_GO_MAIN = '''package main

import (
\t"context"
\t"log"
\t"os"
\t"os/signal"
\t"syscall"
)

func main() {
\tif err := load(); err != nil {
\t\tlog.Fatalf("boom: %v", err)
\t\tos.Exit(1)
\t}
\tctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
\tdefer stop()
\t_ = ctx
\t_ = syscall.EBUSY
}
'''


class IosLocalGoRuntimeTests(unittest.TestCase):
    """iOS：Go 网关在构建期编成 c-archive 静态库，随包签名、进程内运行。

    本机是 Windows，编不了 c-archive，所以这里两头一起钉：
      * 构建脚本与 Xcode 接线（静态断言，CI 会真的跑一遍）；
      * prepare-go-bridge.py 的源码变换（真跑一遍，替身 main.go 全量断言）。
    """

    @classmethod
    def setUpClass(cls):
        cls.build_script = read(os.path.join(ROOT, "mobile", "ios", "scripts",
                                             "build-go-gateway.sh"))
        cls.prepare_script = read(os.path.join(ROOT, "mobile", "ios", "scripts",
                                               "prepare-go-bridge.py"))
        cls.prepare_path = os.path.join(ROOT, "mobile", "ios", "scripts",
                                        "prepare-go-bridge.py")
        cls.bridge_swift = read(os.path.join(SWIFT, "GoBridge.swift"))
        cls.project = read(os.path.join(ROOT, "mobile", "ios", "project.yml"))
        cls.header = read(os.path.join(SWIFT, "OneGateway-Bridging-Header.h"))
        cls.root_vc = read(os.path.join(SWIFT, "RootViewController.swift"))
        cls.workflow = read(os.path.join(ROOT, ".github", "workflows",
                                         "mobile-ios.yml"))

    def test_build_script_uses_c_archive_for_ios(self):
        for token in ("CGO_ENABLED=1", "GOOS=ios", "GOARCH=arm64",
                      "-buildmode=c-archive", "xcrun --sdk iphoneos",
                      "libgateway.a", "libgateway.h", "prepare-go-bridge.py",
                      "./cmd/server", "StartGateway", "StopGateway"):
            self.assertIn(token, self.build_script)
        # 三个下载地址与 Android 一致，镜像优先、直连兜底。
        self.assertIn("v4.gh-proxy.org", self.build_script)
        self.assertIn(GO_REPO, self.build_script)

    def test_prepare_bridge_rewrites_the_cli_entry_point(self):
        with tempfile.TemporaryDirectory(prefix="dual-go-bridge-") as tmp:
            server = os.path.join(tmp, "cmd", "server")
            os.makedirs(server)
            entry = os.path.join(server, "main.go")
            with open(entry, "w", encoding="utf-8") as fh:
                fh.write(SYNTHETIC_GO_MAIN)

            proc = subprocess.run([sys.executable, self.prepare_path, tmp],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(0, proc.returncode, proc.stderr)
            changed = read(entry)
            for token in ("func runGateway() {", "fatalf(",
                          'panic("self-update is disabled inside the app")',
                          "ctx, stop := gatewayContext()"):
                self.assertIn(token, changed)
            for token in ("log.Fatalf(", "os.Exit(1)", '"os/signal"',
                          "signal.NotifyContext"):
                self.assertNotIn(token, changed)

            generated = read(os.path.join(server, "mobile_bridge.go"))
            for token in ("//export StartGateway", "//export StopGateway",
                          'import "C"', "gwDone", "recover()",
                          "flag.CommandLine", "runGateway()"):
                self.assertIn(token, generated)
            # c-archive 不会调用 main，但 runtime 仍引用 main.main：
            # 少了空壳会报 "function main is undeclared in the main package"。
            self.assertIn("func main() {}", generated)

            # 上游结构变了就必须失败，不能悄悄产出一个跑不起来的库。
            again = subprocess.run([sys.executable, self.prepare_path, tmp],
                                   capture_output=True, text=True, timeout=30)
            self.assertNotEqual(0, again.returncode)
            self.assertIn("期望恰好 1 处", again.stderr)

    def test_bridge_template_serializes_restarts_and_recovers(self):
        # stop→start 时旧的 ListenAndServe 还没释放端口：必须等上一轮 done，
        # 否则新监听 bind 失败 -> fatalf -> panic，表现成「重启后面板一直转圈」。
        for token in ("gwDone", "<-prev", "gwStopPending", "recover()",
                      "gatewayContext()", "flag.NewFlagSet"):
            self.assertIn(token, self.prepare_script)
        self.assertIn("//export StopGateway", self.prepare_script)

    def test_swift_bridge_calls_the_c_entry_points(self):
        self.assertIn("StartGateway(cPath)", self.bridge_swift)
        self.assertIn("StopGateway()", self.bridge_swift)
        self.assertIn("CorePaths.resolve().data", self.bridge_swift)
        for token in ("listen", "api_key", "auth_dir", "state_file",
                      "auto_enabled", "balance_refresh_enabled", "logHint"):
            self.assertIn(token, self.bridge_swift)
        # iOS 不能起子进程：桥接层只调 C 函数。
        for forbidden in ("Process(", "posix_spawn", "dlopen(", "fork("):
            self.assertNotIn(forbidden, self.bridge_swift)

    def test_xcode_links_the_static_library(self):
        for token in ("HEADER_SEARCH_PATHS", "LIBRARY_SEARCH_PATHS",
                      "-lgateway", "$(SRCROOT)/OneGateway/GoGateway"):
            self.assertIn(token, self.project)
        self.assertIn('#include <Python/Python.h>', self.header)
        self.assertIn('#include "libgateway.h"', self.header)

    def test_local_go_lifecycle_is_wired_in_the_view_controller(self):
        for token in ("GatewayProfile.isLocalGo", "startLocalGo(", "stopLocalGo()",
                      "switchToLocal(kind:", "showLocalKindSettings()",
                      "GoBridge.shared.logHint", "本机运行（随包内置）",
                      "healthFailures = 0"):
            self.assertIn(token, self.root_vc)
        self.assertNotIn("Go 单二进制不能在 iOS App 内执行", self.root_vc)

    def test_ios_ci_builds_the_archive_before_xcode(self):
        self.assertIn("setup-go@v5", self.workflow)
        self.assertIn("1.26.10", self.workflow)
        self.assertIn("build-go-gateway.sh", self.workflow)
        self.assertIn("nm -g", self.workflow)
        self.assertLess(
            self.workflow.index("Build embedded Go gateway library"),
            self.workflow.index("- name: Generate Xcode project"))
        self.assertLess(
            self.workflow.index("- name: Generate Xcode project"),
            self.workflow.index("- name: Build (unsigned)"))


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
