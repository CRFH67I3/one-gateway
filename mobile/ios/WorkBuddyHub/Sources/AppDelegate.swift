import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        // 自诊断放在最前面：先装好崩溃处理器与「本次运行」标记，再去做任何可能出事的事。
        Diagnostics.shared.start()

        // 持续后台任务的 launch handler 必须在启动完成前注册：晚了系统不会再把
        // identifier 拉起，后台保活就永远不会生效。
        BackgroundKeepAlive.shared.register()

        // 正常进后台就撤掉标记；只有「在前台直接没了」才会留下 .running，
        // 下次启动时由 Diagnostics 报出来。
        NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil, queue: .main
        ) { _ in
            Diagnostics.shared.markClean()
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil, queue: .main
        ) { _ in
            Diagnostics.shared.markForeground()
        }

        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = RootViewController()
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
}
