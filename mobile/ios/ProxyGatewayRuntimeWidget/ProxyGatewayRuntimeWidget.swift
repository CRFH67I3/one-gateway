import ActivityKit
import SwiftUI
import WidgetKit

/// Widget Extension 的唯一入口。
///
/// 这里只放实时活动（Live Activity）：锁屏卡片 + 灵动岛。App 图标下的桌面小组件
/// 不做，免得用户以为要额外配置。
@main
struct ProxyGatewayRuntimeWidgetBundle: WidgetBundle {
    var body: some Widget {
        GatewayLiveActivity()
    }
}

struct GatewayLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: GatewayActivityAttributes.self) { context in
            LockScreenCard(state: context.state)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label {
                        Text("网关")
                    } icon: {
                        Image(systemName: "bolt.horizontal.circle.fill")
                    }
                    .font(.caption2)
                    .foregroundStyle(context.state.running ? Color.green : Color.orange)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(":\(context.state.port)")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    HStack(spacing: 8) {
                        Text("账号 \(context.state.accountsReady)/\(context.state.accountsTotal)")
                        Spacer(minLength: 0)
                        Text(context.state.note)
                            .lineLimit(1)
                    }
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                }
            } compactLeading: {
                IslandIcon(
                    visible: context.state.showInDynamicIsland,
                    running: context.state.running
                )
            } compactTrailing: {
                Group {
                    if context.state.showInDynamicIsland {
                        Text("\(context.state.accountsReady)")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.green)
                    }
                }
            } minimal: {
                IslandIcon(
                    visible: context.state.showInDynamicIsland,
                    running: context.state.running
                )
            }
            .keylineTint(.green)
        }
    }
}

/// 锁屏卡片。「锁屏实时活动」关掉时返回空视图（此时活动本身也会被 App 撤掉，
/// 这里只是双保险）。
private struct LockScreenCard: View {
    let state: GatewayActivityAttributes.ContentState

    var body: some View {
        Group {
            if state.showOnLockScreen {
                HStack(alignment: .center, spacing: 12) {
                    Image(systemName: "bolt.horizontal.circle.fill")
                        .font(.system(size: 26))
                        .foregroundStyle(state.running ? Color.green : Color.orange)

                    VStack(alignment: .leading, spacing: 2) {
                        Text("中转网关")
                            .font(.headline)
                        Text("127.0.0.1:\(state.port) · 账号 \(state.accountsReady)/\(state.accountsTotal)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(state.note)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }

                    Spacer(minLength: 0)

                    Text(state.running ? "运行中" : "已停止")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(state.running ? Color.green : Color.orange)
                }
                .padding(12)
            }
        }
    }
}

/// 灵动岛上的那个小图标；开关关掉时什么都不渲染。
private struct IslandIcon: View {
    let visible: Bool
    let running: Bool

    var body: some View {
        Group {
            if visible {
                Image(systemName: "bolt.horizontal.circle.fill")
                    .foregroundStyle(running ? Color.green : Color.orange)
            }
        }
    }
}
