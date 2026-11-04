"""iOS 桥接层：Swift → 这里 → 共享运行时。

与 Android 侧 `wb_hub_android.py` 一一对应：只做路径转换与 JSON 序列化，
业务逻辑全部留在 `wb_mobile_runtime` / `wb_proxy`。两边共用同一份核心代码，
所以「手机上的网关」和「电脑上的网关」在账号、配额、模型、计费上完全同源。
"""

import json
import os
import sys

_ready = False

#: 网关离不开的二进制扩展。iOS 上它们全在标准库的 lib-dynload 里，必须以
#: `Frameworks/<名字>.framework` 的形式存在才能被 dlopen。打包时漏掉这一步的
#: 表现是 `hashlib has no attribute 'sha1'`——报在启动第一步，日志也还没建好，
#: 排查成本极高。这里提前探测，把「缺什么、该怎么修」直接写进错误信息。
_CRITICAL_EXTENSIONS = ("_sha1", "_hashlib", "_socket", "_ssl", "_json", "zlib")


def _missing_extensions():
    missing = []
    for name in _CRITICAL_EXTENSIONS:
        try:
            __import__(name)
        except Exception:
            missing.append(name)
    return missing


def _runtime(core, data):
    global _ready
    if core and core not in sys.path:
        sys.path.insert(0, core)
    import wb_mobile_runtime as runtime
    if not _ready:
        runtime.configure(home=data, core=core,
                          logs=os.path.join(data, "logs"))
        _ready = True
    return runtime


def _payload(raw):
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _wrap(action):
    def run():
        return action()
    try:
        return json.dumps(run(), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "running": False,
                           "error": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)


def start(raw):
    payload = _payload(raw)

    def action():
        missing = _missing_extensions()
        if missing:
            raise RuntimeError(
                "包内缺少 Python 二进制扩展（%s）。这是打包问题，不是账号或配置问题："
                "iOS 包没有把标准库 lib-dynload 里的 .so 转成 Frameworks/*.framework，"
                "请用 mobile/ios 下的最新构建脚本重新出包。"
                % "、".join(missing)
            )
        runtime = _runtime(payload.get("core", ""), payload.get("data", ""))
        return runtime.start(port=int(payload.get("port", 8788)),
                             lan=bool(payload.get("lan", False)))
    return _wrap(action)


def stop(raw):
    payload = _payload(raw)

    def action():
        return _runtime(payload.get("core", ""), payload.get("data", "")).stop()
    return _wrap(action)


def status(raw):
    payload = _payload(raw)

    def action():
        return _runtime(payload.get("core", ""), payload.get("data", "")).status()
    return _wrap(action)


def log_tail(raw):
    payload = _payload(raw)
    try:
        return _runtime(payload.get("core", ""), payload.get("data", "")) \
            .log_tail(int(payload.get("lines", 300)))
    except Exception as exc:
        return "日志不可用: %s" % exc
