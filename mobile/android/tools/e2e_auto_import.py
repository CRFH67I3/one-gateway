#!/usr/bin/env python3
"""真机端到端测试：首装 → 点「自动导入最新版」→ 下载/解压/起服务 → 面板。

这是给 `MainActivity` 上那个按钮准备的验收脚本：不模拟、不 mock，直接在连着
的手机上点按，靠 uiautomator 的节点树判断界面，靠 `/health` 判断网关真的起来了。

前置条件：
  * 手机上装好了本次构建的 APK（建议先 `adb shell pm clear` 清成首装状态）；
  * `adb` 在 PATH 里，或设 `ADB=/path/to/adb`；
  * 手机能上网（走的是 App 自己的下载逻辑）。

用法：
    python mobile/android/tools/e2e_auto_import.py
    python mobile/android/tools/e2e_auto_import.py --keep     # 结束后不关面板
退出码 0 表示全绿。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

PKG = "com.workbuddy2api.hub"
ACTIVITY = "%s/.MainActivity" % PKG
IMPORT_LABEL = "导入网关项目压缩包"
AUTO_LABEL = "自动导入最新版（联网下载）"
DOWNLOAD_MARKERS = ("正在连接下载地址", "正在下载网关项目")
INSTALL_MARKER = "下载完成，正在解压"

FAILS = []


def adb_path():
    if os.environ.get("ADB"):
        return os.environ["ADB"]
    found = shutil.which("adb")
    if found:
        return found
    for candidate in (
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        "/usr/lib/android-sdk/platform-tools/adb",
    ):
        if os.path.exists(candidate):
            return candidate
    sys.exit("找不到 adb：装好 platform-tools 或设 ADB=/path/to/adb")


ADB = adb_path()


def adb(*args, timeout=120):
    # MSYS_NO_PATHCONV：Git Bash 会把 `/sdcard/x` 改写成 `C:/Program Files/Git/sdcard/x`，
    # 关掉路径转换，设备上的绝对路径才能原样传过去。
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    return subprocess.run([ADB, *args], capture_output=True, timeout=timeout, env=env)


def shell(cmd, timeout=120):
    """跑一条设备端命令。

    adb 会把多余参数用空格拼起来交给设备上的 sh，所以管道、重定向都能直接用，
    不需要自己再套一层 `sh -c`（那会引入两层引号转义）。
    """
    return adb("shell", cmd, timeout=timeout)


def check(name, ok, detail=""):
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + detail))
    if not ok:
        FAILS.append(name)
    return ok


def wait_boot():
    for _ in range(60):
        state = adb("get-state").stdout.decode().strip()
        if state == "device":
            return True
        time.sleep(1)
    return False


def dump_ui():
    """返回 [{'text','desc','cls','bounds'}]，坐标是屏幕像素。"""
    shell("uiautomator dump /sdcard/wb-ui.xml >/dev/null 2>&1")
    raw = adb("exec-out", "cat", "/sdcard/wb-ui.xml")
    xml = raw.stdout.decode("utf-8", "replace")
    nodes = []
    for tag in re.findall(r"<node [^>]*>", xml):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
        bounds = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds", ""))
        if not bounds:
            continue
        nodes.append({
            "text": unescape(attrs.get("text", "")),
            "desc": unescape(attrs.get("content-desc", "")),
            "cls": attrs.get("class", ""),
            "bounds": tuple(int(bounds.group(i)) for i in (1, 2, 3, 4)),
        })
    return nodes


def unescape(text):
    return (text.replace("&#10;", "\n").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))


def label(node):
    return node["text"] or node["desc"]


def find(nodes, needle):
    """按可见文字或 contentDescription 找节点。"""
    for node in nodes:
        if needle in label(node):
            return node
    return None


def find_class(nodes, cls):
    for node in nodes:
        if cls in node["cls"]:
            return node
    return None


def tap(node):
    left, top, right, bottom = node["bounds"]
    shell("input tap %d %d" % ((left + right) // 2, (top + bottom) // 2))


def resumed_activity():
    out = shell("dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity'").stdout
    return out.decode("utf-8", "replace")


def app_foreground():
    return PKG in resumed_activity()


def app_uid():
    out = shell("dumpsys package %s | grep -m1 userId=" % PKG).stdout.decode()
    match = re.search(r"userId=(\d+)", out)
    return int(match.group(1)) if match else -1


def listener_uid(port):
    """哪个 uid 在监听这个端口。

    手机上 `/proc/net/tcp` 每行第 8 列是 uid。这一步是为了把「我们的网关在跑」
    和「端口被别人占着、只是恰好能连上」区分开——后者会让界面假「运行中」，
    并把别人的面板当自己的（同一台手机在 Termux 里跑着另一份网关就是这样）。
    """
    out = shell("cat /proc/net/tcp /proc/net/tcp6 2>/dev/null").stdout.decode()
    needle = ":%04X" % port
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 8:
            continue
        local, state, uid = fields[1], fields[3], fields[7]
        if local.endswith(needle) and state == "0A":
            return int(uid)
    return -1


def status_line(nodes):
    """状态栏那一行（界面里第一个有文字的节点）。"""
    for node in nodes:
        if node["text"].strip():
            return node["text"]
    return ""


def set_port(port):
    """用 App 自己的「端口设置」改端口（顺带验证菜单与设置弹窗没坏）。"""
    menu = find(dump_ui(), "更多")
    if menu is None:
        return False
    tap(menu)
    time.sleep(1)
    item = find(dump_ui(), "端口设置")
    if item is None:
        return False
    tap(item)
    time.sleep(1)
    field = find_class(dump_ui(), "EditText")
    if field is None:
        return False
    tap(field)
    time.sleep(0.4)
    shell("input keyevent KEYCODE_MOVE_END")
    for _ in range(6):
        shell("input keyevent KEYCODE_DEL")
    shell("input text %d" % port)
    time.sleep(0.5)
    ok = find(dump_ui(), "确定")
    if ok is None:
        return False
    tap(ok)
    return True


def health(local_port, device_port):
    """通过 adb forward 从电脑这一侧访问手机上的网关。"""
    adb("forward", "tcp:%d" % local_port, "tcp:%d" % device_port)
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % local_port, timeout=3) as resp:
                body = resp.read(2000).decode("utf-8", "replace")
            if resp.status == 200:
                return body
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="结束后不按返回键")
    parser.add_argument("--port", type=int, default=18799, help="本地转发端口")
    parser.add_argument("--free-port", type=int, default=18788,
                        help="8788 被别的进程占用时，改用这个端口继续测")
    args = parser.parse_args()

    print("== 前置检查")
    if not wait_boot():
        sys.exit("没有连接的设备")
    check("设备已连接", True)
    check("APK 已安装", "package:" in shell("pm list packages %s" % PKG).stdout.decode())
    uid = app_uid()

    print("== 首装界面")
    shell("am force-stop %s" % PKG)
    shell("am start -W -n %s >/dev/null" % ACTIVITY)
    time.sleep(3)
    nodes = dump_ui()
    import_node = find(nodes, IMPORT_LABEL)
    auto_node = find(nodes, AUTO_LABEL)
    check("有「%s」按钮" % IMPORT_LABEL, import_node is not None)
    check("有「%s」按钮" % AUTO_LABEL, auto_node is not None)
    if import_node and auto_node:
        check("自动导入按钮在导入按钮下方",
              auto_node["bounds"][1] > import_node["bounds"][1],
              "import top=%d auto top=%d" % (import_node["bounds"][1], auto_node["bounds"][1]))
    if auto_node is None:
        sys.exit("找不到自动导入按钮，后续步骤无法进行（App 可能已经不是首装状态）")

    print("== 点击自动导入")
    tap(auto_node)
    saw_download = saw_install = False
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(1)
        nodes = dump_ui()
        joined = "\n".join(n["text"] for n in nodes)
        if any(marker in joined for marker in DOWNLOAD_MARKERS):
            saw_download = True
        if INSTALL_MARKER in joined:
            saw_install = True
        if find(nodes, IMPORT_LABEL) is None:
            break
    # 2.5MB 的包在好网下两三秒就下完了，抓不到进度文案不代表没下载。
    print("  [info] 抓到下载进度文案: %s" % ("是" if saw_download else "否（可能太快）"))
    print("  [info] 抓到解压状态文案: %s" % ("是" if saw_install else "否（可能太快）"))

    print("== 导入结果")
    check("仍在 App 内（没有被重启甩回桌面）", app_foreground(), resumed_activity()[:200])

    nodes = dump_ui()
    status = status_line(nodes)
    print("    状态栏: %s" % status[:120])
    # 端口被别人占着（同一台手机在 Termux 里也跑着网关）时，App 应当明说而不是
    # 假装「运行中」并载入别人的面板。确认这一点之后，换个端口继续把主链路测完。
    if "启动失败" in status:
        check("端口冲突时给出可照做的提示", "占用" in status, status[:160])
        print("== 端口被占用，改用 %d 继续" % args.free_port)
        check("能用「端口设置」换端口", set_port(args.free_port), "没找到端口设置弹窗")
        time.sleep(2)
        deadline = time.time() + 90
        while time.time() < deadline:
            status = status_line(dump_ui())
            if "运行中" in status:
                break
            time.sleep(2)
        print("    状态栏: %s" % status[:120])

    check("状态栏显示运行中", "运行中" in status, status[:160])

    port = int(re.search(r"127\.0\.0\.1:(\d+)", status).group(1)) if "127.0.0.1:" in status else 8788
    owner = listener_uid(port)
    check("监听 %d 端口的确实本 App（uid %d）" % (port, uid), owner == uid,
          "实际 uid=%d，说明该端口上跑的是别的进程" % owner)

    body = health(args.port, port)
    check("网关 /health 返回 200", bool(body), "60 次轮询没连上")
    if body:
        check("/health 里带 accounts 字段", "accounts" in body, body[:160])
        print("    health: %s" % body[:160])

    nodes = dump_ui()
    check("首装覆盖层已消失", find(nodes, IMPORT_LABEL) is None)
    panel_text = " | ".join(n["text"].replace("\n", " ") for n in nodes if n["text"])[:200]
    check("面板有内容", len([n for n in nodes if n["text"]]) > 3, "界面几乎空白：%s" % panel_text)
    print("    面板文字: %s" % panel_text)

    if not args.keep:
        shell("am force-stop %s" % PKG)
        adb("forward", "--remove", "tcp:%d" % args.port)

    print()
    if FAILS:
        print("FAILED: %d 项 —— %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
