import Darwin
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
    private static let portHopKey = "hub.portHop"

    /// 端口被占用时顺延探测的上限：8788 起最多试到 8797。
    static let portHopLimit = 10

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

    /// 端口被占用时自动顺延（+1 递增探测，默认关）。
    /// 关闭时保持原行为：预设端口被占就由网关报「端口被占用」，手动处理。
    static var portHop: Bool {
        get { UserDefaults.standard.bool(forKey: portHopKey) }
        set { UserDefaults.standard.set(newValue, forKey: portHopKey) }
    }

    /// 启动前解析实际端口：开了 portHop 就从 requested（缺省当前设置）开始
    /// 探测，被占则 +1 递增（最多 portHopLimit 个），把生效值写回 port——
    /// 状态栏、面板与通知里的地址自然都指向真正在服务的端口。
    /// 开关关闭时原样返回，不做任何探测。
    static func effectivePort(from requested: Int? = nil) -> Int {
        let preferred = requested ?? port
        guard portHop else { return preferred }
        var candidate = preferred
        var hops = 0
        while PortProbe.busy(candidate) && hops < portHopLimit {
            candidate = candidate >= 65535 ? 1024 : candidate + 1
            hops += 1
        }
        if candidate != preferred { port = candidate }
        return candidate
    }

    // MARK: - 远程接入

    /// 打开后不启动任何本地网关，容器只做面板与探活。
    static var remoteEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: remoteKey) }
        set { UserDefaults.standard.set(newValue, forKey: remoteKey) }
    }

    /// 远程网关的 origin（可带 http/https，可省略端口）。
    static var remoteBase: String {
        get { UserDefaults.standard.string(forKey: remoteBaseKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: remoteBaseKey) }
    }
}

enum PortProbe {
    /// 连得上 = 端口被占（不管对方是不是我们的网关）；连不上（拒绝/超时）
    /// = 空闲。回环上的拒绝是立即返回的，阻塞式 connect 足够。
    static func busy(_ port: Int) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(clamping: port).bigEndian
        addr.sin_addr = in_addr(s_addr: UInt32(INADDR_LOOPBACK).bigEndian)
        let result = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return result == 0
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
