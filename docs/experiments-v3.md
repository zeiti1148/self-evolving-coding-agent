# V3 三组经验对照与检索比较

入口为 `scripts/experiment_v3.py`。旧 `scripts/experiment.py` 保留固定 Java 两组实验，并显式使用旧的空查询检索。新实验支持 Java/Python、single/multi、三种经验表示和三种检索策略，不更改生产数据库和原始样例。

## 实验条件

| 参数 | 条件 | 含义 |
| --- | --- | --- |
| 经验表示 | `none` | 关闭经验检索与注入；成功后的经验提取仍执行，保持其他流程一致 |
| 经验表示 | `full` | 完整的生产注入表示；通用经验仍经过隐私字段白名单 |
| 经验表示 | `compact` | 保留 ID、语言、仓库隔离信息、全部问题和规则、适用条件与能力；删除标签、来源与验证等辅助元数据 |
| 检索 | `baseline` | 空查询，保持同仓库优先、异常覆盖、时间、ID 的旧排序；不去重 |
| 检索 | `contextual` | 使用任务描述和最近一次失败测试，加入中文相邻字片段、snake_case/camelCase 标识符匹配及适用条件文本相关性；完全相同的规则保守去重 |
| 检索 | `adaptive` | 任务已有具体异常或最近一次测试失败时才检索；仅使用当前失败的异常，最近测试成功后撤下参考，避免历史失败持续触发注入 |

三种检索都执行相同的语言、仓库、发布状态、能力、异常类型硬过滤。adaptive 的前置判断不会绕过这些资格检查；审计记录 `no_failure_evidence`、`task_error`、`test_failure`、`tests_passed` 或 `test_execution_error`。词法相关性不等于语义适用性；模型仍须根据目标原始测试核对业务条件。精简表示不截断规则或删除前提，也不额外调用模型生成摘要。

应用通过 `EXPERIENCE_RETRIEVAL_STRATEGY=baseline|contextual|adaptive` 配置策略，未配置时保留 contextual。实验 CLI 显式指定策略，不受应用默认值影响。adaptive 跳过注入的任务会如实记录 `reference_received=false`，不能将其解释为模型已使用经验。

完整与精简组使用同一个冻结库和检索策略。每次运行轮换三组顺序；同时比较多个执行模式或检索策略时，也轮换它们的顺序。默认每个案例重复三次。

## 六个独立目标

目录为 `benchmarks/evaluation-v3/catalog.json`，每种语言各三个项目，每个项目 12 项固定测试：

| 场景 | 名称/SKU | 数量 | 选择失败 |
| --- | --- | --- | --- |
| 预约（已有 V3 目标） | 缺失/空白回退 visitor | 缺失/格式错误回退 1，超出 1..30 抛错 | unassigned |
| 票务（新增） | 缺失/空白回退 attendee | 缺失/格式错误回退 2，有效整数钳制到 1..6 | waitlist |
| 库存（新增） | 缺失/空白抛错，有效 SKU 转大写 | 缺失/空白回退 0，格式错误或超出 0..500 抛错 | 空集合为 backorder，非空集合的越界下标回退首项 |

三类目标均包含跨文件组合调用。checkout 来源不在评估集合中。标准修复只保存在 `backend/tests/fixtures/evaluation_v3_fixes.json`，评估工作副本不包含它。新增案例的回归测试同时验证原始缺陷和标准修复，确认测试数量与源目录指纹不变。

## 准备参考库

先用独立训练项目完成真实修复，在独立实验数据库中审核并发布通用经验，再把数据库路径传入 `--experience-db`。发布规则须移除来源的固定业务默认值和私有路径，明确目标仍需独立验证。

运行器使用 SQLite **只读连接**备份参考库，不初始化或迁移输入数据库。预检递归检查已发布经验的来源图；来源缺失、循环、来自评估仓库或匹配评估源码指纹都会拒绝运行。任一所选目标没有可用经验也会报告 `not_run`，避免把空经验组当成有效对照。

每项试验使用独立任务数据库和工作副本；新生成经验仅保存到该试验输出库。检索只读冻结参考库。每次启动新建运行目录，不恢复任务、不自动重跑失败试验。

## 命令

在仓库根目录执行。默认只输出计划：不读取模型密钥、不发请求、不创建实验目录。

```powershell
# 6 个案例 × 3 组 × 3 次重复 = 54 个独立任务
.\.venv\Scripts\python.exe scripts/experiment_v3.py

# 同时比较旧/新检索：108 个任务；只打印规模
.\.venv\Scripts\python.exe scripts/experiment_v3.py --retrievals baseline contextual

# 指定已审核的独立训练参考库后，显式开启真实调用
.\.venv\Scripts\python.exe scripts/experiment_v3.py --live `
  --experience-db <独立训练参考库路径> --retrievals baseline contextual

# 小规模探路：同一 Python 案例，3 组 × 2 种检索，共 6 次
.\.venv\Scripts\python.exe scripts/experiment_v3.py --live `
  --experience-db <独立训练参考库路径> --cases python-inventory-evaluation `
  --repetitions 1 --retrievals baseline contextual --max-steps 12

# 多角色实验；一次只改变一个比较维度更容易解释结果
.\.venv\Scripts\python.exe scripts/experiment_v3.py --modes single multi

