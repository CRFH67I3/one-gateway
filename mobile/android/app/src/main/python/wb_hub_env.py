"""Android 容器运行时：App 只提供 Python 环境，网关源码由用户导入的 ZIP 提供。

和旧版的区别只有「core 从哪来」这一处：

    旧：APK assets/core（构建时把仓库根目录的 wb_*.py + dashboard.html 打进去）
    新：用户从 GitHub 下载仓库 ZIP → 在 App 里导入 → 解压到沙盒 → 以解压出的
        项目根目录作为 core

因此升级网关不再需要重装 APK：重新导入一份新 ZIP 即可。启动流程（CLI 参数、
bootstrap、serve_forever）仍与 PC 版 `wb_proxy.main()` 逐步对齐，业务逻辑一行
都不在这里——这里只做「解压、找项目根、把 PC 版跑起来」。

数据目录（accounts/ usage/ catalog/ logs/）始终落在 App 沙盒里，与项目目录
分开：换一份 ZIP 覆盖导入，已登录的账号不会丢。
"""

import json
import importlib
import os
import re
import shutil
import sys
import threading
import time
import traceback
import zipfile

#: 项目根的判定标志：仓库根目录一定有的入口文件。
MARKER = "wb_proxy.py"
PANEL = "dashboard.html"
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
_STDIO_ORIGINAL = None
_WB = None
_PROJECTS_DIR = None


# --------------------------------------------------------------------------
# 项目导入
# --------------------------------------------------------------------------
def _purge_gateway_modules(projects_dir):
    """把上一份项目 import 进来的模块从 `sys.modules` 里摘掉，返回摘掉的模块名。

    更新网关必须换掉内存里的旧代码，而 Android 上「重启进程」这条路走不通：
    进程自己死掉之后，系统会拦掉「从后台启动 Activity」（真机日志：
    `Abort background activity starts from 10237`），普通闹钟、精确闹钟、
    `setAlarmClock` 都一样——用户只会看到 App 在更新后凭空消失。所以只能在
    原地换代码：停掉 server、把老项目目录里的模块摘掉、把老目录从 sys.path 去掉，
    下一次 start() 就会真正 import 新版本。

    这么做是安全的，因为网关是纯标准库实现，被摘掉的模块没有「必须活着」的
    全局状态：server 已 shutdown、线程已 join、账号数据都在磁盘上。
    """
    global _WB
    _WB = None
    root = os.path.abspath(projects_dir).rstrip(os.sep) + os.sep
    removed = []
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        try:
            full = os.path.abspath(path)
        except Exception:
            continue
        if full.startswith(root):
            sys.modules.pop(name, None)
            removed.append(name)
    if removed:
        # 目录 mtime 变了（新解出来的一份），import 缓存也要跟着失效。
        importlib.invalidate_caches()
    return removed


def _prune_sys_path(core, projects_dir):
    """sys.path 里只保留当前项目，别让旧项目把新版本删掉的模块「复活」。"""
    if not projects_dir:
        return
    root = os.path.abspath(projects_dir).rstrip(os.sep) + os.sep
    kept = []
    for entry in sys.path:
        try:
            full = os.path.abspath(entry)
        except Exception:
            kept.append(entry)
            continue
        if full.startswith(root) and os.path.abspath(core) != full:
            continue
        kept.append(entry)
    sys.path[:] = kept


def _find_root(base):
    """在解压目录里找项目根。

    GitHub 的 Download ZIP 会多套一层 `workbuddy2api-hub-main/`，直接解压出来
    的目录本身不含 wb_proxy.py，所以要往下探一层（也只探一层：再深就不是
    仓库结构了，多半是用户压错了东西）。
    """
    if os.path.isfile(os.path.join(base, MARKER)):
        return base
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return ""
    for name in entries:
        candidate = os.path.join(base, name)
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, MARKER)):
            return candidate
    return ""


