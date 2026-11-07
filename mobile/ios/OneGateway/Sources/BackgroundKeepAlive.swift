import BackgroundTasks
import Foundation

/// iOS 26 的「持续后台任务」（`BGContinuedProcessingTask`）封装。
///
/// 普通 iOS App 进后台会被挂起，套接字随之失效；这个 API 允许一段「用户发起的工作」
/// 在后台继续跑，并且**系统会自己在锁屏和灵动岛生成一张实时活动卡片**，显示我们给的
/// title / subtitle 与进度，用户可以直接在卡片上取消。
///
/// 三条硬约束，代码里都体现了：
///  1. 请求必须在 App 仍处于前台时提交（这里挂在 `willResignActive`，即用户刚切走、
///     App 还没进后台的瞬间），所以 `submit()` 不要挪到 `didEnterBackground`；
///  2. 必须持续上报进度，长时间不动会被判定「卡死」，系统会主动让用户取消；
///  3. 时长由系统决定（跟电量、后台任务数量有关），到期会调用 `expirationHandler`，
///     此时必须尽快收尾并 `setTaskCompleted`，否则会被强杀。
final class BackgroundKeepAlive {

    static let shared = BackgroundKeepAlive()
    static let taskIdentifier = "com.onegateway.host.gateway"

    /// 一片进度对应 30 分钟：跑满一片就再续一片，进度条单调递增、永远到不了 100%
    /// （到 100% 会被系统当成任务完成而结束后台时间）。
    private static let sliceSeconds: Int64 = 1800

    private(set) var isRunning = false
    private(set) var startedAt: Date?
    private(set) var lastError: String?

    /// 任务到期 / 被用户取消时回调（主线程）。调用方应立刻停掉监听，避免半死连接。
    var onChange: (() -> Void)?
    var onExpire: (() -> Void)?

    private var task: AnyObject?
    private var ticker: Timer?
    private var registered = false
    private var finished = true

    static var isSupported: Bool {
        if #available(iOS 26.0, *) { return true }
        return false
    }

    /// 必须在 `application(_:didFinishLaunchingWithOptions:)` 里调用，且只调一次：
    /// launch handler 的注册晚于启动完成，系统就不会再拉起这个 identifier。
    func register() {
        guard !registered else { return }
        registered = true
        guard #available(iOS 26.0, *) else { return }
        _ = BGTaskScheduler.shared.register(
            forTaskWithIdentifier: Self.taskIdentifier,
            using: nil
        ) { [weak self] task in
            if #available(iOS 26.0, *), let self {
                self.start(task)
            } else {
                task.setTaskCompleted(success: false)
            }
        }
    }

    /// 在前台、且用户刚把 App 切走时提交。重复提交会被系统拒绝，所以先看 `isRunning`。
    func submit() {
        guard Settings.keepAlive, !isRunning else { return }
        guard #available(iOS 26.0, *) else { return }
        let request = BGContinuedProcessingTaskRequest(
            identifier: Self.taskIdentifier,
            title: "OneGateway",
            subtitle: "正在后台保持监听…"
        )
        // .queue：系统忙时排队等；.fail 会直接丢弃，后台保活就断了，所以用默认的排队。
        request.strategy = .queue
        do {
            try BGTaskScheduler.shared.submit(request)
            lastError = nil
        } catch {
            lastError = error.localizedDescription
            notify()
        }
    }

    /// 回到前台后收尾（前台本来就活着，不需要继续占后台时间）。
    func stop() {
        if #available(iOS 26.0, *) { finish(success: true) }
    }

    // MARK: - iOS 26+

    @available(iOS 26.0, *)
    private func start(_ bgTask: BGTask) {
        guard let task = bgTask as? BGContinuedProcessingTask else {
            bgTask.setTaskCompleted(success: false)
            return
        }
        self.task = task
        isRunning = true
        startedAt = Date()
        lastError = nil
        finished = false

        task.expirationHandler = { [weak self] in
            guard let self else { return }
            DispatchQueue.main.async {
                self.finish(success: false)
                self.onExpire?()
            }
        }

        task.progress.totalUnitCount = Self.sliceSeconds
        task.progress.completedUnitCount = 0

        // Timers 会挂到「创建它的那个线程」的 runloop 上：launch handler 不一定在主线程，
        // 直接在原地 scheduledTimer 会得到一个永远不触发的计时器。统一回主线程。
        DispatchQueue.main.async { [weak self] in
            guard let self, self.isRunning else { return }
            task.updateTitle("OneGateway", subtitle: "后台运行中 · 127.0.0.1:\(Settings.port)")
            self.ticker?.invalidate()
            self.ticker = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                self?.tick()
            }
            self.notify()
        }
    }

    @available(iOS 26.0, *)
    private func tick() {
        guard isRunning, let continued = self.task as? BGContinuedProcessingTask else { return }
        let progress = continued.progress
        if progress.completedUnitCount + 1 >= progress.totalUnitCount {
            progress.totalUnitCount += Self.sliceSeconds
        }
        progress.completedUnitCount += 1

        let elapsed = Int(Date().timeIntervalSince(startedAt ?? Date()))
        // 每秒刷一次字幕太吵，也没必要；5 秒一次足够让系统知道任务还活着。
        if elapsed % 5 == 0 {
            continued.updateTitle(
                "OneGateway",
                subtitle: "后台运行 \(Self.elapsedText(elapsed)) · 127.0.0.1:\(Settings.port)"
            )
        }
    }

    @available(iOS 26.0, *)
    private func finish(success: Bool) {
        guard !finished else { return }
        finished = true
        ticker?.invalidate()
        ticker = nil
        let continued = self.task as? BGContinuedProcessingTask
        self.task = nil
        continued?.setTaskCompleted(success: success)
        isRunning = false
        startedAt = nil
        notify()
    }

    // MARK: - 状态

    private func notify() {
        if Thread.isMainThread {
            onChange?()
        } else {
            DispatchQueue.main.async { [weak self] in self?.onChange?() }
        }
    }

    /// 「后台已保持 12 分 30 秒」，给设置页和进度卡用。
    static func elapsedText(_ seconds: Int) -> String {
        if seconds < 60 { return "\(seconds) 秒" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes) 分 \(seconds % 60) 秒" }
        return "\(minutes / 60) 小时 \(minutes % 60) 分"
    }
}
