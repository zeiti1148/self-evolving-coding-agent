# 重复注入优化：首轮完整、之后精简（2026-11-21）

对应阶段五 3.2/3.3 的后续优化。前两轮实测指出真正的成本来自**同一规则被逐轮重复注入**（contextual 在两个真实任务里各重复 5–6 次）。本轮加入一个可选机制并做了 3 组重复对照。

## 机制

新增配置项 `EXPERIENCE_COMPACT_AFTER_FIRST`（默认 `false`）：

- 第 1 个决策轮：按原表示（`full`）注入。
- 第 2 轮起：改用 `compact` 表示，去掉 `created_at`、`verification`、`evidence`、`tags`、`source_task_id` 等**支持性元数据**；`applicability`、`constraints`、`issues`（含错误类型与文件）保留。
- 资格重核、失败保留、预算上限、撤出语义都不变；每轮的表示会写进审计的 `injection_events[].representation`。

离线量化：对同一条种子规则，`full` 1468 字符、`compact` 1070 字符，**单条减少 27.1%**。

## 真实对照（同一项目 `java-null-greeting`，各 3 次）

| 变体 | token（3 次） | 均值 | 耗时均值 | 注入字符合计均值 | 成功 |
| --- | --- | ---: | ---: | ---: | --- |
| 基线（始终 full） | 26,367 / 27,720 / 36,828 | 30,305 | 37.7 s | 7,973 | 3/3 |
| 首轮后 compact | 24,069 / 24,012 / 20,975 | **23,019** | **26.1 s** | 5,886 | 3/3 |

- token 均值 **−7,286（−24.0%）**；耗时均值 **−11.5 s（−31%）**。
- 两组区间不重叠：基线最低 26,367 > compact 最高 24,069。6 次任务全部成功。
- 逐轮字符数证据：基线每轮 1196；compact 变体为 1196 → 1005 → 1005 …（重复轮每条 −191 字符）。

## 结论与限制

- 在这个任务族上，精简表示稳定降低了 token 与耗时，方向一致、区间不重叠。**但这是 3 对 3 的小样本、单一项目、单一模型**，不能外推。
- token 降幅（24%）大于注入字符降幅（17%），说明轨迹也变短了（模型更快决定修改），不是纯粹的文本裁剪；本实验无法把两者分开。
- 默认关闭：现有行为不变，需要用户显式设置 `EXPERIENCE_COMPACT_AFTER_FIRST=true`。是否默认开启应由更多语言/项目的对照决定。
- compact 会去掉 `verification`、`evidence` 等溯源字段。它们在资格判定里不被使用（判定用规则状态、来源与授权），但人工审计时看不到了，页面仍可查看原始记录。

## 复现

```powershell
.\.venv\Scripts\python.exe scripts\minimal_retrieval_pair.py                       # 计划
.\.venv\Scripts\python.exe scripts\minimal_retrieval_pair.py --live --arms contextual
.\.venv\Scripts\python.exe scripts\minimal_retrieval_pair.py --live --arms contextual --compact-after-first
```

单元/集成测试：`backend/tests/test_compact_after_first.py`（3 项：首轮 full→之后 compact、默认关闭、compact 保留条件与约束字段）。

## 下一步

1. 在 Java/Python 不同缺陷类型上各跑 3 对，确认降幅是否稳定。
2. 若要默认开启，先确认在"需要溯源文本判断"的任务上模型是否因缺少 verification/evidence 而变差——当前实验没有测这个维度。
