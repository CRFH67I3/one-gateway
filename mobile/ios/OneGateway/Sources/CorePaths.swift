import Foundation

/// 沙盒里的两个关键路径。
///
/// * `bundleCore`：只读包体内的 app 目录（随 App 一起打包，含 wb_*.py 与
///   dashboard.html，同时也是 PYTHONPATH）；运行时会被 `wb_mobile_runtime`
///   同步到可写目录。
/// * `data`：可写数据目录，账号、用量、模型目录缓存、日志都在这里，不随更新丢失。
///
/// 标准库不在 `bundleCore` 里：它在包内 `python/`（PYTHONHOME），由 `PyBridge`
/// 自行按 `Bundle.main.resourcePath` 定位，这里不需要参与。
struct CorePaths {
    let bundleCore: String
    let data: String

    static func resolve() -> CorePaths {
        let manager = FileManager.default
        let support = manager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let data = support.appendingPathComponent("data", isDirectory: true)
        try? manager.createDirectory(at: data, withIntermediateDirectories: true)
        let bundleCore = (Bundle.main.resourcePath ?? "") + "/app"
        return CorePaths(bundleCore: bundleCore, data: data.path)
    }
}

/// 外壳自己的开关（与电脑版的 settings.json 分开存）。
/// 端口/局域网只影响「怎么监听」，账号与密钥仍然由网关自己管理。
enum Settings {
    private static let portKey = "hub.port"
    private static let lanKey = "hub.lan"
    private static let keepAliveKey = "hub.keepAlive"
    private static let liveActivityKey = "hub.liveActivity"
    private static let dynamicIslandKey = "hub.dynamicIsland"
    private static let remoteKey = "hub.remote"
    private static let remoteBaseKey = "hub.remoteBase"
    private static let remoteKindKey = "hub.remoteKind"

    /// 旧版本没写过这个键时用调用方给的默认值，而不是 `Bool()` 的 false。
    private static func storedBool(_ key: String, default fallback: Bool) -> Bool {
        guard UserDefaults.standard.object(forKey: key) != nil else { return fallback }
        return UserDefaults.standard.bool(forKey: key)
    }

    /// iOS 26 的持续后台任务。默认开：这是目前唯一能让网关在后台继续监听的手段。
    static var keepAlive: Bool {
        get { storedBool(keepAliveKey, default: true) }
        set { UserDefaults.standard.set(newValue, forKey: keepAliveKey) }
    }

    /// 自己的实时活动（锁屏卡片 + 灵动岛）。默认关，避免一上来就占用户的锁屏。
    static var liveActivity: Bool {
        get { storedBool(liveActivityKey, default: false) }
        set { UserDefaults.standard.set(newValue, forKey: liveActivityKey) }
    }

    /// 灵动岛的紧凑/展开视图。实时活动关掉时这个开关无意义。
    static var dynamicIsland: Bool {
        get { storedBool(dynamicIslandKey, default: true) }
        set { UserDefaults.standard.set(newValue, forKey: dynamicIslandKey) }
    }

    static var port: Int {
        get {
            let stored = UserDefaults.standard.integer(forKey: portKey)
            return stored == 0 ? 8788 : stored
        }
        set { UserDefaults.standard.set(newValue, forKey: portKey) }
    }

    static var lan: Bool {
        get { UserDefaults.standard.bool(forKey: lanKey) }
        set { UserDefaults.standard.set(newValue, forKey: lanKey) }
    }

    // MARK: - 远程接入（Go 网关这类无法在 App 内执行的形态）

    /// 打开后不启动本地 CPython 网关，容器只做面板与探活。
    static var remoteEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: remoteKey) }
        set { UserDefaults.standard.set(newValue, forKey: remoteKey) }
    }

    /// 远程网关的 origin（可带 http/https，可省略端口）。
    static var remoteBase: String {
        get { UserDefaults.standard.string(forKey: remoteBaseKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: remoteBaseKey) }
    }

    /// 默认 Go：Python 形态通常直接本地托管，远程多半是另一套实现。
    static var remoteKind: String {
        get { UserDefaults.standard.string(forKey: remoteKindKey) ?? GatewayProfile.go }
        set { UserDefaults.standard.set(newValue, forKey: remoteKindKey) }
    }
}

enum NetworkAddress {
    /// 局域网模式下列出本机 IPv4，供通知/面板提示其它设备该填什么地址。
    static func localIPv4() -> String? {
        var address: String?
        var pointer: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&pointer) == 0, let first = pointer else { return nil }
        defer { freeifaddrs(pointer) }
        for item in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let flags = Int32(item.pointee.ifa_flags)
            let isUp = (flags & IFF_UP) != 0
            let isLoopback = (flags & IFF_LOOPBACK) != 0
            guard isUp, !isLoopback, item.pointee.ifa_addr.pointee.sa_family == UInt8(AF_INET) else {
                continue
            }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let result = getnameinfo(item.pointee.ifa_addr, socklen_t(item.pointee.ifa_addr.pointee.sa_len),
                                     &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST)
            if result == 0 {
                let candidate = String(cString: host)
                if candidate.hasPrefix("192.168.") || candidate.hasPrefix("10.")
                    || candidate.hasPrefix("172.") {
                    address = candidate
                    break
                }
                if address == nil { address = candidate }
            }
        }
        return address
    }
}