# 新策略复验：6 个案例 × 3 组 × 3 次重复 = 54 个任务（默认只打印计划）
.\.venv\Scripts\python.exe scripts/experiment_v3.py --retrievals adaptive --repetitions 3
```

模型沿用根目录已有 `LLM_*` 配置；所有条件共用同一配置、步数和时限。真实调用会把评估工作副本及参考规则发送至该服务，并产生费用。脚本没有失败后切换成模拟成功的路径。测试代码可显式注入脚本模型，此时报告标为 `scripted`，不能作为修复能力或效率的证据。

## 报告与解读

每次输出到 `.cache/experiments-v3/<UTC时间>-<随机编号>/`：

- `report.json`：固定计划、配置、源码哈希、原始失败验证、冻结库哈希、全部任务、分组统计、配对结果。
- `report.md`：便于阅读的结果表，明确用量覆盖和统计分母。
- `references.db`：本次只读参考快照。
- `trials/<短序号>/task.json`、`output.db`、`workspaces/`：任务过程、最终测试及独立工作副本。逻辑试验 ID 保留在记录中，`trial_directory` 指向实际目录；短序号避免 Windows/Java 的路径长度限制。

报告逐任务原子更新 JSON。执行错误、停止、测试失败和无效原始基线不会消失；未完成任务保留 pending/running。最终成功要求生产 Agent 为 succeeded、独立最终验证执行预期数量的原始测试，且无失败、异常或跳过。

`actual_requests` 记录真正进入模型调用的经验 ID、内容哈希、表示大小和请求结果，不保存整段提示词。`reference_received` 表示包含经验的请求成功返回，不证明该经验促成修复。失败请求仍计入任务结果，避免只比较成功收到经验的样本造成偏差。

`total_tokens=null` 表示提供商用量不完整。汇总给出完整用量任务数；配对成本只使用同任务、同轮次、双方成功且用量完整的结果。成功率配对包括双方失败的任务。模型失败、低成功率不会被实验工具视为记录系统失败；CLI 对缺失配置、预检失败或比较有效性破坏返回非零。

默认 54 次设计只是小型探索。应分别检查语言和任务类型，保留失败原因，扩大独立样本与重复次数后再判断提升。当前按字词相关性排序不保证成功率提高；大量重复经验的表现和多角色成本需要实际数据验证。

`scripts/analyze_v3.py` 可按批次 manifest 汇总分案例、分语言结果，并拆分修复与经验提取阶段的 token，分别报告用量覆盖。它拒绝重复条件、冻结库/模型/预算不一致、源文件及测试哈希缺失或冲突；前后配对也要求目标输入哈希相同。使用 `--manifest <优化后清单> --before-manifest <原清单> --after-strategy adaptive --output <分析.json>` 对比两个阶段。前后分批运行仍存在时间顺序、并发负载与模型波动的影响。

## 初始工程验证与单案例探路（2026-11-14）

新增计划、快照、隔离、表示与检索测试均在观察到预期失败后实现。独立审查发现并修复“子集试验未排除其他评估案例来源”的问题，增加回归测试；同时拒绝将输出目录放入原始 benchmark。最终完整后端回归：606 passed、1 skipped（Windows 符号链接权限），另有既有 Starlette/httpx 弃用提示。前端接口联调通过 5 个场景、66 个公共模型对象。运行环境限制导致沙箱内 asyncio 本地 socket 初始化挂起，接口回归在允许本地通信的环境中执行。

已在独立实验数据库中准备并审阅 4 条通用经验，追溯至真实 Java/Python 来源任务，生产数据库未变化。前两批各 6 次请求均返回 HTTP 401，完整记录见 [真实预试验与认证诊断](reports/v3-live-pilot-2026-11-14.md)。用户再次更新密钥、通过官方只读认证检查后，同一 Python 库存案例的 6 个条件均完成真实修复并通过各自 12 项原始测试；共 37 次成功模型请求、213,326 token。详见 [真实修复对照结果](reports/v3-live-results-2026-11-14.md)。本轮未观察到明确检索优势，精简组总 token 也未降低；只有一个案例且每条件一次，不能据此推断稳定收益。

## 扩展实验与针对性优化（2026-11-14）

六个 Java/Python 案例、每条件重复 3 次：baseline/contextual 的 108 项真实对照全部通过，adaptive 的 54 项复验也全部通过各案例的 12 项原始测试。输入源码、测试、审核参考库及生产库指纹均保持不变。

轨迹显示无经验组已全部成功，候选经验仅 Java 1 条、Python 3 条，原两种检索的 36 组首次参考集合完全相同；72 个启用经验的任务在自测通过后仍发送参考。新增 adaptive 只在存在具体异常或最新失败测试时检索，成功后撤下历史失败参考。相对 contextual，完整/精简经验组的平均修复 token 分别下降 18.4%/10.4%；两组各有 18 个用量完整的成功配对。同期无经验组上升 8.0%，且复验仅 6/36 个允许经验的任务实际收到参考，结果主要验证减少多余注入的开销。

完整过程、分语言与分案例数据、缺失用量和复现命令见 [扩展实验报告](reports/v3-expanded-2026-11-14.md) 与其 JSON。最终后端回归为 636 passed、1 skipped，前端接口联调通过 5 场景、66 个公共模型对象；独立审查发现的输入哈希校验与多角色语言审计问题已修复。基于本轮结果，本地 `.env` 已设置 `EXPERIENCE_RETRIEVAL_STRATEGY=adaptive`，重新启动应用后生效，未配置时的代码默认值仍为 contextual。

六个案例仍同属输入处理问题，前后分批存在模型与运行波动；本轮观测到开销下降，没有证明复杂任务成功率提升或统计显著收益。