def project_info(root):
    """读项目自身的版本号（与 PC 版 `server_version` 同一处），顺带自检面板。"""
    root = os.path.abspath(root or "")
    entry = os.path.join(root, MARKER)
    if not os.path.isfile(entry):
        return {"ok": False, "error": "目录里没有 %s，看起来不是网关项目" % MARKER}
    version = ""
    try:
        # wb_proxy.py 有两万多行，server_version 在文件靠后的位置：逐行扫，
        # 命中就停，别把整个文件读进内存（手机上内存比磁盘紧张）。
        with open(entry, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "server_version" not in line:
                    continue
                found = re.search(r'server_version\s*=\s*"wb-proxy/([0-9][0-9.]*)"', line)
                if found:
                    version = found.group(1)
                    break
    except Exception:
        pass
    try:
        files = len(os.listdir(root))
    except OSError:
        files = 0
    return {
        "ok": True,
        "root": root,
        "version": version,
        "has_panel": os.path.isfile(os.path.join(root, PANEL)),
        "files": files,
    }


def _safe_members(zf):
    """逐个条目做路径校验，拒绝绝对路径与 ../（zip slip）。"""
    for info in zf.infolist():
        name = (info.filename or "").replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        if name.startswith("__MACOSX/") or "/__MACOSX/" in name:
            continue
        parts = [p for p in name.split("/") if p not in ("", ".", "..")]
        if not parts:
            continue
        yield info, parts


def install(zip_path, projects_dir):
    """解压 ZIP 到 projects_dir/<时间戳>/，返回项目根与版本。

    每次导入都解到一个新目录，成功之后再让外壳把指针切过去——解压到一半失败
    时旧项目还在，不会出现「既跑不起来、也没得回退」的状态。
    """
    zip_path = os.path.abspath(zip_path or "")
    if not os.path.isfile(zip_path):
        return {"ok": False, "error": "压缩包不存在：%s" % zip_path}
    projects_dir = os.path.abspath(projects_dir)
    os.makedirs(projects_dir, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(projects_dir, stamp)
    suffix = 0
    while os.path.exists(dest):
        suffix += 1
        dest = os.path.join(projects_dir, "%s-%d" % (stamp, suffix))
    os.makedirs(dest, exist_ok=True)

    count = 0
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info, parts in _safe_members(zf):
                target = os.path.join(dest, *parts)
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                count += 1
                total += info.file_size
    except zipfile.BadZipFile:
        shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "error": "不是有效的 ZIP 压缩包"}
    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}

    root = _find_root(dest)
    if not root:
        shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False,
                "error": "压缩包里没有 %s；请导入从仓库 Download ZIP 得到的原包" % MARKER}

    info = project_info(root)
    if not info.get("ok"):
        shutil.rmtree(dest, ignore_errors=True)
        return info
    # 解压成功之后、外壳切指针之前，把内存里的旧网关摘掉：否则下一次 start()
    # 拿到的还是 sys.modules 里那份旧代码（导入新版本却不生效）。
    global _PROJECTS_DIR
    _PROJECTS_DIR = projects_dir
    purged = _purge_gateway_modules(projects_dir)
    info.update({"dest": dest, "files_extracted": count, "bytes": total,
                 "imported_at": time.time(), "purged": purged})
    return info


# --------------------------------------------------------------------------
# 运行环境
# --------------------------------------------------------------------------
class _Tee:
    """stdout/stderr 双写：logcat / 系统日志照旧，日志页也能读。"""

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


def _bind_stdio(log_path):
    """把 stdout/stderr 双写到日志文件。

    幂等：更新网关时 `_configure()` 会再调一次，这里必须始终从**原始流**重新包
    一层 Tee。否则每更新一次就会在旧 Tee 外面再套一层，同一行日志被写进文件
    两遍、三遍……（真机上表现为日志越更越长，且每行重复递增）。
    """
    global _LOG_FH, _STDIO_ORIGINAL
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        log_fh = open(log_path, "a", encoding="utf-8", buffering=1)
    except Exception:
        return
    if _STDIO_ORIGINAL is None:
        _STDIO_ORIGINAL = {
            name: getattr(sys, name, None) for name in ("stdout", "stderr")
        }
    previous = _LOG_FH
    _LOG_FH = log_fh
    for name, original in _STDIO_ORIGINAL.items():
        if original is None or not hasattr(original, "write"):
            setattr(sys, name, log_fh)
        else:
            setattr(sys, name, _Tee(original, log_fh))
    if previous is not None and previous is not log_fh:
        try:
            previous.close()
        except Exception:
            pass


