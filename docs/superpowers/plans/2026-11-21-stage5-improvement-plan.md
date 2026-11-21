# 阶段五改进实施计划

> 逐步执行，每完成一个任务勾选一次；每个阶段独立提交、独立验收。

**目标：** 在保持现有主链路和测试基线的前提下，逐项落地阶段四列出的五项不足。

**技术栈：** Python/FastAPI/SQLite + Vue 3/TypeScript/Vite/Vitest。

**设计依据：** `docs/superpowers/specs/2026-11-21-stage5-improvement-design.md`

## 全局约束

- 不改变现有对外接口语义；新增字段只读或向后兼容。
- 不引入运行时服务；可选语法包只进 `requirements-semantic.txt`。
- 每条结论必须能指向本机实测数据；样本不足时写"未测出"。
- 正式库 `data/app.db` 只读使用，所有实验在 `.cache/stage5/` 的独立副本上做。
- 每阶段结束跑对应回归，不靠重跑失败用例掩盖竞态。

## 现有基线（2026-11-21 实测）

- 后端 `pytest backend/tests`：993 通过 / 1 失败（缺 cmake）/ 1 跳过；`test_knowledge_invariants.py` 15 项因缺 hypothesis 未跑。
- 前端 `npm.cmd test`：202 通过 / 22 文件；`npm.cmd run build` 通过。
- 本机库 31 条经验，无 `experience_auto_jobs` 表（自动比较未运行过）。

## 阶段一：自动比较的覆盖与成本

### 任务 1.1 候选盘点（只读）

**文件：** `backend/experience/automation.py`、`backend/api/experience_routes.py`、`frontend/src/components/ExperienceAutoMerge.vue`

- [x] 核对发现上游已返回 `candidate_total` / `selected_candidates` / `deferred_candidates`，但 `candidate_total` 用的是 SQL 粗计数，未走后续资格过滤。
- [x] 计数改为与选取共用一个资格谓词；新增 `compared_candidates`（已比较对数），`selected_candidates` 改为累计入队对数。
- [x] 页面显示「候选 N 条 · 已比较 M · 其余 K 条未比较」；前端契约与校验同步。
- [x] 回归：`pytest backend/tests/test_automation_batch.py backend/tests/test_automatic_repair_merge.py`，前端 202 项通过。

**验收：** 候选数、已比较数、推迟数满足 `total = compared + deferred`；无 job 时显示"暂无比较对象"。

### 任务 1.2 分批补查

**文件：** `backend/experience/automation.py`、`backend/tests/test_automation_batch.py`（新）

- [x] `_plan()` 排除已入队（`experience_auto_pairs` 两侧任一为本条）的候选，只取下一批最多 5 对。
- [x] 增加 `MAX_PAIRS_PER_JOB = 20`；批次大小按"已比较对数"扣减预算，不会超发。
- [x] 一批处理完后 job 回到 `pending` 继续下一批；达到预算时结束并在 `notice` 写明剩余对数。
- [x] 单测：12 对候选分 3 批全部比较；预算 3 时只比较 3 对并披露剩余 4 对。

**验收：** 12 对候选时模型调用 12 次、`deferred_candidates=0`；预算 3 时调用 3 次、`deferred_candidates=4`。

### 任务 1.3 候选排序评测

**文件：** `scripts/evaluate_candidate_ranking.py`（新）、`docs/reports/candidate-ranking-2026-11-21.md`（新）

- [x] 评测只调用生产排序函数 `candidate_ranking`，消融用 patch 关闭词法信号，不重写排序。
- [x] 指标：真实伙伴名次、recall@1、recall@5、平均名次、排序耗时。
- [x] 报告记录 2 条案例的结果与外推边界。

**验收：** 报告给出两策略指标表；明确写出"样本不足以下结论"和"词法信号在这 2 条案例上未改变名次"。

### 任务 1.4 漏合并案例集

**文件：** `benchmarks/missed-merge/cases.json`（新）、`backend/tests/test_missed_merge_cases.py`（新）

- [x] 每条用例：来源/伙伴/干扰项、期望名次（可为不限）、发现途径、日期、是否已修复。
- [x] 重放只走 `candidate_ranking`，不调用模型。

**验收：** 2 条用例重放通过；真实伙伴在两种排序策略下都仍可比较。

## 阶段二：冲突验证的语言与工程适配

### 任务 2.1 抽取移植模块

**文件：** `backend/experience/transplant.py`（新）、`backend/api/conflict_verification.py`

- [ ] 把 `function()`、`signature()`、`body_identity()`、`transplant()` 迁入新模块，Python 行为逐行等价。
- [ ] `conflict_verification.py` 改为调用新模块，现有测试全绿。

**验收：** `pytest backend/tests/test_real_comparison_protocol.py backend/tests/test_real_safety_verification.py` 不变。

### 任务 2.2 Java 目标映射

**文件：** `backend/experience/transplant.py`、`backend/experience/grounding.py`

