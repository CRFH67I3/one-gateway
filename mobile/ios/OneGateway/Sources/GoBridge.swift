import Foundation
import Darwin

/// 随包签名的 Go 网关桥接层。
///
/// iOS 不允许 fork/exec 子进程，也不允许加载未随包签名的代码，所以 Go 网关在
/// 构建期由 `scripts/build-go-gateway.sh` 用 `-buildmode=c-archive` 编成
/// `libgateway.a` 静态链接进 App。`StartGateway` 会起一个 goroutine 跑
/// http.Server，`StopGateway` 通过 context 取消让它优雅退出。
///
/// 配置与 Android 的 GoGateway.kt 逐字对齐：账号/状态写沙盒绝对路径，自更新器
/// 与后台外连任务一律关掉（App 内不能替换二进制，也不该在手机后台持续外连）。
final class GoBridge {
    static let shared = GoBridge()

    private let lock = NSLock()
    private var running = false

    private var workDir: String { CorePaths.resolve().data + "/go-gateway" }

    var isRunning: Bool {
        lock.lock(); defer { lock.unlock() }
        return running
    }

    /// 启动（或重启）Go 网关。返回 false 表示配置写不下去或 C 入口拒绝了调用。
    @discardableResult
    func start(port: Int, lan: Bool) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard writeConfig(port: port, lan: lan) else { return false }
        let path = workDir + "/config.json"
        guard let cPath = strdup(path) else { return false }
        defer { free(cPath) }
        let result = StartGateway(cPath)
        running = result == 0
        return running
    }

    func stop() {
        lock.lock(); defer { lock.unlock() }
        StopGateway()
        running = false
    }

    /// Go 网关把日志写到进程 stderr；iOS 没有可读的本地日志文件，界面里如实说明。
    var logHint: String {
        "Go 网关在 App 进程内运行，日志输出到设备控制台（Console.app / Xcode Devices）。\n\n"
            + "配置与数据目录：\(workDir)"
    }

    private func writeConfig(port: Int, lan: Bool) -> Bool {
        let manager = FileManager.default
        let dir = workDir
        for suffix in ["", "/auths", "/data", "/data/request-logs"] {
            try? manager.createDirectory(atPath: dir + suffix,
                                         withIntermediateDirectories: true)
        }

        let keyFile = dir + "/api_key.txt"
        let apiKey: String
        if let saved = try? String(contentsOfFile: keyFile, encoding: .utf8),
           !saved.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            apiKey = saved.trimmingCharacters(in: .whitespacesAndNewlines)
        } else {
            apiKey = UUID().uuidString.replacingOccurrences(of: "-", with: "")
            try? apiKey.write(toFile: keyFile, atomically: true, encoding: .utf8)
        }

        let config: [String: Any] = [
            "listen": (lan ? "0.0.0.0" : "127.0.0.1") + ":\(port)",
            "api_key": apiKey,
            "auth_dir": dir + "/auths",
            "state_file": dir + "/data/state.json",
            "update": ["auto_enabled": false],
            "schedule": ["balance_refresh_enabled": false],
            "alerting": ["enabled": false],
            "watchdog": ["enabled": false],
            "logging": ["request_archive_enabled": false],
            "admin": ["audit_enabled": false],
            "metrics": ["enabled": false],
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: config,
                                                     options: [.prettyPrinted]) else {
            return false
        }
        do {
            try data.write(to: URL(fileURLWithPath: dir + "/config.json"), options: .atomic)
            return true
        } catch {
            return false
        }
    }
}
