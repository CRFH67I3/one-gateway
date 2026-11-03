import ActivityKit
import Foundation
import UIKit

/// 自己发起的实时活动（Live Activity）：锁屏一张卡片 + 灵动岛紧凑/展开视图。
///
/// 与 `BackgroundKeepAlive` 里那张系统卡片的区别：
///  * 持续后台任务的卡片由系统生成，内容是 title/subtitle 与进度，样式不可改、也关不掉
///    （关掉开关即不提交任务，卡片自然消失）；
///  * 这里的卡片是我们自己的 `ActivityConfiguration`，能显示账号数、端口、请求数，
///    锁屏与灵动岛分别有开关。
///
/// iOS 的约束：同一个 App 同一时刻只会有一张我们自己的实时活动在展示，且只能在
/// **前台**创建；后台只能更新已存在的那一张。所以 `request` 只在设置页操作或前台
/// 轮询时发生。
///
/// 部署版本是 15.0，而 ActivityKit 从 16.1 才有：`Activity<...>` 不能直接声明成存储
/// 属性（会报 'Activity' is only available in iOS 16.1 or newer），所以存储用 `Any?`
/// 擦除，具体类型只在 @available 分支里还原——和 `BackgroundKeepAlive` 把
/// `BGContinuedProcessingTask` 存成 `AnyObject` 是同一个理由。
final class LiveActivityController {

    static let shared = LiveActivityController()

    private var activityRef: Any?
    private var stateRef: Any?
    private var lastUpdateAt = Date.distantPast
    private(set) var lastError: String?

    /// 更新节流：Live Activity 的刷新有系统预算，没必要 3 秒一次全量推。
    private let minInterval: TimeInterval = 10

    var isActive: Bool {
        if #available(iOS 16.2, *) { return activity != nil }
        return false
    }

    // MARK: - 类型擦除的存取

    @available(iOS 16.2, *)
    private var activity: Activity<GatewayActivityAttributes>? {
        get { activityRef as? Activity<GatewayActivityAttributes> }
        set { activityRef = newValue }
    }

    @available(iOS 16.2, *)
    private var lastState: GatewayActivityAttributes.ContentState? {
        get { stateRef as? GatewayActivityAttributes.ContentState }
        set { stateRef = newValue }
    }

    /// App 启动时清掉上一次进程遗留的活动（App 被杀后卡片可能还挂在锁屏上）。
    func resetStale() {
        guard #available(iOS 16.2, *) else { return }
        Task { @MainActor in
            if #available(iOS 16.2, *) { await self.resetStaleNow() }
        }
    }

    /// 把网关当前状态同步到实时活动。
    ///
    /// - Parameter force: 用户刚拨了开关，绕过节流立刻生效。
    func sync(
        port: Int,
        accounts: Int,
        usable: Int,
        requests: Int,
        running: Bool,
        note: String,
        force: Bool = false
    ) {
        guard #available(iOS 16.2, *) else { return }

        // 总开关关掉就立刻移除卡片，而不是等它自己过期。
        guard Settings.liveActivity, running else {
            endNow()
            return
        }

        let state = GatewayActivityAttributes.ContentState(
            accountsTotal: accounts,
            accountsReady: usable,
            requests: requests,
            port: port,
            running: running,
            showOnLockScreen: Settings.liveActivity,
            showInDynamicIsland: Settings.dynamicIsland,
            note: note
        )

        if !force {
            if state == lastState { return }
            if Date().timeIntervalSince(lastUpdateAt) < minInterval { return }
        }

        // 系统设置里关掉了本 App 的实时活动时，request 会抛 denied，提前挡一下。
        guard ActivityAuthorizationInfo().areActivitiesEnabled else {
            lastError = "系统设置里关闭了「实时活动」"
            return
        }

        Task { @MainActor in
            if #available(iOS 16.2, *) { await self.apply(state, port: port) }
        }
    }

    /// 关闭开关、或网关停掉时调用：立即从锁屏和灵动岛移除。
    func endNow() {
        guard #available(iOS 16.2, *) else { return }
        guard let current = activity else { return }
        // 同步把状态清掉，设置页 reload 时「立即移除」那行才会立刻消失；
        // 真正的 end 是异步的，交给 MainActor 去做。
        activity = nil
        lastState = nil
        lastUpdateAt = .distantPast
        Task { @MainActor in
            if #available(iOS 16.2, *) {
                await current.end(nil, dismissalPolicy: .immediate)
            }
        }
    }

    // MARK: - iOS 16.2+

    @available(iOS 16.2, *)
    @MainActor
    private func resetStaleNow() async {
        for stale in Activity<GatewayActivityAttributes>.activities {
            await stale.end(nil, dismissalPolicy: .immediate)
        }
        activity = nil
        lastState = nil
        lastUpdateAt = .distantPast
    }

    @available(iOS 16.2, *)
    @MainActor
    private func apply(_ state: GatewayActivityAttributes.ContentState, port: Int) async {
        // 内容带 staleDate：App 被系统挂起后，卡片会自动变灰而不是一直显示旧数据。
        let content = ActivityContent(
            state: state,
            staleDate: Date().addingTimeInterval(300)
        )

        if let activity = activity {
            await activity.update(content)
            lastState = state
            lastUpdateAt = Date()
            return
        }

        // 后台不允许新建活动（Apple 只允许前台 request），等下次回前台再建。
        guard UIApplication.shared.applicationState == .active else { return }
        do {
            activity = try Activity.request(
                attributes: GatewayActivityAttributes(startedAt: Date(), panelPort: port),
                content: content,
                pushType: nil
            )
            lastState = state
            lastUpdateAt = Date()
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }
}
