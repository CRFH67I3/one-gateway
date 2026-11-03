import ActivityKit
import Foundation

/// 实时活动（Live Activity）的内容定义。
///
/// 这份文件同时挂在主 App 与 Widget Extension 两个 target 上（见 project.yml 的
/// `Shared`）：ActivityKit 靠「类型名 + Codable 结构」把两边配对，并不要求两边是
/// 同一个二进制 symbol，Apple 自己的示例也是共享源码的写法。
///
/// 这样也就不需要 App Group —— 锁屏 / 灵动岛的显隐开关直接放进 `ContentState`，
/// 每次 `update` 跟着内容一起下发，Widget 侧从 `context.state` 里读就行。
struct GatewayActivityAttributes: ActivityAttributes {

    /// 会变的部分。
    struct ContentState: Codable, Hashable {
        /// 账号总数。
        var accountsTotal: Int
        /// 其中可用的账号数。
        var accountsReady: Int
        /// 累计请求数（展示用）。
        var requests: Int
        /// 当前监听端口。
        var port: Int
        /// 网关是否在监听。
        var running: Bool
        /// 锁屏卡片是否渲染内容。
        var showOnLockScreen: Bool
        /// 灵动岛的紧凑 / 展开视图是否渲染内容。
        var showInDynamicIsland: Bool
        /// 一行补充说明，例如「后台保持中」。
        var note: String
    }

    /// 活动创建时间，用于展示运行时长。
    var startedAt: Date
    /// 面板端口，创建时写入，之后不变。
    var panelPort: Int
}
