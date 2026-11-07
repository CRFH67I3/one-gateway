import Foundation

/// 容器里唯一一份「网关 → 地址/字段」的契约表，与 Android 的 `GatewayProfile.kt`
/// 逐字对齐。
///
/// 本容器只运行 Python 网关（wb_proxy.py + dashboard.html）：构建期注入
/// Resources/app，由内嵌 CPython 托管，导入 ZIP 即可热更新。仍可在
/// 「远程网关接入」里连到别处已运行的实例，此时本机不托管任何进程。
///
/// 契约集中在下面这几个成员里：面板路径、探活路径、健康字段、地址规范化。
enum GatewayProfile {
    static let python = "python"
    static let pythonDefaultPort = 8788

    static func label() -> String { "Python 网关" }

    static var remote: Bool { Settings.remoteEnabled }

    /// 接受 `127.0.0.1:8788`、`192.168.1.5`、完整 URL；缺端口补默认值。
    /// 允许 https 是为了接公网反代；局域网 http 由 Info.plist 的
    /// NSAllowsLocalNetworking 放行。
    static func normaliseBase(_ raw: String) -> String {
        var value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while value.hasSuffix("/") { value.removeLast() }
        let scheme = value.hasPrefix("https://") ? "https" : "http"
        if let range = value.range(of: "://") {
            value = String(value[range.upperBound...])
        }
        if value.isEmpty { return "\(scheme)://127.0.0.1:\(pythonDefaultPort)" }
        if !value.contains(":") { value += ":\(pythonDefaultPort)" }
        return "\(scheme)://\(value)"
    }

    static var base: String {
        if Settings.remoteEnabled { return normaliseBase(Settings.remoteBase) }
        return "http://127.0.0.1:\(Settings.port)"
    }

    static var panelBase: String { base + "/" }
    static var healthURL: String { base + "/health" }
    static var apiBase: String { base + "/v1" }

    static func normaliseHealth(_ json: [String: Any]) -> (Int, Int) {
        return (json["accounts"] as? Int ?? 0,
                json["accounts_ready"] as? Int ?? 0)
    }
}
