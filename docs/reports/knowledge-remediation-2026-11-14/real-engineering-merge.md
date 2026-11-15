# 真实工程：独立修复、经验合并与扩展验证

本次使用两个真实开源工程的历史缺陷源码，不再用小型业务函数代替工程。合并输入来自两个项目分别修复后，由现有提取器保存的原始经验；原文没有为匹配合并主题而改写。

## 来源与规模

| 工程 | 上游缺陷提交 | 生产 Python 规模 | 上游修复 |
|---|---|---|---|
| pytest-dev/pytest | `3c1534944cbd34e8a41bc9e76818018fadefc9a1` | 65 文件 / 28,798 行 | [#10356](https://github.com/pytest-dev/pytest/pull/10356) |
| sphinx-doc/sphinx | `dd1615c59dc6fff633e27dbb3861f2d27e1fb976` | 183 文件 / 67,209 行 | [#8548](https://github.com/sphinx-doc/sphinx/pull/8548) |


来源为 [SWE-bench Verified 官方数据](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)，两项难度标签均为人工 1–4 小时。源码完整归档和官方 test_patch 固定；许可证随源码保留。没有向修复模型提供官方 patch 或 hints_text。

## 不掩盖首次失败

第一轮 pytest 的 89 项和 Sphinx 的 6 项选定测试通过，但扩展测试揭示：pytest 的单个 MarkDecorator 被错误当作列表展开，导致 2 项原有测试回归；Sphinx 只处理当前模块缓存，跨模块真实 HTML 的 6 个用例仍失败。第一轮资料被合并门槛拦截。

第二轮将这些用例明确提升为回归测试，从原始缺陷代码重新独立修复。pytest 通过 266 项；Sphinx 在 45 次决策预算内仍有 6 项失败，运行状态为 stopped，未生成新经验。接着只继续 Sphinx 的失败副本，并提供“检查成员发现之后的过滤与 inherited-members 默认 object 截断”的诊断摘要；未提供 pytest 经验或官方补丁。故最终 Sphinx 结果是有诊断辅助的续跑，不能称为一次无提示成功。

初轮使用 Python 3.10 时 Sphinx 有 6 个不相关的兼容失败，参考补丁也失败。最终改用该历史项目测试矩阵支持的 Python 3.9；此前兼容失败消失。旧结果仍保留。

## 最终修复与同条件对照

| 工程 | 版本与测试集 | 实测结果 |
|---|---|---|
| pytest | before-component | 259 通过 / 7 失败 / 1 跳过 |
| pytest | before-holdout | 3 通过 / 33 失败 / 0 跳过 |
| pytest | model-component | 266 通过 / 0 失败 / 1 跳过 |
| pytest | model-holdout | 36 通过 / 0 失败 / 0 跳过 |
| pytest | reference-component | 266 通过 / 0 失败 / 1 跳过 |
| pytest | reference-holdout | 36 通过 / 0 失败 / 0 跳过 |
| sphinx | before-component | 135 通过 / 7 失败 / 1 跳过 |
| sphinx | before-holdout | 3 通过 / 3 失败 / 0 跳过 |
| sphinx | model-component | 142 通过 / 0 失败 / 1 跳过 |
| sphinx | model-holdout | 6 通过 / 0 失败 / 0 跳过 |
| sphinx | reference-component | 136 通过 / 6 失败 / 1 跳过 |
| sphinx | reference-holdout | 3 通过 / 3 失败 / 0 跳过 |

component 包含完整选定组件及第一轮转入的回归测试；holdout 是新的未见测试，pytest 为多层 27 种标记表示组合、6 种基类排列及 3 种非类输入，Sphinx 为默认继承和显式基类截断的实际 HTML。上游已有的 pytest xfail 与缺少可选 Cython 的 Sphinx 测试始终单列；没有把它们计作通过。

Sphinx 官方参考补丁在额外 HTML 场景失败，说明这些场景超出该基准原有回归覆盖；这里展示真实输出差异，不将参考补丁当作所有新需求的正确性判据。

## 自动合并得到的共享原则

**以显式 MRO 遍历修复继承元数据或成员收集丢失**

在类继承元数据或成员的收集中，如果只依赖普通属性查找（如 getattr）或只扫描当前类命名空间，就只会看到 MRO 中首个命中的定义或本类定义，导致非首个基类定义以及仅在源码中可见的定义丢失。条件性共享修复原则是：对类对象显式按 MRO 遍历各类自身命名空间（如 obj.__mro__ 或 getmro(subject)[1:]），收集定义并保留来源或定义类信息；重复项处理、返回物化、过滤截断和写入策略分别遵循各业务约束（pytest 的 pytestmark 不去重并必须返回 list，store_mark 仅用本类标记；Sphinx autodoc 首个定义者胜出、使用 ModuleAnalyzer/INSTANCEATTR 并保留 inherited-members 语义）。

- 不得把该原则泛化为所有属性查找；pytest 非类对象仍走 getattr(obj, "pytestmark", [])，Sphinx 只处理可分析的类成员。
- 重复项策略不共享：pytest 不按标记名去重，Sphinx 以 name not in members 实现首个定义者优先、共同祖先只出现一次。
- pytest 必须 return list(...) 以支持列表相等性；Sphinx 无此返回物化要求。
- pytest 的 store_mark 只能用 consider_mro=False 的本类标记追加，不能把继承标记复制进子类、不能修改父类或兄弟类、不能缓存。
- Sphinx 必须用基类自己的 __module__/__qualname__ 与 ns == qualname 定位文档，并静默跳过 AttributeError/PycodeError；纯源码实例属性成员值为 INSTANCEATTR。
- Sphinx 必须保留 inherited_members_option 默认 'object' 与 :inherited-members: <BaseName> 的命名基类排除语义；pytest 必须保留 consider_mro 默认 True。
- 两类修复的文件、常量、过滤选项和测试计数不可互换；不得把 pytest 的列表物化或去重规则套到 Sphinx，也不得把 Sphinx 的 ModuleAnalyzer/INSTANCEATTR 套到 pytest。
- Sphinx 最终验证前必须清理 XDEBUG2.log/XDEBUG5.txt/_XDEBUG_FLAG 等临时调试写入；pytest 只改 src/_pytest/mark/structures.py。

pytest 需要累积各级标记并处理单项/列表表示；Sphinx 需要保留 MRO 中优先类的同名文档，并遵守显式基类排除。两份来源的完整 constraints、applicability、issues 与 evidence 作为实例附在原则中，未用某个项目的具体操作覆盖另一个项目。

最终两项任务共提炼 3 条原始经验，其中 Sphinx 还产生一条“清理临时调试日志”的经验。程序枚举全部 2 个跨项目候选对，而不是静默挑选方便合并的记录。候选判断与原始全文见审计包的 merge.json。

同项目的日志清理与继承修复涉及同一函数，确定性整理先将它们标为 unknown 并阻止继续。Codex 核对补丁及操作范围后，显式确认 compatible（不是 equivalent）；审阅及版本历史保存在 project-relation-review.json。这一步是明确审阅，不计作自动判断。随后跨项目的原则归纳及复核由模型自动完成。

合并流程为原始资料导入 → 项目规则整理与明确审阅 → 枚举跨项目候选 → 通用模型提出原则 → 新上下文逐来源复核 → 精确引文校验 → 保存共享原则。重复执行命中同一 ID，不重复付费；原始记录、源库哈希与命名空间隔离检查通过。案例由人定向选择，原则内容由程序自动归纳及复核；并未演示全库无人值守的候选发现。

## 两份原始经验

### pytest

原实现用 getattr(obj, "pytestmark", []) 读取类标记，类属性按 MRO 查找只会得到最先命中的 pytestmark，因此多重继承时其他基类的标记被覆盖。修复需要让 get_unpacked_marks 对类按 obj.__mro__ 收集每个类 __dict__ 中的 pytestmark，本地类在前、基类按 C3 MRO 在后；consider_mro=False 时只读取当前类 __dict__；非类对象保持 getattr 路径；单个标记要包装成列表；不得按名称去重。还必须把归一化结果物化为 list 返回，否则生成器无法与列表做相等比较；store_mark 追加时只能使用对象自身已有标记，不能把继承标记复制进子类，也不能修改父类或兄弟类。

在 src/_pytest/mark/structures.py 修改 get_unpacked_marks：增加 consider_mro 默认 True；类对象按 __mro__ 展平 __dict__.get("pytestmark", [])，consider_mro=False 时只取当前类；非类对象保持原 getattr 读取；最后 return list(normalize_mark_list(mark_list))。同时调整 store_mark 使用 consider_mro=False 的本地标记列表追加新标记。保留函数装饰顺序、无标记类返回 []、同名标记不去重、父类后加标记对子类可见以及父子/兄弟隔离行为。

### Sphinx

1) `sphinx.ext.autodoc.importer.get_class_members()` 只用 subject 自身命名空间（`namespace = '.'.join(objpath)`）匹配 `analyzer.attr_docs`，基类 `__init__` 里的 `self.attr = None  #: doc` 属于纯源码实例属性，运行时既不在任何类的 `__dict__` 中，也不在 `__annotations__` 中，`dir(subject)` 也取不到，因此完全不会被发现，HTML 中缺少 ROOT/LEFT/RIGHT 等继承属性标记。2) 即使成员被发现，`Documenter.filter_members()` 的 `is_filtered_inherited_member()` 仅用 `name in cls.__dict__` 或 `name in cls.__annotations__` 判断“成员定义在 MRO 的这一类”，纯源码实例属性会一路走到 `:inherited-members:` 的默认截断类（`inherited_members_option(None)` 返回 `'object'`）并返回 True；在 `self.options.members is ALL` 的分支里该返回值直接导致 `keep = False`，继承属性在生成文档前被静默丢弃。3) 排障期在应用源码里加入的写 `XDEBUG2.log`/`XDEBUG5.txt` 的文件日志未清理。

