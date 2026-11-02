"""Android 桥接层：Kotlin 调这里，这里调共享运行时。

这一层刻意保持极薄：只做「路径转换 + JSON 序列化」，任何业务判断都留在
`wb_mobile_runtime` / `wb_proxy` 里，PC 版与移动端因此共用同一套逻辑。
"""

import json
import os
import sys

_ready = False


def _runtime(core_dir, data_dir):
    """首次调用时完成沙盒初始化（同步核心源码、绑定目录与证书）。"""
    global _ready
    if core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    import wb_mobile_runtime as runtime
    if not _ready:
        runtime.configure(home=data_dir, core=core_dir,
                          logs=os.path.join(data_dir, "logs"))
        _ready = True
    return runtime


def _dump(payload):
    return json.dumps(payload, ensure_ascii=False)


def _fail(exc):
    return _dump({"ok": False, "running": False,
                  "error": "%s: %s" % (type(exc).__name__, exc)})


def start(core_dir, data_dir, port, lan):
    try:
        runtime = _runtime(core_dir, data_dir)
        return _dump(runtime.start(port=int(port), lan=bool(lan)))
    except Exception as exc:
        return _fail(exc)


def stop(core_dir, data_dir):
    try:
        return _dump(_runtime(core_dir, data_dir).stop())
    except Exception as exc:
        return _fail(exc)


def status(core_dir, data_dir):
    try:
        return _dump(_runtime(core_dir, data_dir).status())
    except Exception as exc:
        return _fail(exc)


def import_credential(core_dir, data_dir, path):
    try:
        return _dump(_runtime(core_dir, data_dir).import_credential(path))
    except Exception as exc:
        return _fail(exc)


def log_tail(core_dir, data_dir, lines):
    try:
        return _runtime(core_dir, data_dir).log_tail(int(lines))
    except Exception as exc:
        return "日志不可用: %s" % exc
