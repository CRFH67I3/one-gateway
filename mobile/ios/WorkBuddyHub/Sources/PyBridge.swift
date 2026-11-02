import Foundation
import Python

/// 内嵌 CPython 的桥接层。
///
/// iOS 上不存在「另起一个 python 进程」这种选项（App Store 禁止下载/派生子进程
/// 执行代码），所以解释器必须链接进 App：`Python.xcframework` 由 CI 从 BeeWare
/// 的 python-app-support 下载，随包嵌入，`PYTHONHOME` 指向 `Python.framework`。
///
/// 调用约定与 Android 侧保持一致：动作名 + 一个 JSON 字符串参数，返回值也是
/// JSON 字符串，外壳只负责解析展示。
final class PyBridge {
    static let shared = PyBridge()

    enum BridgeError: LocalizedError {
        case missingFramework
        case notInitialized

        var errorDescription: String? {
            switch self {
            case .missingFramework: return "未找到嵌入的 Python.framework"
            case .notInitialized: return "Python 解释器尚未初始化"
            }
        }
    }

    private let lock = NSLock()
    private var initialized = false

    /// 初始化解释器。必须在调用任何 Python 动作之前完成，且只做一次。
    func bootstrap(corePath: String) throws {
        lock.lock()
        defer { lock.unlock() }
        if initialized { return }

        guard let frameworks = Bundle.main.privateFrameworksPath else {
            throw BridgeError.missingFramework
        }
        let home = frameworks + "/Python.framework"
        guard FileManager.default.fileExists(atPath: home) else {
            throw BridgeError.missingFramework
        }

        setenv("PYTHONHOME", home, 1)
        setenv("PYTHONPATH", corePath, 1)
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1)
        setenv("PYTHONUNBUFFERED", "1", 1)

        // 0 = 不安装信号处理器：iOS 的信号语义与桌面不同，装了反而干扰系统。
        Py_InitializeEx(0)
        // 网关跑在自己的 Python 线程里，主线程交还给 iOS 主循环；
        // 此后每次调用都必须 PyGILState_Ensure / Release。
        _ = PyEval_SaveThread()
        initialized = true
    }

    /// 调用 `wb_hub_ios.<function>(payloadJSON)`，返回解析后的字典。
    func call(_ function: String, _ payload: [String: Any]) -> [String: Any] {
        guard initialized else {
            return ["ok": false, "running": false,
                    "error": BridgeError.notInitialized.localizedDescription]
        }
        guard let argument = Self.encode(payload) else {
            return ["ok": false, "running": false, "error": "payload 序列化失败"]
        }
        guard let raw = invoke(function, argument) else {
            return ["ok": false, "running": false, "error": "python 调用失败: \(function)"]
        }
        if function == "log_tail" { return ["text": raw] }
        guard let data = raw.data(using: .utf8),
              let parsed = try? JSONSerialization.jsonObject(with: data),
              let object = parsed as? [String: Any] else {
            return ["ok": false, "running": false, "error": raw]
        }
        return object
    }

    // MARK: - CPython C API
    //
    // 只调用 CPython 头文件里导出的普通函数（不碰宏与 static inline 助手），
    // 这样在不同 Xcode / SDK 组合下都能编过；返回的对象引用刻意不手动 DecRef：
    // 这套桥接只在启停、状态、日志这几个低频动作上调用，泄漏量可以忽略，
    // 换来的是不依赖 `Py_DecRef` 这类内联符号是否被 Swift 导入。

    private func invoke(_ function: String, _ argument: String) -> String? {
        let gstate = PyGILState_Ensure()
        defer { PyGILState_Release(gstate) }

        guard let module = PyImport_ImportModule("wb_hub_ios") else {
            PyErr_Print()
            return nil
        }
        guard let callable = PyObject_GetAttrString(module, function) else {
            PyErr_Print()
            return nil
        }
        guard let pyArg = PyUnicode_FromString(argument) else {
            PyErr_Print()
            return nil
        }
        guard let tuple = PyTuple_New(1) else {
            PyErr_Print()
            return nil
        }
        // PyTuple_SetItem 会「窃取」pyArg 的引用，此处不能再 DecRef。
        PyTuple_SetItem(tuple, 0, pyArg)

        guard let result = PyObject_CallObject(callable, tuple) else {
            PyErr_Print()
            return nil
        }
        guard let cString = PyUnicode_AsUTF8(result) else {
            PyErr_Print()
            return nil
        }
        return String(cString: cString)
    }

    private static func encode(_ payload: [String: Any]) -> String? {
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: []) else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }
}
