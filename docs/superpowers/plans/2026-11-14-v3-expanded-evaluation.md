# V3 扩展实验与针对性优化计划

> **For agentic workers:** Use superpowers:executing-plans. The user requested direct execution; do not introduce an approval gate between experiment, diagnosis, implementation and verification.

**Goal:** 在多个独立 Java/Python 目标上，以每条件 3 次重复解释经验检索收益，并用同口径实验验证针对性改进。

**Architecture:** 复用 V3 的原始测试、冻结参考库、来源排除和逐任务记录。先运行当前版本的 108 项对照，再依据完整轨迹形成实现方案；优化后保持相同目标、输入、限制和重复次数，至少重跑 54 项新检索的 none/full/compact 条件。

**Tech Stack:** Python、pytest、Java/Maven、SQLite、现有 DeepSeek 配置。

**Spec:** 本计划的协议与用户要求为本轮实验规格；实现方案在诊断完成后写入独立文档，不能提前按评估结果硬编码业务规则。

## 实验协议

- 案例：现有 evaluation-v3 全部 6 个目标，Java/Python 各 3 个。
- 初始条件：baseline/contextual × none/full/compact × 每案例 3 次，共 108 项；single 模式。
- 每项任务最多 12 轮、180 秒，每请求最多 60 秒；同一模型和审核参考库。
- 6 个案例分别运行，相互独立；最多 6 个并行进程，每个进程内按原计划轮换条件与策略顺序。机器有 16 个逻辑 CPU。耗时包含并发环境影响，不能与先前串行小样本直接归因比较。
- 初始批次完成前不修改运行时代码，保留源码快照与哈希。每阶段使用新的目录和任务数据库。
- 保留所有失败、缺失用量和基础设施异常；成本比较仅使用双方成功且用量完整的配对，并同时报告成功率。
- 不修改生产数据库、原始 benchmark、用户演示文件与 output；不提交、推送或合并。
- 不复制或打印密钥；新调用继续使用被 Git 忽略的本地 .env。

## Review Focus

1. 小经验池导致只重排同一批参考；不能把排序变化当作内容筛选收益。
2. 异常类型过滤、首次无失败证据、成功测试后的历史失败，可能影响经验适用性。
3. 分析按案例与重复次数配对；不能把重复运行当作完全独立的业务场景。
4. 优化不能从评估目标提取训练经验或硬编码默认值，不能丢弃规则的适用条件。
5. 前后对照保留完整来源与版本信息；并发、服务延迟、时间顺序和样本量作为解释限制。

### Task 1: 扩展当前版本对照

**Files:** `.cache/v3-expanded/` 调度、进度与源码快照；现有 `scripts/experiment_v3.py`。

**Interfaces:** 每个案例输出独立 `report.json`，108 个唯一 trial_id；运行清单记录进程退出码、路径与源文件哈希。

- [x] 预检 6 个案例的参考资格和原始库完整性；确认 dry-run 为 108。
- [x] 启动受限并发的 6 个案例任务；持续检查增量报告并保留失败。
- [x] 结束后核对任务数量、重复次数、源码与经验库哈希。

### Task 2: 诊断没有收益的原因

**Files:** `scripts/analyze_v3.py`、`backend/tests/test_analyze_v3.py`、`docs/reports/v3-expanded-2026-11-14.md`。

**Interfaces:** 只读汇总多个 V3 报告，按案例/语言/策略/经验表示统计成功、完整 token、决策轮次、工具调用、参考注入和模型调用阶段；保持失败分母。

- [x] 先写测试，覆盖重复 trial_id、缺失用量、失败样本与提取/修复调用拆分；观察失败后实现。
- [x] 交叉核对实际请求、检索事件与失败测试，区分测量局限和可修复机制。
- [x] 固定针对性优化方案、独立回归测试与后续对照协议，再改运行时逻辑。

### Task 3: 实现与复验

**Files:** `backend/experience/retrieval.py`、`policy.py`、`backend/agent/core.py`、`orchestrator.py`、`backend/config.py`、`backend/api/main.py`、V3 CLI、相应测试及结果文档。

**Interfaces:** 实现规格见 `docs/superpowers/specs/2026-11-14-v3-adaptive-retrieval-design.md`。新增 `adaptive_retrieval_evidence(task_description, results, language)` 和 `adaptive` 策略；保留旧策略以便复现。配置 `EXPERIENCE_RETRIEVAL_STRATEGY` 控制应用策略；54 项复验显式指定 adaptive。

- [x] 用回归测试复现已确认机制，最小修改后通过测试；保留旧检索作为可复现对照。
- [x] 在相同 6 个案例上，每条件 3 次，执行优化后 none/full/compact 的 54 项复验。
- [x] 对完整数据作配对比较；不能仅用 token 下降宣称修复效果提高。
- [x] 完成全量后端回归、必要前端联调和一次独立最终审查，修复重要发现。
- [x] 保存可复现命令、实际结果、限制与剩余问题。

**结果：** 108/108 对照、54/54 复验通过原始测试；完整/精简经验组配对平均修复 token 下降 18.4%/10.4%，同期无经验组上升 8.0%。该变化只支持本集合中的开销改善。后端 636 passed、1 skipped；前端联调通过；审查发现已修复。完整报告与数据见 `docs/reports/v3-expanded-2026-11-14.md` 及同名 JSON。本地配置启用 adaptive，代码默认值保留 contextual；工作区改动未提交。
