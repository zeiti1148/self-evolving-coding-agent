# 多语言示例项目（Go / C++ / Python）

三个最小可运行项目，各有**一条初始失败的测试**，用来演示非 Python 语言的修复任务、冲突校验与自动合并。基线失败是刻意的：`Greet("")` / `greet("")` 返回 `Hello, !`，期望 `Hello, guest!`。

## 目录

| 目录 | 语言 | 初始失败 |
| --- | --- | --- |
| `python/` | Python | `greet("")` 返回 `Hello, !` |
| `python-orders/` | Python | `total([])` 返回 `None`，期望 `0` |
| `go/` | Go | `Greet("")` 返回 `Hello, !` |
| `go-pagination/` | Go | `LastPageStart(9, 3)` 返回 `9`，期望 `6` |
| `cpp/` | C++ | `greet("")` 返回 `Hello, !` |
| `cpp-parse/` | C++ | `to_int("")` 抛异常，期望 `-1` |

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

## 自动合并实测（2026-11-23）

在“项目内容互不相同”的独立副本上，每个项目连跑两次**描述完全一致且把实现写死**的任务，等待后台自动比较：

| 项目 | 语言 | 结果 | 说明 |
| --- | --- | --- | --- |
| orders-merge | Python | **合并** | 1 对比较，`equivalent_rules: merged` |
| cpp-merge | C++ | **合并** | 1 对比较，`equivalent_rules: merged` |
| go-merge | Go | 未合并 | 该仓库存在第三条同目标规则，其未判定关系挡住了这对候选 |

同一批任务里还产生了 2 条跨项目的“共同处理原则”（`shared_principle`）：Python 的空输入边界默认值、C++ 的空字符串入口替代值，都显示为“2 份来源 · 已归并”。

合并要同时满足：**同一代码目标**（`文件#函数` 一致）、**行为等价**（模型复核 + 真实测试）、**邻域没有未判定关系**。第三条经验如果没有处理完，会连带挡住前两条的合并，这是保守设计。

## Python（自动合并样例）

`python/` 包含 `greeting.py`、`test_greeting.py`、`pytest.ini`；`pytest.ini` 是上传识别所必需的。

```powershell
cd benchmarks/multilang/python
python -m pytest -q     # 预期 FAIL：test_greet_defaults_empty_name
```

**得到一次真正的自动合并**：把 `python/` 打包上传成项目，然后对**同一个项目**连续提交两次内容相同的任务，描述里把实现写死：

> 调用 greet("") 返回 "Hello, !"，期望 "Hello, guest!"。请严格按以下实现修复：先执行 name = name.strip()；如果 name 为空字符串，则 name = "guest"；最后返回 "Hello, " + name + "!"。不要改动测试文件。

两次修复的实现与行为相同，后台自动比较会判为等价并合并：界面上显示「已自动合并 / 2 份来源 → 1 条规则 / 候选 1 条 · 已比较 1」，目录的「已归并」从 0 变为 1。

实测（2026-11-23）：两次任务分别 8 步、7 步，均成功；自动比较 1 对，结果 `equivalent_rules: merged`。

## 记录（2026-11-22）

用网页「上传自己的项目 ZIP」把两个目录打包上传，各跑一次真实修复任务：

| 项目 | 语言 | 任务结果 | 步数 | 用时 |
| --- | --- | --- | --- | --- |
| greeting-go | go | 通过，经验已入库 | 7 | 约 19 秒 |
| greeting-cpp | cpp | 通过，经验已入库 | 8 | 约 20 秒 |
| greeting-go（第二次，验证自动比较） | go | 通过 | 7 | 约 20 秒 |

第二次 Go 任务触发了自动比较：候选 1 条、已比较 1 对；但由于两次修复对含首尾空白的输入行为不同，判定为不合并、保留双方。这属于正确判定，不是失败。

## 为什么 Go 版本没有合并成功

同一个 Go 项目上，第一次修复保留原始 `name` 参与拼接（`"  Ann  "` → `"Hello,   Ann  !"`），第二次修复会先 `TrimSpace` 再拼接（`"Hello, Ann!"`）。两者对含空白输入的可观察行为不同，自动复核因此拒绝合并；随后一次“写死实现”的 Go 任务虽然代码一致，但保存的经验没有提取到函数符号（`symbols` 为空），合并器判定“没有同目标规则”。Python 样例的符号提取与目标匹配是稳定的，所以用 Python 做自动合并演示。
