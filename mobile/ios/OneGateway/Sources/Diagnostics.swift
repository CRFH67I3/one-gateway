import Darwin
import Foundation
import UIKit

/// 轻量自诊断：给「App 突然消失、设备上又找不到崩溃报告」这种情况留证据。
///
/// 背景：iOS 上「闪退」有三种完全不同的死法，外观一样但证据不同——
///  1. 崩溃（SIGSEGV/SIGABRT…）：系统会在设备上留 `.ips`；
///  2. 被系统回收（内存压力 / watchdog）：不一定留报告，或报告滞后；
///  3. 自己退出 / 被外部杀掉：**没有任何报告**。
/// 第三种最难查，用户只能描述成「闪退」。所以在 App 内部再记一份：
///
///  * `hub-events.log`：面包屑（启动、前后台、网关启停、WebView 加载、内存警告），
///    写在 `data/logs/` 下，跟网关日志同一个目录，方便一起取回；
///  * `.running` 标记：启动时写下，进后台时删除。下次启动若还在，说明上一次运行
///    是「在前台直接没了」，与正常锁屏/切换 App 区分开；
///  * 崩溃处理器：ObjC 未捕获异常与常见信号各写一行，再交回系统原来的处理，
///    不会妨碍系统生成 `.ips`。
///
/// 这些都是「记录」而不是「修复」：写盘量很小（每条几十字节），出事时才知道
/// 到底死在哪一步。
final class Diagnostics {

    static let shared = Diagnostics()

    /// 信号处理器里只能用 `write(2)` 这类异步信号安全的调用，文件描述符必须提前开好。
    static var crashFD: Int32 = -1

    private let queue = DispatchQueue(label: "com.onegateway.host.diag")
    private let startedAt = Date()
    private var logURL: URL?
    private var markerURL: URL?
    private var logHandle: FileHandle?
    private let maxBytes = 256 * 1024

    /// 上一次运行是不是「在前台直接没了」。
    private(set) var previousRunUnclean = false
    private(set) var previousRunMarker = ""

    private init() {}

    // MARK: - 启动

    func start() {
        let data = URL(fileURLWithPath: CorePaths.resolve().data, isDirectory: true)
        let logs = data.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)

        logURL = logs.appendingPathComponent("hub-events.log")
        markerURL = logs.appendingPathComponent(".running")

        readMarker()
        rotateIfNeeded()
        openLog()
        installHandlers()
        writeMarker()

        let info = Bundle.main.infoDictionary
        let version = (info?["CFBundleShortVersionString"] as? String) ?? "?"
        let build = (info?["CFBundleVersion"] as? String) ?? "?"
        note("=== launch v\(version)(\(build)) \(UIDevice.current.systemName) \(UIDevice.current.systemVersion) \(deviceModel())")
        if previousRunUnclean {
            note("!! 上次运行未正常收尾：\(previousRunMarker)")
        }

