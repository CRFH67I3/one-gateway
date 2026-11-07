import UIKit
import WebKit

/// 主界面 = 与电脑版同一张面板（WKWebView 加载 127.0.0.1）。
///
/// 平台适配点：iOS 没有「常驻后台服务」这回事，后台进程会被挂起、套接字失效。
/// 所以这里的模型是「前台运行 + 优雅让位」：
///   * 进入后台后给 20 秒宽限期，让在途请求收尾，然后主动关闭监听；
///   * 回到前台立即恢复监听（Python 侧的账号池/调度线程只初始化一次，恢复很快）。
/// 这也是为什么面板里可能看到一次断连——这是 iOS 的硬约束，不是网关的问题。
final class RootViewController: UIViewController {

    private let paths = CorePaths.resolve()
    private let webView = WKWebView(frame: .zero)
    private let statusDot = UIView()
    private let statusLabel = UILabel()
    private let spinner = UIActivityIndicatorView(style: .medium)

    private var loadedURL: String?
    private var healthTimer: Timer?
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    private var pauseWorkItem: DispatchWorkItem?
    private var bootstrapped = false

    /// App 是否在前台。后台计时器/后台任务到期回调都会调 `pauseGateway`，
    /// 但用户可能刚好在这时切回来——没有这个标记就会出现「已经回前台了，
    /// 延迟的 stop 却把监听关掉」：面板转圈、网关地址打不开，看起来像崩了。
    private var isForeground = true
    /// 连续健康检查失败次数：网关真的躺了才重启，偶发一次超时不要动。
    private var healthFailures = 0

    /// 最近一次拿到的网关状态。实时活动与设置页的「状态」都读这几个值。
    private var lastAccounts = 0
    private var lastUsable = 0
    private var lastRunning = false
    private var lastNote = "正在启动"

    /// 面板与 API 地址统一由 GatewayProfile 按形态给出：
    /// 本地 Python 是 `/` + /health，Go 是 `/panel/` + /healthz（本机与远程同路径）。
    private var baseURL: String { GatewayProfile.panelBase }
    private var apiBase: String { GatewayProfile.apiBase }
    private var systemDark: Bool { traitCollection.userInterfaceStyle == .dark }

    override func viewDidLoad() {
        super.viewDidLoad()
        buildUI()
        observeLifecycle()
        wireBackgroundKeepAlive()
        startGateway()
        startPolling()
    }

    /// 系统深浅变化：外壳由 UIKit 自动换色，页面里的两套主题脚本得各自补一次。
    /// WKWebView 的 prefers-color-scheme 不总跟系统走，所以 Go 面板靠注入同步。
    override func traitCollectionDidChange(_ previousTraitCollection: UITraitCollection?) {
        super.traitCollectionDidChange(previousTraitCollection)
        guard traitCollection.userInterfaceStyle != previousTraitCollection?.userInterfaceStyle else {
            return
        }
        webView.evaluateJavaScript(
            GatewayProfile.themeScript(dark: systemDark), completionHandler: nil)
    }

    /// 持续后台任务的状态变化：任务起来了就取消「20 秒后自断监听」的保底逻辑，
    /// 任务到期则立刻停监听（避免内核已经回收套接字、面板还在等半死连接）。
    private func wireBackgroundKeepAlive() {
        BackgroundKeepAlive.shared.onChange = { [weak self] in
            guard let self else { return }
            if BackgroundKeepAlive.shared.isRunning {
                self.pauseWorkItem?.cancel()
                self.pauseWorkItem = nil
                self.endBackgroundTask()
                self.lastNote = "后台保持中"
                self.syncLiveActivity(force: true)
            }
        }
        BackgroundKeepAlive.shared.onExpire = { [weak self] in
            guard let self else { return }
            self.pauseGateway()
            self.endBackgroundTask()
        }
        // 上一次进程被杀后，锁屏上的实时活动可能还挂着，启动时先清掉再按需重建。
        LiveActivityController.shared.resetStale()
    }

    deinit {
        healthTimer?.invalidate()
        NotificationCenter.default.removeObserver(self)
    }

    // MARK: - UI

