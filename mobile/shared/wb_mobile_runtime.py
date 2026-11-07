"""移动端（iOS / Android）运行时外壳：原样运行导入的网关项目。

设计边界（重要）：
* 本文件**不含任何网关业务逻辑**——账号池、调度、计费、面板、上游转发全部由
  同目录的 `wb_proxy.py` 及其模块提供——那份代码来自用户导入的外部项目 ZIP，
  不在本仓库里。这里只负责「让网关能在 App 沙盒里跑起来」。
* 平台差异（前台服务、后台挂起、通知、WebView）由各平台原生外壳处理，这里只
  暴露 start / stop / status / import_credential / log_tail 五个动作。

启动流程与导入项目的 `wb_proxy.main()` 逐步对齐（顺序不能改）：
    _parse_cli_args → _apply_cli_overrides → _bootstrap_runtime
    → _report_first_run → _log_startup_summary → GatewayServer.serve_forever()

两处刻意的差异：
1. serve_forever 跑在守护线程里（原生外壳需要主线程）；
2. bootstrap 只做一次。停止后再启动只重建监听套接字，不重复拉起调度器、
   计费刷新、更新检查等常驻线程——移动端在前后台之间反复暂停/恢复时，
   重复 bootstrap 会成倍堆积后台线程。
"""

import hashlib
import json
import os
import sys
import threading
import time
import traceback

#: 随包携带、需要同步到可写目录的文件（网关核心 + 面板静态页 + 本文件）。
CORE_HTML = "dashboard.html"
CORE_PREFIX = "wb_"
MANIFEST_NAME = ".core-manifest.json"
LOG_NAME = "gateway.log"

_STATE_LOCK = threading.RLock()
_SERVER = None
_THREAD = None
_ARGS = None
_BOOTED = False
_ERROR = ""
_STARTED_AT = 0.0
_HOME = None
_CORE = None
_LOGS = None
_LOG_FH = None
_WB = None


# --------------------------------------------------------------------------
# 目录与运行环境
# --------------------------------------------------------------------------
def _core_files(src_dir):
    """列出核心文件名：wb_*.py + dashboard.html（与导入 ZIP 的口径一致）。"""
    names = []
    try:
        entries = sorted(os.listdir(src_dir))
    except OSError:
        return names
    for name in entries:
        if name == CORE_HTML or (name.startswith(CORE_PREFIX) and name.endswith(".py")):
            names.append(name)
    return names


def _sha1(path):
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sync_core(src_dir, dst_dir, force=False):
    """把只读包体内的核心源码同步到可写目录。

    包体（APK 的 assets / iOS 的 .app）是只读的，而 `wb_proxy` 会按 `__file__`
    的目录去找 `dashboard.html`、`catalog/`、`usage/`，所以必须先落一份到沙盒里
    可写的位置，再把该目录放进 sys.path。返回 (变更文件数, 清单)。
    """
    src_dir = os.path.abspath(src_dir)
    dst_dir = os.path.abspath(dst_dir)
    os.makedirs(dst_dir, exist_ok=True)
    current = {}
    for name in _core_files(src_dir):
        current[name] = _sha1(os.path.join(src_dir, name))

    manifest_path = os.path.join(dst_dir, MANIFEST_NAME)
    previous = {}
    if not force and os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                previous = json.load(fh).get("files") or {}
        except Exception:
            previous = {}

    changed = 0
    for name, digest in current.items():
        target = os.path.join(dst_dir, name)
        if previous.get(name) == digest and os.path.exists(target):
            continue
        tmp = target + ".tmp"
        with open(os.path.join(src_dir, name), "rb") as src, open(tmp, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
        os.replace(tmp, target)
        changed += 1

    # 版本升级后消失的旧模块要清掉，否则 sys.path 上会残留过期代码。
    for name in list(previous):
        if name not in current:
            try:
                os.remove(os.path.join(dst_dir, name))
                changed += 1
            except OSError:
                pass

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"files": current, "synced_at": time.time()}, fh, ensure_ascii=False, indent=2)
    return changed, current


class _Tee:
    """把输出同时写给原流与日志文件。

    设备上 stderr 通常存在（Android 指向 logcat、iOS 指向系统日志），但用户看不到，
    而面板外的「查看启动日志」需要读文件。所以不做「有 stderr 就不落盘」的取舍，
    一律双写：logcat 照旧，日志页也有内容。
    """

    def __init__(self, primary, secondary):
        self._primary = primary
        self._secondary = secondary

    def write(self, data):
        for stream in (self._primary, self._secondary):
            try:
                stream.write(data)
                stream.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for stream in (self._primary, self._secondary):
            try:
                stream.flush()
            except Exception:
                pass

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("fileno is not available on the mobile log stream")


def _bind_stdio(log_path):
    """把 stdout/stderr 接到日志文件（必要时替换缺失的流）。"""
    global _LOG_FH
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        _LOG_FH = open(log_path, "a", encoding="utf-8", buffering=1)
    except Exception:
        return
    for name in ("stdout", "stderr"):
        current = getattr(sys, name, None)
        if current is None or not hasattr(current, "write"):
            setattr(sys, name, _LOG_FH)
        else:
            setattr(sys, name, _Tee(current, _LOG_FH))


