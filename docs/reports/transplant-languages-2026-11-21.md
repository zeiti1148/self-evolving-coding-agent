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

## 2026-11-21 追加：真实 Maven 交叉验证（Java）

新增 `backend/tests/test_java_conflict_verification.py`，默认跳过（`RUN_MAVEN_CONFLICT=1` 才执行），避免拖慢 CI。它构造两个真实 Maven 项目，各自修好同一个方法但行为相反：

- 项目 0：`Converter.convert` 返回 1，测试断言 `convert(1) == 1`。
- 项目 1：`Converter.convert` 返回 2，测试断言 `convert(1) == 2`。
- 修复前两者都不通过实测，修复后各自通过；随后用 `ConflictVerifier.verify` 对 `src/main/java/demo/Converter.java#Converter.convert` 做 2 次自测 + 4 次交叉移植。

本机实测（41.9 秒，全部为真实 Maven 执行）：

| 结果 | 次数 | 说明 |
| --- | ---: | --- |
| 自测通过 | 2 | 各自实现通过自己的完整测试 |
| 交叉失败 | 4 | 交换实现后失败 1 项、无错误、无跳过，且复现了来源的原始失败 |
| 用例总数 | 每轮 1 项 | 与项目测试配置一致 |

过程中修掉了两个真实缺陷：

1. `transplant.changed()` 原先比较的是**签名**而不是函数体，导致"经验声称的目标没有对应实际源码修改"被误判。现在 Python 比较函数体身份，其他语言比较 body 字节区间。
2. Windows `MAX_PATH`：pytest 的 `tmp_path` 加上任务 UUID 后，Maven 的 `target/surefire-reports` 路径超过 260 字符，`rglob` 抛错。测试改用短工作目录；**这是测试夹具的规避，生产环境的 workspace_root 仍是配置项，长路径场景尚未处理**。

## 限制

- 真实样例目前只有 Java 且是受控小项目；Go/C++ 仍只有单元级片段证据，不能推断真实项目的冲突检出率。
- 宏展开、跨文件声明不在支持范围内。
- 语法包属于可选依赖（`requirements-semantic.txt`）。未安装时这些语言一律走人工确认，功能降级但不会崩溃。
- Windows 长路径问题只在测试夹具里规避，未在生产路径上解决。

## 2026-11-21 追加：重载消歧

原先只要同名函数多于一个就拒绝，"重载一律转人工"。现在目标可以写成带参数类型的形式：

| 目标写法 | 行为 |
| --- | --- |
| `Greeting.greet`（有重载） | 拒绝，提示写出参数签名 |
| `Greeting.greet(String)` | 只在参数类型为 `String` 的重载上拼接 |
| `Greeting.greet(int)`（源码无此签名） | 拒绝："该参数签名未能唯一定位函数" |

实现要点：参数按**类型**比较，不带形参名（`String name` 与 `String other` 视为同一签名）；把类型文本去空白后逐项拼接比较，因此泛型、数组、注解写法必须与源码一致。C++ 的引用与 `const` 修饰需写全。

测试（`backend/tests/test_transplant.py` 新增 3 项，合计 16 项）：无签名拒绝、签名选中单个重载且不触碰另一个重载、未知签名拒绝。冲突验证相关回归 42 项通过。

仍然转人工的情况：模板函数、Go 方法缺少接收者、参数类型写法无法对齐（例如源码用别名类型而经验写原始类型）。

## 后续

1. 用真实 Java / Go 工程各构造一对"同符号不同行为"的经验，跑一次完整交叉验证，记录判定与拒绝理由分布。
2. 根据拒绝理由分布决定是否需要进一步支持类型别名归一化（例如 `List` 与 `java.util.List`）。
