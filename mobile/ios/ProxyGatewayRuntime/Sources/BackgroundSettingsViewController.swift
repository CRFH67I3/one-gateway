import UIKit

/// 「后台保持」设置页。
///
/// 三个开关对应三种保留后台的手段：
///  1. 持续后台任务（iOS 26 的 `BGContinuedProcessingTask`）—— 让网关进后台还能listen；
///  2. 锁屏实时活动 —— App 自己画的锁屏卡片；
///  3. 灵动岛实时活动 —— 同一张活动在灵动岛的紧凑/展开视图。
///
/// 2 和 3 是同一张 Live Activity 的两个展示面：关掉 2 会移除整张活动（连灵动岛一起消失），
/// 关掉 3 只让灵动岛不再渲染内容、锁屏卡片保留。
final class BackgroundSettingsViewController: UITableViewController {

    /// 任一开关变化后通知主界面刷新（重建/移除实时活动、提交后台任务）。
    var onChange: (() -> Void)?

    private struct Row {
        var title: String
        var subtitle: String?
        var kind: Kind

        enum Kind {
            case toggle(isOn: Bool, enabled: Bool, changed: (Bool) -> Void)
            case button(destructive: Bool, tap: () -> Void)
            case info(value: String)
        }
    }

    private struct Section {
        var title: String
        var footer: String?
        var rows: [Row]
    }

    private var sections: [Section] = []

    init() {
        super.init(style: .insetGrouped)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "后台保持"
        navigationItem.rightBarButtonItem = UIBarButtonItem(
            title: "完成", style: .done, target: self, action: #selector(dismissSelf))
        rebuild()
    }

    @objc private func dismissSelf() {
        dismiss(animated: true)
    }

    // MARK: - 数据

    private func rebuild() {
        var sections: [Section] = []

        // 1. 持续后台任务
        let supported = BackgroundKeepAlive.isSupported
        sections.append(Section(
            title: "保留后台",
            footer: supported
                ? "iOS 26 的持续后台任务。开启后切到后台，系统会继续给网关后台时间，并在锁屏与灵动岛显示一张进度卡片，用户可在卡片上直接取消。实际时长由系统按电量与后台任务数量决定；到期后网关会停止监听，回到前台自动恢复。"
                : "当前系统版本不支持持续后台任务（需要 iOS 26 及以上）。离开 App 20 秒后网关会停止监听，回到前台自动恢复。",
            rows: [
                Row(
                    title: "持续后台处理",
                    subtitle: supported ? "切到后台后由系统继续运行网关（含系统进度卡片）" : "需要 iOS 26",
                    kind: .toggle(
                        isOn: Settings.keepAlive,
                        enabled: supported,
                        changed: { [weak self] on in
                            Settings.keepAlive = on
                            if on {
                                BackgroundKeepAlive.shared.submit()
                            } else {
                                BackgroundKeepAlive.shared.stop()
                            }
                            self?.changed()
                        })
                )
            ]
        ))

        // 2. 实时活动
        let liveEnabled = Settings.liveActivity
        var liveRows: [Row] = [
            Row(
                title: "锁屏实时活动",
                subtitle: "在锁屏显示端口、账号可用数与请求数",
                kind: .toggle(
                    isOn: liveEnabled,
                    enabled: true,
                    changed: { [weak self] on in
                        Settings.liveActivity = on
                        if !on {
                            LiveActivityController.shared.endNow()
                        }
                        self?.changed()
                    })
            ),
            Row(
                title: "灵动岛显示",
                subtitle: liveEnabled ? "在灵动岛紧凑显示运行状态" : "先开启锁屏实时活动",
                kind: .toggle(
                    isOn: Settings.dynamicIsland,
                    enabled: liveEnabled,
                    changed: { [weak self] on in
                        Settings.dynamicIsland = on
                        self?.changed()
                    })
            ),
        ]
        if LiveActivityController.shared.isActive {
            liveRows.append(Row(
                title: "立即移除实时活动",
                subtitle: "从锁屏和灵动岛撤下当前卡片（开关状态不变）",
                kind: .button(destructive: true, tap: { [weak self] in
                    LiveActivityController.shared.endNow()
                    self?.changed()
                })
            ))
        }
        sections.append(Section(
            title: "实时活动",
            footer: "锁屏与灵动岛是同一张实时活动：关掉「锁屏实时活动」会立即移除整张活动，灵动岛一起消失；「灵动岛显示」只影响灵动岛，锁屏卡片保留。系统设置里关闭了本 App 的「实时活动」时，这里开了也不会显示。",
            rows: liveRows
        ))

        // 3. 状态
        sections.append(Section(
            title: "状态",
            footer: nil,
            rows: [
                Row(title: "后台任务", subtitle: nil,
                    kind: .info(value: keepAliveStatus())),
                Row(title: "实时活动", subtitle: nil,
                    kind: .info(value: LiveActivityController.shared.isActive ? "显示中" : "未显示")),
            ]
        ))

        self.sections = sections
        tableView.reloadData()
    }

    private func keepAliveStatus() -> String {
        guard BackgroundKeepAlive.isSupported else { return "系统不支持" }
        guard Settings.keepAlive else { return "已关闭" }
        if let started = BackgroundKeepAlive.shared.startedAt, BackgroundKeepAlive.shared.isRunning {
            let seconds = Int(Date().timeIntervalSince(started))
            return "运行中 · 已保持 \(BackgroundKeepAlive.elapsedText(seconds))"
        }
        if let error = BackgroundKeepAlive.shared.lastError, !error.isEmpty {
            return "待命（上次提交失败：\(error)）"
        }
        return "待命（切到后台后启动）"
    }

    private func changed() {
        rebuild()
        onChange?()
    }

    // MARK: - Table

    override func numberOfSections(in tableView: UITableView) -> Int {
        sections.count
    }

    override func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        sections[section].rows.count
    }

    override func tableView(_ tableView: UITableView, titleForHeaderInSection section: Int) -> String? {
        sections[section].title
    }

    override func tableView(_ tableView: UITableView, titleForFooterInSection section: Int) -> String? {
        sections[section].footer
    }

    override func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let row = sections[indexPath.section].rows[indexPath.row]
        let cell = UITableViewCell(style: .subtitle, reuseIdentifier: nil)
        cell.textLabel?.text = row.title
        cell.detailTextLabel?.text = row.subtitle
        cell.detailTextLabel?.numberOfLines = 0
        cell.selectionStyle = .none

        switch row.kind {
        case let .toggle(isOn, enabled, changed):
            let toggle = UISwitch()
            toggle.isOn = isOn
            toggle.isEnabled = enabled
            toggle.addAction(UIAction { [weak toggle] _ in
                changed(toggle?.isOn ?? false)
            }, for: .valueChanged)
            cell.accessoryView = toggle
        case let .button(destructive, _):
            cell.textLabel?.textColor = destructive ? .systemRed : .label
            cell.selectionStyle = .default
            cell.accessoryView = nil
        case let .info(value):
            cell.accessoryType = .none
            cell.accessoryView = nil
            let label = UILabel()
            label.text = value
            label.font = .systemFont(ofSize: 14)
            label.textColor = .secondaryLabel
            label.textAlignment = .right
            label.sizeToFit()
            cell.accessoryView = label
            cell.selectionStyle = .none
        }
        return cell
    }

    override func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        if case let .button(_, tap) = sections[indexPath.section].rows[indexPath.row].kind {
            tap()
        }
    }
}