在 `get_class_members()` 已有逻辑之后追加对 `getmro(subject)[1:]` 的遍历：对每个基类取 `__module__` 与 `__qualname__`，用 `ModuleAnalyzer.for_module(module).find_attr_docs()` 取该模块属性文档，仅当 `ns == qualname` 且 `name not in members` 时登记 `ClassAttribute(cls, name, INSTANCEATTR, '\n'.join(docstring))`，并用 `except (AttributeError, PycodeError)` 吞掉内建/C 模块等无法分析的情况。因为 `name not in members` 即视为已定义，MRO 中第一个定义者胜出、共同祖先只出现一次、`Reverse(Right, Left)` 选中新的第一父类都自然满足。随后把 `filter_members()` 内的判定改成 `is_filtered_inherited_member(name, obj)` 并同步所有调用点，在 MRO 循环里 `name in cls.__dict__` / `__annotations__` 之后追加 `elif isinstance(obj, ObjectMember) and obj.class_ is cls: return False`，表示“该成员确实由这个类定义（含源码实例属性）”，从而不会因到达默认或命名截断基类而被过滤。定义类信息由成员发现阶段的 `ClassAttribute.class_` 记录并经 `ObjectMember` 透传，`ClassDocumenter.get_object_members()` 仍用 `m.class_ == self.object` 实现继承成员的 opt-in。

