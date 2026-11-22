# 多语言示例项目（Go / C++）

两个最小可运行项目，各有**一条初始失败的测试**，用来演示非 Python 语言的修复任务与冲突校验。基线失败是刻意的：`Greet("")` / `greet("")` 返回 `Hello, !`，期望 `Hello, guest!`。

## Go

```powershell
cd benchmarks/multilang/go
go test ./...        # 预期 FAIL：Greet("") = "Hello, !"
```

## C++

```powershell
cmake -S benchmarks/multilang/cpp -B benchmarks/multilang/cpp/.build -DCMAKE_BUILD_TYPE=Debug
cmake --build benchmarks/multilang/cpp/.build --config Debug
ctest --test-dir benchmarks/multilang/cpp/.build -C Debug --output-on-failure   # 预期 FAIL
```

Windows 上如果 PATH 里没有 CMake，可以用 Visual Studio 自带的：
`D:\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin`。

## 记录（2026-11-22）

用网页「上传自己的项目 ZIP」把两个目录打包上传，各跑一次真实修复任务：

| 项目 | 语言 | 任务结果 | 步数 | 用时 |
| --- | --- | --- | --- | --- |
| greeting-go | go | 通过，经验已入库 | 7 | 约 19 秒 |
| greeting-cpp | cpp | 通过，经验已入库 | 8 | 约 20 秒 |
| greeting-go（第二次，验证自动比较） | go | 通过 | 7 | 约 20 秒 |

第二次 Go 任务触发了自动比较：候选 1 条、已比较 1 对；缺少语义模型时判定为“语义审阅未共同确认等价，保留双方经验”，两条 Go 规则因此停在待审阅状态。这是设计上的保守行为，不是失败。