    private func buildUI() {
        view.backgroundColor = .systemBackground
        webView.translatesAutoresizingMaskIntoConstraints = false
        webView.allowsBackForwardNavigationGestures = true
        // 面板用 alert/confirm 做提示与二次确认（关闭网关、删账号、清日志），
        // 没有 uiDelegate 时 WKWebView 会静默丢弃，confirm 直接返回 false。
        webView.uiDelegate = self
        webView.navigationDelegate = self

        let header = UIView()
        header.translatesAutoresizingMaskIntoConstraints = false
        header.backgroundColor = .systemBackground

        statusDot.translatesAutoresizingMaskIntoConstraints = false
        statusDot.layer.cornerRadius = 5
        statusDot.backgroundColor = .systemGray3

        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        statusLabel.font = .systemFont(ofSize: 12)
        statusLabel.textColor = .secondaryLabel
        statusLabel.text = "正在启动网关…"

        spinner.translatesAutoresizingMaskIntoConstraints = false
        spinner.hidesWhenStopped = true

        let menuButton = UIButton(type: .system)
        menuButton.translatesAutoresizingMaskIntoConstraints = false
        menuButton.setImage(UIImage(systemName: "ellipsis.circle"), for: .normal)
        menuButton.addTarget(self, action: #selector(showMenu), for: .touchUpInside)

        let reloadButton = UIButton(type: .system)
        reloadButton.translatesAutoresizingMaskIntoConstraints = false
        reloadButton.setImage(UIImage(systemName: "arrow.clockwise"), for: .normal)
        reloadButton.addTarget(self, action: #selector(reloadDashboard), for: .touchUpInside)

        [statusDot, statusLabel, spinner, menuButton, reloadButton].forEach(header.addSubview)
        [header, webView].forEach(view.addSubview)

        NSLayoutConstraint.activate([
            header.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            header.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            header.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            header.heightAnchor.constraint(equalToConstant: 44),

            statusDot.leadingAnchor.constraint(equalTo: header.leadingAnchor, constant: 16),
            statusDot.centerYAnchor.constraint(equalTo: header.centerYAnchor),
            statusDot.widthAnchor.constraint(equalToConstant: 10),
            statusDot.heightAnchor.constraint(equalToConstant: 10),

            statusLabel.leadingAnchor.constraint(equalTo: statusDot.trailingAnchor, constant: 8),
            statusLabel.centerYAnchor.constraint(equalTo: header.centerYAnchor),

            spinner.leadingAnchor.constraint(equalTo: statusLabel.trailingAnchor, constant: 6),
            spinner.centerYAnchor.constraint(equalTo: header.centerYAnchor),
            spinner.widthAnchor.constraint(equalToConstant: 16),
            spinner.heightAnchor.constraint(equalToConstant: 16),

            reloadButton.trailingAnchor.constraint(equalTo: menuButton.leadingAnchor, constant: -4),
            reloadButton.centerYAnchor.constraint(equalTo: header.centerYAnchor),
            reloadButton.widthAnchor.constraint(equalToConstant: 40),
            reloadButton.heightAnchor.constraint(equalToConstant: 40),

            menuButton.trailingAnchor.constraint(equalTo: header.trailingAnchor, constant: -8),
            menuButton.centerYAnchor.constraint(equalTo: header.centerYAnchor),
            menuButton.widthAnchor.constraint(equalToConstant: 40),
            menuButton.heightAnchor.constraint(equalToConstant: 40),

            webView.topAnchor.constraint(equalTo: header.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            webView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])

        statusLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    }

    // MARK: - 生命周期

    private func observeLifecycle() {
        NotificationCenter.default.addObserver(
            self, selector: #selector(willResignActive),
            name: UIApplication.willResignActiveNotification, object: nil)
        NotificationCenter.default.addObserver(
            self, selector: #selector(didEnterBackground),
            name: UIApplication.didEnterBackgroundNotification, object: nil)
        NotificationCenter.default.addObserver(
            self, selector: #selector(didBecomeActive),
            name: UIApplication.didBecomeActiveNotification, object: nil)
    }

    /// 持续后台任务的请求必须在 App 仍算「前台」时提交，`willResignActive` 是最后一个
    /// 这样的时机（用户刚按 Home / 锁屏，进程还没被挂起）；放到 `didEnterBackground`
    /// 提交会被系统直接拒绝。
    @objc private func willResignActive() {
        BackgroundKeepAlive.shared.submit()
    }

    @objc private func didEnterBackground() {
        isForeground = false
        healthFailures = 0
        Diagnostics.shared.note("didEnterBackground keepAlive=\(BackgroundKeepAlive.shared.isRunning)")
        spinner.stopAnimating()
        // 持续后台任务已经在跑：后台时间由它接管，不能再按 20 秒的老规矩断监听。
        if BackgroundKeepAlive.shared.isRunning { return }
        backgroundTask = UIApplication.shared.beginBackgroundTask { [weak self] in
            // 宽限期被系统提前切断：立刻停下监听，避免半死连接。
            self?.pauseGateway()
            self?.endBackgroundTask()
        }
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            // 任务可能在这 20 秒里才被系统拉起，起来了就交给它，别自断。
            if BackgroundKeepAlive.shared.isRunning {
                self.endBackgroundTask()
                return
            }
            self.pauseGateway()
            self.endBackgroundTask()
        }
        pauseWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 20, execute: work)
    }

    @objc private func didBecomeActive() {
        isForeground = true
        Diagnostics.shared.note("didBecomeActive running=\(lastRunning)")
        pauseWorkItem?.cancel()
        pauseWorkItem = nil
        endBackgroundTask()
        // 前台本来就有前台时间，把后台任务还给系统，锁屏上那张进度卡也随之撤掉。
        BackgroundKeepAlive.shared.stop()
        guard bootstrapped else { return }
        // 网关还活着就只探一次健康，不要整页重载面板。
        // 之前是无条件 `reloadDashboard()`：每次从授权页/别的 App 切回来都重排一次
        // 1.2MB 的面板，WebKit 侧内存瞬时抬高，iPad 上后台再挂着几个大 App 就容易被系统收走。
        if lastRunning {
            refreshHealth()
            return
        }
        resumeGateway()
    }

    /// 从后台回来、或网关确认躺了时恢复监听（`start` 本身是幂等的）。
    private func resumeGateway() {
        if GatewayProfile.remote {
            // 远程网关不会因为我们切到后台就停，回来探一次即可，别去调 PyBridge。
            refreshHealth()
            return
        }
        if GatewayProfile.isLocalGo {
            // Go 网关被 pauseGateway 停过；重新起一个 goroutine 并立刻探活。
            startLocalGo()
            return
        }
        spinner.startAnimating()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let state = PyBridge.shared.call("start", self.payload())
            DispatchQueue.main.async {
                // 之前那张页面已经连不上了，清掉缓存地址让 apply 重新加载一次面板。
                self.loadedURL = nil
                self.apply(state)
            }
        }
    }

