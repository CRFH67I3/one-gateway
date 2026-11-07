import Foundation

/// 两套外部网关的接入契约，与 Android 的 `GatewayProfile.kt` 逐字对齐。
///
/// iOS 只允许运行签名进包的代码，所以两种形态都在**进程内**本地运行：
///  * Python 形态（wb_proxy.py + dashboard.html）：构建期注入 Resources/app，
///    由内嵌 CPython 托管，可随包更新；
///  * Go 形态（workbuddy2api-panel）：构建期用 -buildmode=c-archive 编成静态库
///    静态链接进 App，由 GoBridge 起 goroutine 跑 http.Server。导入的 ZIP 只用于
///    确认形态与版本，不能替换二进制。
///  * 仍可在「远程网关接入」里连到别处已运行的实例，此时本机两种都不托管。
///
/// 契约差异集中在下面这几个函数里，改一种形态不用翻遍整个 ViewController：
/// 面板路径、探活路径、健康字段、默认端口、主题脚本、地址规范化。
enum GatewayProfile {
    static let python = "python"
    static let go = "go"
    static let pythonDefaultPort = 8788
    static let goDefaultPort = 7863

    static func panelPath(_ value: String) -> String { value == go ? "/panel/" : "/" }
    static func healthPath(_ value: String) -> String { value == go ? "/healthz" : "/health" }
    static func defaultPort(_ value: String) -> Int {
        value == go ? goDefaultPort : pythonDefaultPort
    }
    static func label(_ value: String) -> String { value == go ? "Go 网关" : "Python 网关" }

    static var remote: Bool { Settings.remoteEnabled }
    /// 本地模式由用户在菜单里选形态（默认 Python），远程模式用远程设置里的形态。
    static var kind: String { Settings.remoteEnabled ? Settings.remoteKind : Settings.localKind }
    /// Go 型且没开远程 = 本机运行随包签名的 Go 静态库。
    static var isLocalGo: Bool { !remote && Settings.localKind == go }

    /// 接受 `127.0.0.1:7863`、`192.168.1.5`、完整 URL；缺端口补该形态默认值。
    /// 允许 https 是为了接公网反代；局域网 http 由 Info.plist 的
    /// NSAllowsLocalNetworking 放行。
    static func normaliseBase(_ raw: String, forKind hint: String? = nil) -> String {
        var value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while value.hasSuffix("/") { value.removeLast() }
        let scheme = value.hasPrefix("https://") ? "https" : "http"
        if let range = value.range(of: "://") {
            value = String(value[range.upperBound...])
        }
        let port = defaultPort(hint ?? kind)
        if value.isEmpty { return "\(scheme)://127.0.0.1:\(port)" }
        if !value.contains(":") { value += ":\(port)" }
        return "\(scheme)://\(value)"
    }

    static var base: String {
        if Settings.remoteEnabled { return normaliseBase(Settings.remoteBase) }
        return "http://127.0.0.1:\(Settings.port)"
    }

    static var panelBase: String { base + panelPath(kind) }
    static var healthURL: String { base + healthPath(kind) }
    static var apiBase: String { base + "/v1" }

    /// Go 的 /healthz 在账号池不可服务时返回 503，响应体仍带 total/healthy：
    /// 那是「活着但没号」，不是「连不上」。
    static func acceptsHealthCode(_ code: Int) -> Bool {
        kind == go ? (code == 200 || code == 503) : code == 200
    }

    static func normaliseHealth(_ json: [String: Any]) -> (Int, Int) {
        if kind == go {
            return (json["total"] as? Int ?? 0, json["healthy"] as? Int ?? 0)
        }
        return (json["accounts"] as? Int ?? 0,
                json["accounts_ready"] as? Int ?? 0)
    }

    /// Go 面板的主题同步脚本：theme.js 首帧读 localStorage['wb2api.theme']，
    /// app.js 用顶层 `let theme` + applyTheme() 刷 `data-theme`。Python 面板不认
    /// 这套，仍由面板自己的 `wb:system-theme` 通道处理，两边互不干扰。
    static func themeScript(dark: Bool) -> String {
        let value = dark ? "dark" : "light"
        return """
        (function(v){
          try{localStorage.setItem('wb2api.theme',v);}catch(e){}
          try{theme=v;applyTheme();}catch(e){document.documentElement.setAttribute('data-theme',v);}
          try{window.dispatchEvent(new StorageEvent('storage',{key:'wb2api.theme',newValue:v}));}catch(e){}
        })('\(value)');
        """
    }
}
