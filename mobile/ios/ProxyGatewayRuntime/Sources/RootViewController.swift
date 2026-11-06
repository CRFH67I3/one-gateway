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

    private var baseURL: String { "http://127.0.0.1:\(Settings.port)/" }
    private var apiBase: String { "http://127.0.0.1:\(Settings.port)/v1" }

    override func viewDidLoad() {
        super.viewDidLoad()
        buildUI()
        observeLifecycle()
        wireBackgroundKeepAlive()
        startGateway()
        startPolling()
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
        // 已经回到前台就别再停了：后台计时器/后台任务到期通知可能晚到一步。
        guard bootstrapped, !isForeground else { return }
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

    private func payload(port: Int? = nil, lan: Bool? = nil) -> [String: Any] {
        ["core": paths.bundleCore,
         "data": paths.data,
         "port": port ?? Settings.port,
         "lan": lan ?? Settings.lan]
    }

    private func startGateway() {
        spinner.startAnimating()
        Diagnostics.shared.note("startGateway")
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
        guard let url = URL(string: "http://127.0.0.1:\(Settings.port)/health") else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            guard let self else { return }
            var parsed: [String: Any]?
            if let data,
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
            if healthFailures == 3, isForeground, bootstrapped, lastRunning {
                Diagnostics.shared.note("health failed 3x, restarting gateway")
                lastRunning = false
                resumeGateway()
            }
            return
        }
        healthFailures = 0
        statusDot.backgroundColor = .systemGreen
        let port = Settings.port
        let accounts = json["accounts"] as? Int ?? 0
        let usable = json["accounts_ready"] as? Int ?? 0
        statusLabel.text = "运行中 · 127.0.0.1:\(port) · 账号 \(usable)/\(accounts)"
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
            let lan = Settings.lan ? (NetworkAddress.localIPv4() ?? "127.0.0.1") : "127.0.0.1"
            let text = "http://\(lan):\(Settings.port)/v1"
            UIPasteboard.general.string = text
            self.toast("已复制：\(text)")
        })
        sheet.addAction(UIAlertAction(title: "修改端口", style: .default) { [weak self] _ in
            self?.askPort()
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

    private func showLog() {
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
        let message = """
        版本 \(version)

        手机上的 WorkBuddy 多账号反向代理网关。网关本体与电脑版是同一套 Python 代码，
        账号、配额、模型目录与用量统计都在本机处理，不经过第三方服务器。

        面板：\(baseURL)
        API：\(apiBase)

        提示：iOS 26 起可用「持续后台处理」让网关在后台继续监听（系统会在锁屏和
        灵动岛显示进度卡片）；也可以在「后台保持」里打开自己的锁屏/灵动岛实时活动。
        更早的系统上，进入后台 20 秒后会暂停监听，回到前台自动恢复。
        """
        let alert = UIAlertController(title: "中转网关", message: message, preferredStyle: .alert)
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
