#!/usr/bin/env python3
"""把外部 Go 网关的 `cmd/server` 改造成可从 Swift 调用的 c-archive 入口。

iOS 不允许 fork/exec 子进程，Go 代码必须静态链接进 App 并在进程内起 goroutine。
但 `cmd/server/main.go` 是典型的 CLI：package main + 阻塞的 main() + flag/os.Args
+ log.Fatalf + signal.NotifyContext。构建期做几处**机械替换**，既不 fork 上游
仓库，也不把网关源码放进本仓库：

  * main() -> runGateway()，由 //export StartGateway 显式启动；
  * log.Fatalf -> fatalf：os.Exit(1) 会杀掉整个 iOS App，改成 panic 再 recover；
  * os.Exit(1) 的自更新分支 -> panic：App 内不允许替换自身二进制；
  * signal.NotifyContext -> gatewayContext()：让 Swift 的 StopGateway 能取消服务；
  * 删掉因此不再使用的 os/signal import。

启动是串行的：每次 StartGateway 同步登记一个 done channel，新 goroutine 先等上一轮
runGateway 真正返回再抢端口。否则「改端口 / 切局域网」的 stop→start 会撞上还没
释放的 listen，触发 log.Fatalf -> panic，表现成「重启之后面板一直转圈」。

替换次数全部断言，上游一改结构就立刻失败，而不是悄悄产出一个跑不起来的库。
用法：python3 prepare-go-bridge.py <解压后的 Go 仓库根目录>
"""
import os
import sys

BRIDGE = r'''package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"context"
	"flag"
	"fmt"
	"os"
	"sync"
)

var (
	gwMu           sync.Mutex
	gwCancel       context.CancelFunc
	gwDone         chan struct{}
	gwStopPending  bool
)

// fatalf 取代 log.Fatalf：iOS 上 os.Exit(1) 会终止整个 App，改成 panic 后由
// StartGateway 的 goroutine recover，错误留在外壳的诊断里。
func fatalf(format string, args ...any) {
	panic(fmt.Sprintf(format, args...))
}

// gatewayContext 让外壳能停掉网关：runGateway 里的 defer stop() 是 no-op，
// 真正的取消由 StopGateway 触发，ctx.Done() 会走 main.go 里现成的
// srv.Shutdown() 优雅退出。gwStopPending 兜住「StartGateway 刚返回、goroutine
// 还没跑到 gatewayContext 就被 StopGateway 点停」这一小段窗口。
func gatewayContext() (context.Context, func()) {
	gwMu.Lock()
	defer gwMu.Unlock()
	if gwCancel != nil {
		gwCancel()
	}
	ctx, cancel := context.WithCancel(context.Background())
	gwCancel = cancel
	if gwStopPending {
		cancel()
		gwStopPending = false
	}
	return ctx, func() {}
}

//export StartGateway
func StartGateway(configPath *C.char) C.int {
	if configPath == nil {
		return 1
	}
	cfg := C.GoString(configPath)

	gwMu.Lock()
	prev := gwDone
	// 即便 Swift 忘了先 StopGateway，也把上一份先取消掉。
	if gwCancel != nil {
		gwCancel()
		gwCancel = nil
	}
	gwStopPending = false
	done := make(chan struct{})
	gwDone = done // 同步登记：紧随其后的 StartGateway 会等这一个，不会并发抢端口
	gwMu.Unlock()

	go func() {
		// 旧监听彻底关闭（含 Shutdown 排干）后再起新的，否则 bind 会失败。
		if prev != nil {
			<-prev
		}
		defer func() {
			_ = recover() // fatalf / 上游 panic 不能带崩整个 App
			gwMu.Lock()
			if gwDone == done {
				gwDone = nil
				gwCancel = nil
			}
			gwMu.Unlock()
			close(done)
		}()
		// runGateway 会重新 flag.String("config")，每次启动都要重置 CommandLine，
		// 否则第二次调用 panic: flag redefined: config。
		flag.CommandLine = flag.NewFlagSet("gateway", flag.ContinueOnError)
		os.Args = []string{"gateway", "-config", cfg}
		runGateway()
	}()
	return 0
}

//export StopGateway
func StopGateway() {
	gwMu.Lock()
	defer gwMu.Unlock()
	if gwCancel != nil {
		gwCancel()
		gwCancel = nil
	} else {
		// 新一轮还没进 gatewayContext：记下来，等它一注册就立刻取消。
		gwStopPending = true
	}
}
'''

REPLACEMENTS = (
    ("func main() {", "func runGateway() {", 1),
    ("log.Fatalf(", "fatalf(", -1),
    ("os.Exit(1)", 'panic("self-update is disabled inside the app")', 1),
    ("ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)",
     "ctx, stop := gatewayContext()", 1),
    ('\t"os/signal"\n', "", 1),
)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: prepare-go-bridge.py <go-repo-root>", file=sys.stderr)
        return 2
    root = sys.argv[1]
    entry = os.path.join(root, "cmd", "server", "main.go")
    if not os.path.isfile(entry):
        print("error: 找不到 cmd/server/main.go", file=sys.stderr)
        return 1

    with open(entry, encoding="utf-8") as fh:
        text = fh.read()
    for old, new, expected in REPLACEMENTS:
        found = text.count(old)
        if expected == 1 and found != 1:
            print(f"error: 期望恰好 1 处 {old!r}，实际 {found} 处；上游结构变了，需要人工核对",
                  file=sys.stderr)
            return 1
        if expected == -1 and found == 0:
            print(f"error: 一处 {old!r} 都没找到", file=sys.stderr)
            return 1
        text = text.replace(old, new)
        print(f"  replaced {old!r}: {found}")

    with open(entry, "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(root, "cmd", "server", "mobile_bridge.go"),
              "w", encoding="utf-8") as fh:
        fh.write(BRIDGE)
    print("  wrote cmd/server/mobile_bridge.go")
    return 0


if __name__ == "__main__":
    sys.exit(main())