- [ ] tree-sitter-java 定位方法节点，校验参数与修饰符一致，按字节区间拼接。
- [ ] 定位不唯一或签名不同 → `VerificationUnavailable`。

**验收：** Java 方法级等价/冲突对可自动判定；错误目标被明确拒绝。

### 任务 2.3 Go / C++ 目标映射

**文件：** `backend/experience/transplant.py`、`requirements-semantic.txt`

- [ ] 新增 `tree-sitter-go`、`tree-sitter-cpp`（可选依赖）。
- [ ] 未安装语法包时保持"需人工确认"，不报错崩溃。

**验收：** 本机缺 cmake 时 C++ 用例 skip 并记录原因；Go 用例在已装 Go 的环境通过。

### 任务 2.4 反例与回归

- [ ] 每语言一条"必须拒绝"反例（辅助函数不同、宏/模板不可解析、跨文件接口）。
- [ ] 报告记录拒绝理由分布。

## 阶段三：自适应检索成本

### 任务 3.1 注入去重

**文件：** `backend/agent/core.py`、`backend/agent/orchestrator.py`、`backend/tests/test_retrieval_dedupe.py`（新）

- [ ] 跨轮次按 `rule_id` 去重，只追加新规则。
- [ ] 单测：连续两轮发现新错误类型时，第二轮只含新增规则。

### 任务 3.2 预算参数化与审计

**文件：** `backend/experience/retrieval.py`、`backend/agent/core.py`、`backend/api/task_metrics.py`

- [ ] `limit` / `character_budget` 按策略取值（baseline/contextual/adaptive 各一组明确常量）。
- [ ] 审计补充：注入规则数、注入字符数、逐轮变化。

### 任务 3.3 引用追踪

- [ ] 从模型审计消息解析实际引用的 `rule_id`，计算"注入未使用"比例。
- [ ] 指标进入任务对比面板。

### 任务 3.4 小样本对照

- [ ] 6 对任务 × 2 策略，固定经验库与预算；记录耗时、token、最终测试结果。
- [ ] 报告明确样本量与外推边界。

## 阶段四：自动判定的证据强度

### 任务 4.1 证据清单

**文件：** `backend/experience/reviews.py`、`backend/api/experience_routes.py`、`frontend/src/components/ConflictAutomation.vue`

- [ ] 自动决定附带可读证据清单（来源任务、最终测试数、原始失败、交叉测试轮次）。
- [ ] 页面展示清单与覆盖缺口提示，不生成新测试。

### 任务 4.2 误合并撤销

- [ ] 撤销入口复用 `reopen` 与依赖失效传播；原始经验与历史记录不变。
- [ ] 回归：撤销后检索不再返回该规则，相关授权全部失效。

### 任务 4.3 失效重验

- [ ] 源码/测试配置/来源关系变化 → 授权标记需重验并进入补证队列。
- [ ] 回归：重验前不会被注入。

## 阶段五：体验与资料

### 任务 5.1 页面

- [ ] 候选盘点与补查进度（接 1.1/1.2）。
- [ ] 批量补证入口、失败原因与重试状态。
- [ ] 大规模检索的分页与筛选回归。

### 任务 5.2 文档与汇报

- [ ] 阶段五进展写入 `docs/`；实验明细、源码说明、汇报材料分开保存。
- [ ] 更新 `README.md` 更新日志（当前停在 2026-11-14）。

## 执行顺序

1. 阶段一 1.1 → 1.2 → 1.4 → 1.3（先可观测与去漏检，再做排序评测）。
2. 阶段三 3.1 → 3.2（低成本、纯代码可测）。
3. 阶段二 2.1 → 2.2 → 2.3 → 2.4（依赖新增语法包，风险中等）。
4. 阶段四 4.2 → 4.3 → 4.1（先做已有机机制的组合，最后做证据展示）。
5. 阶段五 随各阶段同步。

## 需要确认的边界

- 语义排序是否引入 `sentence-transformers`（本机 CPU、需下载模型，PyPI 无 Windows wheel，需源码安装 torch 依赖）。
- 阶段三真实对照允许的模型调用量（建议 6 对 × 2 策略）。
- 阶段二是否扩展到真实 Java/Go 工程样例，还是先用受控夹具。

## 执行记录

### 2026-11-21 阶段一 1.1–1.4

已确认的选择：不引入语义模型（纯确定性排序）；真实调用预算 6 对 × 2 策略（阶段三）；顺序为阶段一 → 三 → 二 → 四。

阶段一实测：自动合并相关后端 35 项通过；新增 `test_automation_batch.py` 3 项、`test_missed_merge_cases.py` 2 项；前端 202 项通过，生产构建通过。12 条候选分 3 批全部比较；预算 3 时比较 3 对并披露剩余 4 对。

已知未完成：案例集只有 2 条，排序评测的结论仅限方向性；阶段一没有真实模型调用。详见 `docs/reports/candidate-ranking-2026-11-21.md`。
