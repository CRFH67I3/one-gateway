"""iOS 桥接层：Swift → 这里 → 共享运行时。

与 Android 侧 `wb_hub_android.py` 一一对应：只做路径转换与 JSON 序列化，
业务逻辑全部留在 `wb_mobile_runtime` / `wb_proxy`。两边共用同一份核心代码，
所以「手机上的网关」和「电脑上的网关」在账号、配额、模型、计费上完全同源。
"""

import json
import os
import sys

_ready = False


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