    private func endBackgroundTask() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }

    private func pauseGateway() {
        // 远程网关不在本机，没有监听可停；后台时保持现状，回来重新探活即可。
        guard !GatewayProfile.remote else { return }
        // 已经回到前台就别再停了：后台计时器/后台任务到期通知可能晚到一步。
        guard bootstrapped, !isForeground else { return }
        if GatewayProfile.isLocalGo {
            lastRunning = false
            lastNote = "已暂停，回到前台自动恢复"
            syncLiveActivity(force: true)
            stopLocalGo()
            return
        }
        lastRunning = false
        lastNote = "已暂停，回到前台自动恢复"
        Diagnostics.shared.note("pauseGateway")
        syncLiveActivity(force: true)
        let payload = self.payload()
        DispatchQueue.global(qos: .utility).async {
            _ = PyBridge.shared.call("stop", payload)
        }
    }

    /// 把最新状态推给自己的实时活动（锁屏卡片 / 灵动岛）。开关关着时这里会撤下卡片。
    private func syncLiveActivity(force: Bool = false) {
        LiveActivityController.shared.sync(
            port: Settings.port,
            accounts: lastAccounts,
            usable: lastUsable,
            requests: 0,
            running: lastRunning,
            note: lastNote,
            force: force
        )
    }

    // MARK: - 网关

    /// Go 型本机运行：静态库在进程内起 goroutine，不碰 CPython 解释器。
    /// 改端口/局域网/形态时都用同一条路径重启，避免两套生命周期各自漂移。
    private func startLocalGo(port: Int? = nil, lan: Bool? = nil) {
        let ok = GoBridge.shared.start(port: port ?? Settings.port, lan: lan ?? Settings.lan)
        bootstrapped = true
        loadedURL = nil
        // 启动要花几秒（读配置、账号池、预热目录）。不把这两个值清掉的话，
        // 旧形态留下的 lastRunning=true 会让连续三次探活失败触发 resumeGateway，
        // 在 Go 还在初始化时又重启一遍，形成来回抢端口的循环。
        healthFailures = 0
        lastRunning = false
        Diagnostics.shared.note("GoBridge.start ok=\(ok)")
        if !ok { toast("Go 网关配置写入失败") }
        refreshHealth()
    }

    private func stopLocalGo() {
        GoBridge.shared.stop()
        Diagnostics.shared.note("GoBridge.stop")
    }

    private func payload(port: Int? = nil, lan: Bool? = nil) -> [String: Any] {
        ["core": paths.bundleCore,
         "data": paths.data,
         "port": port ?? Settings.port,
         "lan": lan ?? Settings.lan]
    }

    private func startGateway() {
        spinner.startAnimating()
        Diagnostics.shared.note("startGateway remote=\(GatewayProfile.remote)")
        // 远程接入没有本地进程，连 CPython bootstrap 都不必做：网关跑在别处，
        // 健康轮询会直接驱动面板加载。bootstrapped 置位是为了回前台时走探活。
        if GatewayProfile.remote {
            bootstrapped = true
            refreshHealth()
            return
        }
        if GatewayProfile.isLocalGo {
            // 随包签名的 Go 静态库在进程内跑，不需要 CPython bootstrap。
            spinner.stopAnimating()
            startLocalGo()
            return
        }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                try PyBridge.shared.bootstrap(corePath: self.paths.bundleCore)
                self.bootstrapped = true
            } catch {
                Diagnostics.shared.note("bootstrap failed: \(error.localizedDescription)")
                DispatchQueue.main.async {
                    self.spinner.stopAnimating()
                    self.statusLabel.text = "初始化失败：\(error.localizedDescription)"
                    self.statusDot.backgroundColor = .systemRed
                }
                return
            }
            let state = PyBridge.shared.call("start", self.payload())
            DispatchQueue.main.async { self.apply(state) }
        }
    }

    private func restartGateway(port: Int, lan: Bool) {
        if GatewayProfile.remote {
            // 远程模式下改端口只影响「本机托管」那一侧；这里只需按当前地址重探一次。
            loadedURL = nil
            refreshHealth()
            return
        }
        if GatewayProfile.isLocalGo {
            // 端口变了要换 listen：先取消旧 goroutine，再按新端口起一份。
            stopLocalGo()
            startLocalGo(port: port, lan: lan)
            reloadDashboard()
            return
        }
        spinner.startAnimating()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            _ = PyBridge.shared.call("stop", self.payload(port: port, lan: lan))
            let state = PyBridge.shared.call("start", self.payload(port: port, lan: lan))
            DispatchQueue.main.async {
                self.loadedURL = nil
                self.apply(state)
                self.reloadDashboard()
            }
        }
    }

    private func apply(_ state: [String: Any]) {
        spinner.stopAnimating()
        if let error = state["error"] as? String, !error.isEmpty,
           state["running"] as? Bool != true {
            statusDot.backgroundColor = .systemRed
            statusLabel.text = "启动失败：\(error)"
            lastRunning = false
            lastNote = "启动失败"
            syncLiveActivity(force: true)
            return
        }
        guard state["running"] as? Bool == true else {
            statusDot.backgroundColor = .systemGray3
            statusLabel.text = "网关已停止"
            lastRunning = false
            lastNote = "网关已停止"
            syncLiveActivity(force: true)
            return
        }
        statusDot.backgroundColor = .systemGreen
        let port = state["port"] as? Int ?? Settings.port
        let accounts = state["accounts"] as? Int ?? 0
        let usable = state["usable"] as? Int ?? 0
        statusLabel.text = "运行中 · 127.0.0.1:\(port) · 账号 \(usable)/\(accounts)"
        lastAccounts = accounts
        lastUsable = usable
        lastRunning = true
        lastNote = "网关运行中"
        syncLiveActivity(force: true)
        loadDashboardIfNeeded()
    }

    // MARK: - 健康检查

    private func startPolling() {
        healthTimer?.invalidate()
        healthTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.refreshHealth()
        }
        refreshHealth()
    }

    private func refreshHealth() {
        // 两种形态的探活路径不同（/health vs /healthz），地址也可能是远程设备。
        guard let url = URL(string: GatewayProfile.healthURL) else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            guard let self else { return }
            var parsed: [String: Any]?
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            // Go 网关在池不可服务时回 503，但响应体仍带 total/healthy，算可达。
            if let data, GatewayProfile.acceptsHealthCode(code),
               let object = try? JSONSerialization.jsonObject(with: data) {
                parsed = object as? [String: Any]
            }
            DispatchQueue.main.async { self.applyHealth(parsed) }
        }.resume()
    }

    private func applyHealth(_ json: [String: Any]?) {
        guard let json else {
            healthFailures += 1
            if statusLabel.text?.hasPrefix("运行中") == true {
                statusDot.backgroundColor = .systemOrange
                statusLabel.text = "正在恢复网关…"
                lastNote = "正在恢复网关"
                syncLiveActivity()
            }
            // 连续三次都连不上，说明监听真的没了（不是偶发超时）：自己拉起来，
            // 别让用户对着「正在恢复网关…」干等到切后台。
            // 远程网关没有本地进程可拉起，重试只会白跑一次 PyBridge。
            if healthFailures == 3, isForeground, bootstrapped, lastRunning,
               !GatewayProfile.remote {
                Diagnostics.shared.note("health failed 3x, restarting gateway")
                lastRunning = false
                resumeGateway()
            }
            return
        }
        healthFailures = 0
        statusDot.backgroundColor = .systemGreen
        // Python /health 用 accounts/accounts_ready，Go /healthz 用 total/healthy。
        let (accounts, usable) = GatewayProfile.normaliseHealth(json)
        let endpoint = GatewayProfile.remote ? GatewayProfile.base : "127.0.0.1:\(Settings.port)"
        statusLabel.text = "运行中 · \(endpoint) · 账号 \(usable)/\(accounts)"
        lastAccounts = accounts
        lastUsable = usable
        lastRunning = true
        lastNote = BackgroundKeepAlive.shared.isRunning ? "后台保持中" : "网关运行中"
        syncLiveActivity()
        loadDashboardIfNeeded()
    }

    private func loadDashboardIfNeeded() {
        let target = baseURL
        guard loadedURL != target, let url = URL(string: target) else { return }
        loadedURL = target
        webView.load(URLRequest(url: url))
    }

    @objc private func reloadDashboard() {
        loadedURL = baseURL
        if let url = URL(string: baseURL) {
            webView.load(URLRequest(url: url))
        }
    }

    // MARK: - 菜单

    @objc private func showMenu() {
        let sheet = UIAlertController(title: nil, message: nil, preferredStyle: .actionSheet)
        sheet.addAction(UIAlertAction(title: "后台保持…", style: .default) { [weak self] _ in
            self?.showBackgroundSettings()
        })
        sheet.addAction(UIAlertAction(title: "复制 API 地址", style: .default) { [weak self] _ in
            guard let self else { return }
            let text: String
            if GatewayProfile.remote {
                // 远程网关的 /v1 在另一台设备上，复制本机回环没有意义。
                text = GatewayProfile.apiBase
            } else {
                let lan = Settings.lan ? (NetworkAddress.localIPv4() ?? "127.0.0.1") : "127.0.0.1"
                text = "http://\(lan):\(Settings.port)/v1"
            }
            UIPasteboard.general.string = text
            self.toast("已复制：\(text)")
        })
        sheet.addAction(UIAlertAction(title: "修改端口", style: .default) { [weak self] _ in
            self?.askPort()
        })
        sheet.addAction(UIAlertAction(title: "远程网关接入…", style: .default) { [weak self] _ in
            self?.showRemoteSettings()
        })
        sheet.addAction(UIAlertAction(title: "本机网关形态…", style: .default) { [weak self] _ in
            self?.showLocalKindSettings()
        })
        let lanTitle = Settings.lan ? "关闭局域网访问" : "开启局域网访问"
        sheet.addAction(UIAlertAction(title: lanTitle, style: .default) { [weak self] _ in
            guard let self else { return }
            Settings.lan.toggle()
            self.restartGateway(port: Settings.port, lan: Settings.lan)
            self.toast(Settings.lan ? "已开启局域网访问（会自动生成 API Key）" : "已关闭局域网访问")
        })
        sheet.addAction(UIAlertAction(title: "查看启动日志", style: .default) { [weak self] _ in
            self?.showLog()
        })
        sheet.addAction(UIAlertAction(title: "诊断信息", style: .default) { [weak self] _ in
            self?.showDiagnostics()
        })
        sheet.addAction(UIAlertAction(title: "关于", style: .default) { [weak self] _ in
            self?.showAbout()
        })
        sheet.addAction(UIAlertAction(title: "取消", style: .cancel, handler: nil))
        if let popover = sheet.popoverPresentationController {
            popover.sourceView = view
            popover.sourceRect = CGRect(x: view.bounds.midX, y: 60, width: 1, height: 1)
        }
        present(sheet, animated: true)
    }

    /// 后台保持设置页：持续后台处理 + 锁屏/灵动岛实时活动三个开关。
    private func showBackgroundSettings() {
        let controller = BackgroundSettingsViewController()
        controller.onChange = { [weak self] in
            self?.syncLiveActivity(force: true)
        }
        let nav = UINavigationController(rootViewController: controller)
        nav.modalPresentationStyle = .formSheet
        present(nav, animated: true)
    }

    private func askPort() {
        let alert = UIAlertController(title: "监听端口", message: "客户端里填这个端口。修改后会重启网关。",
                                      preferredStyle: .alert)
        alert.addTextField { field in
            field.keyboardType = .numberPad
            field.text = String(Settings.port)
        }
        alert.addAction(UIAlertAction(title: "取消", style: .cancel, handler: nil))
        alert.addAction(UIAlertAction(title: "保存", style: .default) { [weak self, weak alert] _ in
            guard let self else { return }
            let text = alert?.textFields?.first?.text ?? ""
            guard let port = Int(text), (1024...65535).contains(port) else {
                self.toast("端口需在 1024–65535 之间")
                return
            }
            Settings.port = port
            self.restartGateway(port: port, lan: Settings.lan)
        })
        present(alert, animated: true)
    }

    /// 远程接入设置：一个地址输入框 + 两个连接动作 + 切回本机。
    ///
    /// 两种网关现在都能在本机运行（Python 走 CPython，Go 走随包签名的静态库）；
    /// 远程接入是可选的连接方式，用于连电脑/服务器上已运行的实例。
    private func showRemoteSettings() {
        let alert = UIAlertController(
            title: "远程网关接入",
            message: "两种网关默认都在本机运行。需要连电脑、服务器或同一网络上已运行的实例时，在这里填它的地址。\n\n留空时按形态用默认端口（Go 7863 / Python 8788）。",
            preferredStyle: .alert)
        alert.addTextField { field in
            field.placeholder = "127.0.0.1:7863"
            field.text = Settings.remoteBase
            field.keyboardType = .URL
            field.autocapitalizationType = .none
            field.autocorrectionType = .no
        }
        alert.addAction(UIAlertAction(title: "连接 Go 网关", style: .default) { [weak self, weak alert] _ in
            self?.connectRemote(raw: alert?.textFields?.first?.text ?? "",
                                kind: GatewayProfile.go)
        })
        alert.addAction(UIAlertAction(title: "连接 Python 网关", style: .default) { [weak self, weak alert] _ in
            self?.connectRemote(raw: alert?.textFields?.first?.text ?? "",
                                kind: GatewayProfile.python)
        })
        alert.addAction(UIAlertAction(title: "切回本机 Python 网关", style: .default) { [weak self] _ in
            self?.switchToLocal(kind: GatewayProfile.python)
        })
        alert.addAction(UIAlertAction(title: "切回本机 Go 网关", style: .default) { [weak self] _ in
            self?.switchToLocal(kind: GatewayProfile.go)
        })
        alert.addAction(UIAlertAction(title: "取消", style: .cancel, handler: nil))
        present(alert, animated: true)
    }

    /// 切换本机形态：先把两边可能残留的监听都停掉，再按新形态启动。
    /// 不这么做的话，Python 的 8788 还占着端口，Go 起来后探活会连到旧网关。
    private func switchToLocal(kind: String) {
        stopLocalGo()
        Settings.remoteEnabled = false
        Settings.localKind = kind
        bootstrapped = false
        loadedURL = nil
        healthFailures = 0
        spinner.startAnimating()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            _ = PyBridge.shared.call("stop", self.payload())
            DispatchQueue.main.async { self.startGateway() }
        }
        toastLater("已切回本机 \(GatewayProfile.label(kind))")
    }

    /// 菜单里的形态选择：不用先绕进「远程接入」也能切 Python / Go。
    private func showLocalKindSettings() {
        let alert = UIAlertController(
            title: "本机网关形态",
            message: "Python 网关由内嵌 CPython 托管；Go 网关由构建期编译进包、随 App 签名的静态库在进程内运行。\n\nGo 网关版本与 App 版本绑定，换版本需要升级 App。",
            preferredStyle: .actionSheet)
        alert.addAction(UIAlertAction(title: "Python 网关（本地托管）", style: .default) { [weak self] _ in
            self?.switchToLocal(kind: GatewayProfile.python)
        })
        alert.addAction(UIAlertAction(title: "Go 网关（随包内置）", style: .default) { [weak self] _ in
            self?.switchToLocal(kind: GatewayProfile.go)
        })
        alert.addAction(UIAlertAction(title: "取消", style: .cancel, handler: nil))
        present(alert, animated: true)
    }

    private func connectRemote(raw: String, kind: String) {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let allowed = CharacterSet(charactersIn:
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-[]:/")
        if !trimmed.isEmpty, trimmed.rangeOfCharacter(from: allowed.inverted) != nil {
            toastLater("地址需形如 127.0.0.1:7863 或 https://host:port")
            return
        }
        Settings.remoteBase = GatewayProfile.normaliseBase(trimmed, forKind: kind)
        Settings.remoteKind = kind
        Settings.remoteEnabled = true
        loadedURL = nil
        healthFailures = 0
        bootstrapped = true
        refreshHealth()
        toastLater("已接入 \(GatewayProfile.base)")
    }

    /// 设置弹窗自己正在退场，立刻再 present 一个 toast 会撞上转场动画。
    private func toastLater(_ text: String) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
            self?.toast(text)
        }
    }

    private func showLog() {
        // 远程接入时网关进程不在本机，日志要看运行网关那一侧。
        if GatewayProfile.remote || GatewayProfile.isLocalGo {
            let message = GatewayProfile.remote
                ? "远程接入模式下没有本地启动日志，请看运行网关那一侧的控制台。"
                : GoBridge.shared.logHint
            let alert = UIAlertController(title: "启动日志", message: message,
                                          preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "好", style: .default, handler: nil))
            present(alert, animated: true)
            return
        }
        let payload = self.payload()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var request = payload
            request["lines"] = 300
            let result = PyBridge.shared.call("log_tail", request)
            let text = (result["text"] as? String) ?? "暂无日志"
            DispatchQueue.main.async {
                let alert = UIAlertController(title: "启动日志", message: String(text.suffix(4000)),
                                              preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: "好", style: .default, handler: nil))
                self?.present(alert, animated: true)
            }
        }
    }

    private func showDiagnostics() {
        let text = Diagnostics.shared.report()
        let alert = UIAlertController(title: "诊断信息",
                                      message: String(text.suffix(3500)),
                                      preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "复制全部", style: .default) { _ in
            UIPasteboard.general.string = text
        })
        alert.addAction(UIAlertAction(title: "好", style: .cancel, handler: nil))
        present(alert, animated: true)
    }

    private func showAbout() {
        let version = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "?"
        let mode = GatewayProfile.remote ? "远程接入"
            : (GatewayProfile.isLocalGo ? "本机运行（随包内置）" : "本地托管")
        let message = """
        版本 \(version)

        OneGateway 是网关宿主容器：仓库本身不含网关源码，App 提供内嵌 CPython、
        构建期编译并随包签名的 Go 静态库、常驻托管与面板外壳。当前为
        \(GatewayProfile.label(GatewayProfile.kind)) · \(mode)。Python 网关由 CPython
        托管、可随包更新；Go 网关在 App 进程内运行，版本与 App 绑定，换版本要升级
        App。也可以连到电脑 / 服务器上已运行的实例。账号、配额与用量统计都在
        网关那一侧处理，不经过第三方服务器。

        面板：\(baseURL)
        API：\(apiBase)
        网关地址：\(GatewayProfile.base)

        提示：iOS 26 起可用「持续后台处理」让网关在后台继续监听（系统会在锁屏和
        灵动岛显示进度卡片）；也可以在「后台保持」里打开自己的锁屏/灵动岛实时活动。
        更早的系统上，进入后台 20 秒后会暂停监听，回到前台自动恢复。
        """
        let alert = UIAlertController(title: "OneGateway", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "好", style: .default, handler: nil))
        present(alert, animated: true)
    }

    private func toast(_ text: String) {
        let alert = UIAlertController(title: nil, message: text, preferredStyle: .alert)
        present(alert, animated: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            alert.dismiss(animated: true)
        }
    }
}