def _bind_trust_store(home_dir):
    """配置 HTTPS 根证书。

    CPython 在移动端没有可用的默认 CA 路径：iOS 没有 /etc/ssl，Android 的证书也
    不在 OpenSSL 的查找位置，不处理就会让每一次上游请求报
    CERTIFICATE_VERIFY_FAILED。优先用随包安装的 certifi（iOS 构建时装入），
    其次退回系统证书目录（Android 的 cacerts 目录就是 OpenSSL 认的散列布局）。
    """
    if os.environ.get("SSL_CERT_FILE"):
        return "preset"
    try:
        import certifi  # noqa: F401  （iOS 构建时随包装入，Android 上通常不存在）
        bundle = certifi.where()
        if bundle and os.path.exists(bundle):
            os.environ["SSL_CERT_FILE"] = bundle
            os.environ["REQUESTS_CA_BUNDLE"] = bundle
            return "certifi"
    except Exception:
        pass
    for candidate in ("/system/etc/security/cacerts",          # Android 系统根证书
                      "/apex/com.android.conscrypt/cacerts",   # Android 14+
                      "/etc/ssl/certs"):
        if os.path.isdir(candidate):
            os.environ["SSL_CERT_DIR"] = candidate
            return candidate
    return "default"


def configure(home, core, logs=None, force=False):
    """准备沙盒目录、同步核心源码、固定环境变量。

    必须在 `import wb_proxy` 之前调用：`wb_proxy` 在模块级按这些环境变量算出
    ACCOUNTS_DIR / USAGE_DIR / CATALOG_DIR，导完再改就晚了。
    """
    global _HOME, _CORE, _LOGS
    with _STATE_LOCK:
        home = os.path.abspath(home)
        core_src = os.path.abspath(core)
        _HOME = home
        _CORE = os.path.join(home, "core")
        _LOGS = os.path.abspath(logs or os.path.join(home, "logs"))
        for sub in ("accounts", "usage", "catalog", "logs", "core"):
            os.makedirs(os.path.join(home, sub), exist_ok=True)
        os.makedirs(_LOGS, exist_ok=True)

        changed, files = sync_core(core_src, _CORE, force=force)
        _bind_stdio(os.path.join(_LOGS, LOG_NAME))

        os.environ["ACCOUNTS_DIR"] = os.path.join(home, "accounts")
        os.environ["WB_PROXY_USAGE_DIR"] = os.path.join(home, "usage")
        os.environ["WB_PROXY_CATALOG_DIR"] = os.path.join(home, "catalog")
        os.environ["WB_APP_ROOT"] = home
        os.environ.setdefault("PYTHONUNBUFFERED", "1")
        # App 沙盒里的 HOME 与桌面端不同（iOS 是 App 容器、Android 是 /data/user/0/...），
        # 统一指到数据目录：桌面凭证扫描、缓存与相对路径都落在可写位置。
        os.environ["HOME"] = home
        os.environ["TMPDIR"] = os.path.join(home, "tmp")
        os.makedirs(os.environ["TMPDIR"], exist_ok=True)
        trust = _bind_trust_store(home)

        if _CORE not in sys.path:
            sys.path.insert(0, _CORE)
        return {"home": home, "core": _CORE, "logs": _LOGS,
                "synced": changed, "files": sorted(files), "trust": trust}


def _ensure_configured():
    if _HOME is None:
        raise RuntimeError("wb_mobile_runtime.configure() must be called first")


def _gateway():
    """惰性导入网关主模块（导入即会按环境变量算好各数据目录）。"""
    global _WB
    if _WB is None:
        import wb_proxy as gateway
        _WB = gateway
    return _WB


# --------------------------------------------------------------------------
# 生命周期
# --------------------------------------------------------------------------
def _status_locked():
    gateway = _WB
    host = getattr(_ARGS, "host", None) or os.environ.get("HOST") or "127.0.0.1"
    port = int(getattr(_ARGS, "port", None) or os.environ.get("PORT") or 8788)
    accounts = 0
    usable = 0
    pool = getattr(gateway, "POOL", None) if gateway else None
    if pool is not None:
        try:
            accounts = len(pool.accounts)
            usable = pool.count_ready()
        except Exception:
            pass
    try:
        version = gateway.running_version() if gateway else ""
    except Exception:
        version = ""
    return {
        "ok": not _ERROR,
        "running": _SERVER is not None,
        "host": host,
        "port": port,
        "url": "http://%s:%d/" % ("127.0.0.1" if host == "0.0.0.0" else host, port),
        "apiBase": "http://%s:%d/v1" % ("127.0.0.1" if host == "0.0.0.0" else host, port),
        "version": version,
        "accounts": accounts,
        "usable": usable,
        "home": _HOME or "",
        "log": os.path.join(_LOGS, LOG_NAME) if _LOGS else "",
        "error": _ERROR,
        "since": _STARTED_AT,
    }