def _bind_trust_store():
    """根证书：Android 的 /system/etc/security/cacerts 就是 OpenSSL 认的散列布局。"""
    if os.environ.get("SSL_CERT_FILE"):
        return "preset"
    try:
        import certifi  # noqa: F401
        bundle = certifi.where()
        if bundle and os.path.exists(bundle):
            os.environ["SSL_CERT_FILE"] = bundle
            os.environ["REQUESTS_CA_BUNDLE"] = bundle
            return "certifi"
    except Exception:
        pass
    for candidate in ("/system/etc/security/cacerts",
                      "/apex/com.android.conscrypt/cacerts",
                      "/etc/ssl/certs"):
        if os.path.isdir(candidate):
            os.environ["SSL_CERT_DIR"] = candidate
            return candidate
    return "default"


def _configure(project, data):
    """固定环境变量并挂 sys.path。

    必须在 `import wb_proxy` 之前调用：wb_proxy 在模块级就按这些变量算好了
    ACCOUNTS_DIR / USAGE_DIR / CATALOG_DIR。
    """
    global _HOME, _CORE, _LOGS
    home = os.path.abspath(data)
    core = os.path.abspath(project)
    _HOME, _CORE = home, core
    _LOGS = os.path.join(home, "logs")
    for sub in ("accounts", "usage", "catalog", "logs", "tmp"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)

    os.environ["ACCOUNTS_DIR"] = os.path.join(home, "accounts")
    os.environ["WB_PROXY_USAGE_DIR"] = os.path.join(home, "usage")
    os.environ["WB_PROXY_CATALOG_DIR"] = os.path.join(home, "catalog")
    os.environ["WB_APP_ROOT"] = home
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ["HOME"] = home
    os.environ["TMPDIR"] = os.path.join(home, "tmp")

    _bind_stdio(os.path.join(_LOGS, LOG_NAME))
    trust = _bind_trust_store()
    _prune_sys_path(core, _PROJECTS_DIR)
    if core not in sys.path:
        sys.path.insert(0, core)
    return {"home": home, "core": core, "logs": _LOGS, "trust": trust}


def _ensure_configured():
    if _HOME is None:
        raise RuntimeError("运行时还没初始化：先调用 start()")


def _gateway():
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
    accounts = usable = 0
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
        "core": _CORE or "",
        "log": os.path.join(_LOGS, LOG_NAME) if _LOGS else "",
        "error": _ERROR,
        "since": _STARTED_AT,
    }


def status(project, data):
    try:
        with _STATE_LOCK:
            return _dump(_status_locked())
    except Exception as exc:
        return _fail(exc)


def start(project, data, port, lan):
    """启动（或恢复）网关监听。重复调用幂等。"""
    global _SERVER, _THREAD, _ARGS, _BOOTED, _ERROR, _STARTED_AT
    with _STATE_LOCK:
        if _SERVER is not None:
            return _dump(_status_locked())
        _ERROR = ""
        try:
            info = project_info(project)
            if not info.get("ok"):
                _ERROR = info.get("error") or "网关项目不可用"
                return _dump(_status_locked())
            # 项目换了（更新后重新 start）必须重新 configure：否则 sys.path 里
            # 还挂着旧项目目录，import 出来的仍是旧代码；顺带把 _BOOTED 复位，
            # 让新版本自己跑一次首启引导。
            switched = _CORE is not None and os.path.abspath(project) != _CORE
            if _HOME is None or switched:
                _configure(project, data)
                if switched:
                    _BOOTED = False
            gateway = _gateway()

            host = "0.0.0.0" if lan else "127.0.0.1"
            argv = ["wb_proxy.py",
                    "--host", host, "--port", str(int(port)),
                    "--accounts-dir", os.path.join(_HOME, "accounts"),
                    "--usage-dir", os.path.join(_HOME, "usage")]
            if lan:
                argv.append("--lan")
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
                gateway._report_first_run(args)

            server = gateway.GatewayServer((args.host, args.port), gateway.Handler)
            thread = threading.Thread(target=server.serve_forever,
                                      name="wb-gateway", daemon=True)
            thread.start()
            _SERVER, _THREAD, _ARGS = server, thread, args
            _STARTED_AT = time.time()
        except SystemExit as exc:
            _ERROR = str(exc.code or "gateway refused to start")
            _SERVER = None
        except OSError as exc:
            _ERROR = _listen_error(exc, port)
            _SERVER = None
        except Exception as exc:
            _ERROR = "%s: %s" % (type(exc).__name__, exc)
            _SERVER = None
            _traceback_to_log()
        return _dump(_status_locked())


