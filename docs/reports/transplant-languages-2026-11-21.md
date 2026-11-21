# 冲突交叉验证的语言适配（2026-11-21）

对应阶段五设计第二节。本轮把"只能验证可定位的 Python 函数"扩展到 Java、Go、C++，并把移植逻辑从 API 层抽成可复用模块。不宣称这些语言的冲突判定准确率，因为还没有真实工程样例。

## 起点核查

- `backend/api/conflict_verification.py` 原先只允许 Python：语言检查与 `.py` 路径检查两道。
- 执行与证据采集其实已支持多语言（Surefire XML / CTest XML / `go test -json`），缺口只在"目标映射 + 实现拼接"。
- `backend/experience/grounding.py` 只对 Python/Java 提取符号，Go/C++ 退化为文件范围。
- 2026-11-21 核实 PyPI：`tree-sitter-go 0.25.0`、`tree-sitter-cpp 0.23.4` 均有 `win_amd64` wheel。

## 改动

1. 新增 `backend/experience/transplant.py`
   - Python 分支逐行保留原 AST 行为（签名、辅助函数依赖、作用域列检查、docstring 处理不变）。
   - Java / Go / C++ 用 tree-sitter 定位唯一目标，只替换**函数体**，目标声明保持原样。
   - 统一拒绝口径：语言不支持、语法包未安装、文件无法解析、目标不唯一、声明不同、函数体相同、模板函数、Go 方法缺少接收者——全部抛 `VerificationUnavailable`，进入人工确认。
   - `qualified_names()` 与 `grounding.symbols()` 共用同一套命名，避免"作用域判定"和"冲突拼接"各有一套。
2. `backend/api/conflict_verification.py` 改为调用新模块；语言与扩展名检查改为按能力判断。
3. `backend/experience/grounding.py` 的 Go/C++ 改用 `qualified_names()`；语法包缺失时仍退化为文件范围。
4. 依赖：`requirements-semantic.txt` 增加 `tree-sitter-go`、`tree-sitter-cpp`（可选，不进入基础依赖）。

## 实测（本机，2026-11-21）

| 测试集 | 结果 |
| --- | --- |
| 新增 `test_transplant.py` | 13 项通过 |
| 冲突验证相关 4 个文件（含既有真实交叉验证） | 53 项通过 |

覆盖的行为：

- Python：移植只替换函数体、签名不同拒绝、外部依赖不同拒绝、相同实现拒绝（沿用既有断言）。
- Java：方法级拼接成功，目标缺失时拒绝。
- Go：顶层函数拼接成功；方法必须写成 `接收者.方法`，只写方法名被拒绝。
- C++：成员函数拼接成功；模板成员明确拒绝，转人工。
- 命名一致：同一份源码在 `qualified_names()` 与 `grounding.symbols()` 下结果相同；无法解析时都返回空集合（文件范围）。

## 限制

- 没有 Java/Go/C++ 的真实工程冲突样例。当前证据只到"受控片段能定位、能拼接、能拒绝"，不能推断真实项目的冲突检出率。
- C++ 的宏展开、重载、跨文件声明不在支持范围内；重载同名函数会因"无法唯一定位"进入人工确认。
- 语法包属于可选依赖（`requirements-semantic.txt`）。未安装时这些语言一律走人工确认，功能降级但不会崩溃。
- 本轮没有真实模型调用。

## 后续

1. 用真实 Java / Go 工程各构造一对"同符号不同行为"的经验，跑一次完整交叉验证，记录判定与拒绝理由分布。
2. 根据拒绝理由分布决定是否需要支持重载消歧（例如按参数类型选择目标）。