def status():
    with _STATE_LOCK:
        return _status_locked()


def start(port=8788, host="127.0.0.1", lan=False, api_key=None, panel_password=None):
    """启动（或恢复）网关监听，返回状态字典。重复调用是幂等的。"""
    global _SERVER, _THREAD, _ARGS, _BOOTED, _ERROR, _STARTED_AT
    with _STATE_LOCK:
        if _SERVER is not None:
            return _status_locked()
        _ERROR = ""
        try:
            _ensure_configured()
            gateway = _gateway()
            if lan and host == "127.0.0.1":
                host = "0.0.0.0"
            argv = [sys.argv[0] if sys.argv else "wb_proxy.py",
                    "--host", str(host), "--port", str(int(port)),
                    "--accounts-dir", os.path.join(_HOME, "accounts"),
                    "--usage-dir", os.path.join(_HOME, "usage")]
            if lan:
                argv.append("--lan")
            if api_key:
                argv += ["--api-key", str(api_key)]
            if panel_password:
                argv += ["--panel-password", str(panel_password)]
            saved_argv = sys.argv
            sys.argv = argv
            try:
                args = gateway._parse_cli_args()
                gateway._apply_cli_overrides(args)
            finally:
                sys.argv = saved_argv

            if not _BOOTED:
                generated = gateway._bootstrap_runtime(args)
                gateway._report_first_run(args)
                gateway._log_startup_summary(args, generated)
                _BOOTED = True
            else:
                # 恢复监听时走同一套「有没有账号」的提示，别让面板空着也不说话。
                gateway._report_first_run(args)

            server = gateway.GatewayServer((args.host, args.port), gateway.Handler)
            thread = threading.Thread(target=server.serve_forever,
                                      name="wb-gateway", daemon=True)
            thread.start()
            _SERVER, _THREAD, _ARGS = server, thread, args
            _STARTED_AT = time.time()
        except SystemExit as exc:  # 端口被占等致命错误由 wb_proxy 用 SystemExit 表达
            _ERROR = str(exc.code or "gateway refused to start")
            _SERVER = None
        except OSError as exc:
            _ERROR = "listen failed: %s" % exc
            _SERVER = None
        except Exception as exc:
            _ERROR = "%s: %s" % (type(exc).__name__, exc)
            _SERVER = None
            try:
                _traceback_to_log()
            except Exception:
                pass
        return _status_locked()


def _traceback_to_log():
    text = traceback.format_exc()
    try:
        with open(os.path.join(_LOGS, LOG_NAME), "a", encoding="utf-8") as fh:
            fh.write("\n[mobile] gateway bootstrap failed\n" + text + "\n")
    except Exception:
        pass


def stop():
    """关闭监听（保留已 bootstrap 的后台线程，便于快速恢复）。"""
    global _SERVER, _THREAD
    with _STATE_LOCK:
        server = _SERVER
        _SERVER = None
        thread = _THREAD
        _THREAD = None
    if server is not None:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)
    return status()


# 语义清晰的别名：iOS 从后台回到前台时只是恢复监听，不需要重启整个进程。
resume = start
pause = stop


def restart(port=None, host=None, lan=None, api_key=None):
    stop()
    with _STATE_LOCK:
        current_port = port if port is not None else int(getattr(_ARGS, "port", 8788) or 8788)
        current_host = host if host is not None else (getattr(_ARGS, "host", "127.0.0.1") or "127.0.0.1")
        current_lan = lan if lan is not None else current_host == "0.0.0.0"
    return start(port=current_port, host="127.0.0.1" if not current_lan else "0.0.0.0",
                 lan=current_lan, api_key=api_key)


def import_credential(path):
    """把外部 `.info` / JSON 凭证文件导入账号池（面板登录流程之外的补充入口）。"""
    with _STATE_LOCK:
        if not _BOOTED or _WB is None or getattr(_WB, "POOL", None) is None:
            return {"ok": False, "error": "gateway not started"}
        try:
            account = _WB.POOL.import_desktop_credential(path=path, source="mobile-file")
        except Exception as exc:
            return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        if account is None:
            return {"ok": False, "error": "no account in file"}
        uid = getattr(account, "uid", "") or ""
        return {"ok": True, "uid": uid, "nickname": getattr(account, "nickname", "") or "",
                "accounts": len(_WB.POOL.accounts)}


def log_tail(lines=200):
    """读启动日志尾部，供原生外壳的「诊断」页展示。"""
    path = os.path.join(_LOGS, LOG_NAME) if _LOGS else ""
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.readlines()
        return "".join(data[-max(1, int(lines)):])
    except Exception as exc:
        return "log unavailable: %s" % exc


def health(timeout=2.0):
    """直连本机 /health，确认监听真的在应答（面板首屏前用）。"""
    import urllib.request
    state = status()
    url = "http://127.0.0.1:%d/health" % state["port"]
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