def _listen_error(exc, port):
    """把 bind 失败翻译成用户能照着做的一句话。

    手机上最常见的不是权限问题，而是**同一个端口上已经有一份网关**：比如用户
    在 Termux 里也跑着 PC 版脚本，或者上一次的进程没退干净。这时网关自己起不来，
    但 `127.0.0.1:<port>` 仍然是通的（通的是别人的进程），界面若不看这里的
    error，就会显示「运行中」并把别人的面板当成自己的——所以这条错误必须说清楚。
    """
    busy = getattr(exc, "errno", None) in (98, 48, 10048) or "in use" in str(exc).lower()
    if busy:
        return ("端口 %d 已被占用：这台手机上可能还有另一份网关在跑（例如 Termux 里"
                "的脚本），或上一次没有正常退出。到菜单「端口设置」换一个端口再启动，"
                "或先关掉占用的那一份。" % int(port))
    return "监听 %d 端口失败：%s" % (int(port), exc)


def _traceback_to_log():
    try:
        with open(os.path.join(_LOGS, LOG_NAME), "a", encoding="utf-8") as fh:
            fh.write("\n[container] gateway bootstrap failed\n" + traceback.format_exc() + "\n")
    except Exception:
        pass


def stop(project, data):
    global _SERVER, _THREAD
    with _STATE_LOCK:
        server, thread = _SERVER, _THREAD
        _SERVER = _THREAD = None
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
    try:
        with _STATE_LOCK:
            return _dump(_status_locked())
    except Exception as exc:
        return _fail(exc)


def import_credential(project, data, path):
    """把外部 `.info` / JSON 凭证导入账号池（面板之外的补充入口）。"""
    with _STATE_LOCK:
        if not _BOOTED or _WB is None or getattr(_WB, "POOL", None) is None:
            return _dump({"ok": False, "error": "网关还没启动"})
        try:
            account = _WB.POOL.import_desktop_credential(path=path, source="mobile-file")
        except Exception as exc:
            return _fail(exc)
        if account is None:
            return _dump({"ok": False, "error": "文件里没有可用账号"})
        return _dump({"ok": True,
                      "uid": getattr(account, "uid", "") or "",
                      "nickname": getattr(account, "nickname", "") or "",
                      "accounts": len(_WB.POOL.accounts)})


def log_tail(project, data, lines):
    try:
        path = os.path.join(_LOGS, LOG_NAME) if _LOGS else ""
        if not path or not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data_lines = fh.readlines()
        return "".join(data_lines[-max(1, int(lines)):])
    except Exception as exc:
        return "日志不可用: %s" % exc


def describe(project):
    try:
        return _dump(project_info(project))
    except Exception as exc:
        return _fail(exc)


def loaded():
    """进程里是否还留着网关本体（`wb_proxy` 在 sys.modules 里）。

    正常路径上它总是 False：install() 成功时已经把旧项目 import 进来的模块摘掉，
    所以外壳不需要重启进程，直接重新 start() 就会加载新版本。留这个接口是为了
    诊断——真机上如果它返回 True 而版本号没变，说明某次 install 没走 purge。
    """
    return _dump({"ok": True, "loaded": "wb_proxy" in sys.modules})


def _dump(payload):
    return json.dumps(payload, ensure_ascii=False)


def _fail(exc):
    return _dump({"ok": False, "running": False,
                  "error": "%s: %s" % (type(exc).__name__, exc)})