        NotificationCenter.default.addObserver(
            forName: UIApplication.didReceiveMemoryWarningNotification,
            object: nil, queue: .main
        ) { _ in
            Diagnostics.shared.note("memory warning")
        }
    }

    /// 进后台 = 正常挂起，把标记删掉；下次启动看到标记就是「前台没了」。
    func markClean() {
        queue.async { [weak self] in
            guard let self, let marker = self.markerURL else { return }
            try? FileManager.default.removeItem(at: marker)
        }
    }

    func markForeground() {
        writeMarker()
    }

    // MARK: - 记录

    func note(_ text: String) {
        let line = line(for: text)
        queue.async { [weak self] in
            self?.append(line)
        }
    }

    /// 同步写一条：崩溃处理器里没有下一次机会，不能排队。
    func noteSync(_ text: String) {
        append(line(for: text))
    }

    private func line(for text: String) -> String {
        let stamp = Self.formatter.string(from: Date())
        let uptime = String(format: "%.1f", Date().timeIntervalSince(startedAt))
        return "[\(stamp) +\(uptime)s] \(text)\n"
    }

    /// 把日志尾部 + 崩溃记录拼成一段可复制的文本，供「诊断信息」页展示。
    func report(maxLines: Int = 200) -> String {
        var parts: [String] = []
        let info = Bundle.main.infoDictionary
        let version = (info?["CFBundleShortVersionString"] as? String) ?? "?"
        let build = (info?["CFBundleVersion"] as? String) ?? "?"
        parts.append("版本 \(version)(\(build)) · \(UIDevice.current.systemName) \(UIDevice.current.systemVersion)")
        parts.append("设备 \(deviceModel())")
        parts.append("上次运行：\(previousRunUnclean ? "未正常结束（前台消失）" : "正常")")
        parts.append("")
        if let text = tail(logURL, lines: maxLines) {
            parts.append("— 运行记录 —\n" + text)
        }
        let fault = URL(fileURLWithPath: CorePaths.resolve().data, isDirectory: true)
            .appendingPathComponent("logs/faulthandler.log")
        if let text = tail(fault, lines: 80), !text.isEmpty {
            parts.append("— Python faulthandler —\n" + text)
        }
        return parts.joined(separator: "\n")
    }

    /// 面板「查看启动日志」旁边那条路：把 Python 侧的原生崩溃栈也带上。
    func tail(_ url: URL?, lines: Int) -> String? {
        guard let url, let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        let all = text.split(separator: "\n", omittingEmptySubsequences: false)
        guard all.count > lines else { return text }
        return all.suffix(lines).joined(separator: "\n")
    }

    // MARK: - 内部

    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MM-dd HH:mm:ss.SSS"
        return f
    }()

    private func deviceModel() -> String {
        var info = utsname()
        _ = uname(&info)
        let machine = withUnsafeBytes(of: &info.machine) { raw -> String in
            let bytes = raw.prefix(while: { $0 != 0 })
            return String(decoding: bytes, as: UTF8.self)
        }
        return "\(machine) · \(UIDevice.current.name)"
    }

    private func openLog() {
        guard let logURL else { return }
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        logHandle = try? FileHandle(forWritingTo: logURL)
        _ = try? logHandle?.seekToEnd()
    }

    /// 日志超过上限就砍掉前一半：设备上不方便做外部轮转，够用即可。
    private func rotateIfNeeded() {
        guard let logURL,
              let attributes = try? FileManager.default.attributesOfItem(atPath: logURL.path),
              let size = attributes[.size] as? Int, size > maxBytes,
              let text = try? String(contentsOf: logURL, encoding: .utf8) else { return }
        let kept = String(text.suffix(maxBytes / 2))
        try? kept.write(to: logURL, atomically: true, encoding: .utf8)
    }

    private func append(_ line: String) {
        guard let data = line.data(using: .utf8) else { return }
        if logHandle == nil { openLog() }
        logHandle?.write(data)
    }

    private func writeMarker() {
        queue.async { [weak self] in
            guard let self, let marker = self.markerURL else { return }
            let text = "pid=\(ProcessInfo.processInfo.processIdentifier) at=\(Self.formatter.string(from: Date()))"
            try? text.write(to: marker, atomically: true, encoding: .utf8)
        }
    }

    private func readMarker() {
        guard let marker = markerURL,
              let text = try? String(contentsOf: marker, encoding: .utf8) else { return }
        previousRunUnclean = true
        previousRunMarker = text
        try? FileManager.default.removeItem(at: marker)
    }

    // MARK: - 崩溃处理

    private func installHandlers() {
        NSSetUncaughtExceptionHandler { exception in
            let text = """
            !! NSException \(exception.name.rawValue): \(exception.reason ?? "")
            \(exception.callStackSymbols.joined(separator: "\n"))
            """
            // 这里必须同步写：进程马上就要被 terminated，排队等于丢掉。
            Diagnostics.shared.noteSync(text)
        }

        // 提前开好 fd、提前把要写的字节准备好：信号处理器里不能分配内存、不能走 Foundation
        // ——崩溃点很可能就在 malloc 里，处理器再 malloc 会直接死锁（表现为「卡住不动」，
        // 比闪退更难查）。所以这里只做 write(2)。
        if let crashURL = logURL?.deletingLastPathComponent()
            .appendingPathComponent("hub-crash.log") {
            if !FileManager.default.fileExists(atPath: crashURL.path) {
                FileManager.default.createFile(atPath: crashURL.path, contents: nil)
            }
            Diagnostics.crashFD = open(crashURL.path, O_WRONLY | O_APPEND | O_CREAT, 0o644)
        }

        wbCrashLines.removeAll()
        for (sig, name) in [(SIGABRT, "SIGABRT"), (SIGSEGV, "SIGSEGV"), (SIGBUS, "SIGBUS"),
                            (SIGILL, "SIGILL"), (SIGFPE, "SIGFPE"), (SIGTRAP, "SIGTRAP")] {
            wbCrashLines.append((sig, Array("!! \(name)\n".utf8CString)))
            _ = signal(sig, wbCrashSignal)
        }
    }

    /// 让已经写出去的记录落盘（崩溃路径上能做的只有这么多了）。
    /// 刻意不用 `queue.sync`：万一崩溃就发生在自己的队列上，同步等于死锁。
    func flushNow() {
        try? logHandle?.synchronize()
    }
}

/// 安装处理器时就把「要写哪些字节」准备好，信号处理器里只做一次 `write(2)`。
var wbCrashLines: [(sig: Int32, text: [CChar])] = []

/// C 侧信号处理器：写一行固定文本，然后把信号交还给系统默认处理，
/// 这样系统照常生成 `.ips`，我们只是多留一条「死因」。
private func wbCrashSignal(_ sig: Int32) {
    let fd = Diagnostics.crashFD
    if fd >= 0 {
        for entry in wbCrashLines where entry.sig == sig {
            entry.text.withUnsafeBufferPointer { buffer in
                guard let base = buffer.baseAddress else { return }
                _ = write(fd, base, strlen(base))
            }
            break
        }
    }
    _ = signal(sig, SIG_DFL)
    _ = raise(sig)
}
