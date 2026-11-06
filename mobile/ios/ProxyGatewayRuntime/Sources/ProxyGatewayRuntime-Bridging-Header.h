//
//  ProxyGatewayRuntime-Bridging-Header.h
//
//  把 CPython 的 C API 直接暴露给 Swift。
//
//  为什么不写 `import Python`：Python.framework 是否带 Clang module map 取决于
//  支持包版本，一旦缺失 Swift 编译期就报 "no such module 'Python'"。官方文档给的
//  Objective-C 路径（`#include <Python/Python.h>`）只依赖 framework 的 Headers
//  目录，更稳，因此统一走桥接头。
//

#import <Foundation/Foundation.h>
#include <Python/Python.h>
