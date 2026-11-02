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

    private var baseURL: String { "http://127.0.0.1:\(Settings.port)/" }
    private var apiBase: String { "http://127.0.0.1:\(Settings.port)/v1" }

    override func viewDidLoad() {
        super.viewDidLoad()
        buildUI()
        observeLifecycle()
        startGateway()
        startPolling()
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
            self, selector: #selector(didEnterBackground),
            name: UIApplication.didEnterBackgroundNotification, object: nil)
        NotificationCenter.default.addObserver(
            self, selector: #selector(didBecomeActive),
            name: UIApplication.didBecomeActiveNotification, object: nil)
    }

    @objc private func didEnterBackground() {
        spinner.stopAnimating()
        backgroundTask = UIApplication.shared.beginBackgroundTask { [weak self] in
            // 宽限期被系统提前切断：立刻停下监听，避免半死连接。
            self?.pauseGateway()
            self?.endBackgroundTask()
        }
        let work = DispatchWorkItem { [weak self] in
            self?.pauseGateway()
            self?.endBackgroundTask()
        }
        pauseWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 20, execute: work)
    }

    @objc private func didBecomeActive() {
        pauseWorkItem?.cancel()
        pauseWorkItem = nil
        endBackgroundTask()
        guard bootstrapped else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let state = PyBridge.shared.call("start", self.payload())
            DispatchQueue.main.async {
                self.apply(state)
                self.reloadDashboard()
            }
        }
    }

    private func endBackgroundTask() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }

    private func pauseGateway() {
        guard bootstrapped else { return }
        let payload = self.payload()
        DispatchQueue.global(qos: .utility).async {
            _ = PyBridge.shared.call("stop", payload)
        }
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
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                try PyBridge.shared.bootstrap(corePath: self.paths.bundleCore)
                self.bootstrapped = true
            } catch {
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
            return
        }
        guard state["running"] as? Bool == true else {
            statusDot.backgroundColor = .systemGray3
            statusLabel.text = "网关已停止"
            return
        }
        statusDot.backgroundColor = .systemGreen
        let port = state["port"] as? Int ?? Settings.port
        let accounts = state["accounts"] as? Int ?? 0
        let usable = state["usable"] as? Int ?? 0
        statusLabel.text = "运行中 · 127.0.0.1:\(port) · 账号 \(usable)/\(accounts)"
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
            if statusLabel.text?.hasPrefix("运行中") == true {
                statusDot.backgroundColor = .systemOrange
                statusLabel.text = "正在恢复网关…"
            }
            return
        }
        statusDot.backgroundColor = .systemGreen
        let port = Settings.port
        let accounts = json["accounts"] as? Int ?? 0
        let usable = json["accounts_ready"] as? Int ?? 0
        statusLabel.text = "运行中 · 127.0.0.1:\(port) · 账号 \(usable)/\(accounts)"
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

    private func showAbout() {
        let version = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "?"
        let message = """
        版本 \(version)

        手机上的 WorkBuddy 多账号反向代理网关。网关本体与电脑版是同一套 Python 代码，
        账号、配额、模型目录与用量统计都在本机处理，不经过第三方服务器。

        面板：\(baseURL)
        API：\(apiBase)

        提示：iOS 会在 App 进入后台后挂起进程，回到前台会自动恢复监听。
        """
        let alert = UIAlertController(title: "WorkBuddy 网关", message: message, preferredStyle: .alert)
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