## API 来源与审计

共 5 次修复任务执行（包含预算停止的运行），以及原则合并请求；总计 162 次请求记录，162 次成功返回。逐请求均绑定用户指定的当前仓库 `.env`，请求地址 `https://api.deepseek.com`，模型 `deepseek-flash`。没有检索或注入另一任务的经验。日志不含密钥。提供商累计报告 7,393,673 tokens，包含重复上下文，不能直接换算为计费金额。

## 文件与复现

- [完整结构化结果](real-engineering-merge.json)
- [原始执行、JUnit、参考对照和 API 审计包](real-engineering/evidence.zip)
- [pytest 最终生产补丁](real-engineering/pytest-final.patch)
- [Sphinx 最终生产补丁](real-engineering/sphinx-final.patch)
- [pytest 原始经验](real-engineering/pytest-experience.json)
- [Sphinx 原始经验](real-engineering/sphinx-experience.json)
- [实验脚本说明](../../../benchmarks/engineering-merge/README.md)

## 结论的范围

两项定向选择的历史案例只能证明本次修复和有条件原则归纳确实执行，不能估计总体准确率、生产故障率或修复成功率提升。未运行全仓库所有测试，未使用官方 Docker 评测，因此不是 SWE-bench 排名成绩。公开案例可能有训练污染；未向模型提供补丁不等于能证明模型从未见过该问题。共享原则仅存放于显式实验命名空间，未自动发布到所有项目；新目标仍需验证。

模型提炼文字不等于逐句验证的事实。例如日志清理记录要求工作树无日志产物，但实验副本保留了 DEBUGLOG/XDEBUG 文件作为审计材料。最终生产源码已另行检查，没有调试写文件语句；交付生产补丁只包含源码。原文保留这一限制，不将测试通过包装成所有文字断言均得到证明。

相关后端原则与来源追踪回归：20 项通过。R22–R24 的处理及默认产品尚未覆盖的范围见 [持续修复记录](../../experience-consolidation-remediation.md)。