// MARK: - 面板 JS 弹窗

/// WKWebView 不自带 alert/confirm/prompt 的 UI：不设置 uiDelegate 时这三个调用会被静默丢弃，
/// `confirm()` 直接返回 false——面板里所有二次确认（关闭网关、删账号、清空日志）都会表现为
/// 「点了没反应」。这里补上原生对话框，并让 `target=_blank` 就地打开。
extension RootViewController: WKUIDelegate {

    func webView(
        _ webView: WKWebView,
        runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping () -> Void
    ) {
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "好", style: .default) { _ in completionHandler() })
        presentPanelDialog(alert)
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (Bool) -> Void
    ) {
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "取消", style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: "确定", style: .default) { _ in completionHandler(true) })
        presentPanelDialog(alert)
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptTextInputPanelWithPrompt prompt: String,
        defaultText: String?,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (String?) -> Void
    ) {
        let alert = UIAlertController(title: nil, message: prompt, preferredStyle: .alert)
        alert.addTextField { $0.text = defaultText }
        alert.addAction(UIAlertAction(title: "取消", style: .cancel) { _ in completionHandler(nil) })
        alert.addAction(UIAlertAction(title: "确定", style: .default) { _ in
            completionHandler(alert.textFields?.first?.text)
        })
        presentPanelDialog(alert)
    }

    /// 面板的「在新窗口打开运行日志」是 target=_blank；默认不处理就是点了没反应，这里就地打开。
    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if navigationAction.targetFrame == nil {
            webView.load(navigationAction.request)
        }
        return nil
    }

    /// 同一个时刻只允许一个弹窗，避免「already presenting」把 JS 回调卡死。
    private func presentPanelDialog(_ alert: UIAlertController) {
        DispatchQueue.main.async {
            if let presented = self.presentedViewController, !presented.isBeingDismissed {
                presented.dismiss(animated: false) { self.present(alert, animated: true) }
            } else {
                self.present(alert, animated: true)
            }
        }
    }
}

// MARK: - 面板加载状态

/// 面板加载成功/失败、WebKit 内容进程被回收，都记一笔：
/// 「授权成功后闪退」这类问题，如果只是 WebKit 的内容进程被系统收了，App 其实
/// 还活着，只是白屏——记录能把这三种情况区分开。
extension RootViewController: WKNavigationDelegate {

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        Diagnostics.shared.note("panel loaded")
        // 页面脚本已就绪：把系统深浅写进 Go 面板的 localStorage 与 data-theme。
        // Python 面板不认这套变量，注入会安静地失败（每一步都包在 try 里）。
        webView.evaluateJavaScript(
            GatewayProfile.themeScript(dark: systemDark), completionHandler: nil)
    }

    func webView(
        _ webView: WKWebView,
        didFailProvisionalNavigation navigation: WKNavigation!,
        withError error: Error
    ) {
        Diagnostics.shared.note("panel load failed: \(error.localizedDescription)")
        // 允许下一次健康检查成功后重试（否则会一直停在白屏）。
        loadedURL = nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        Diagnostics.shared.note("web content process terminated")
        reloadDashboard()
    }
}
