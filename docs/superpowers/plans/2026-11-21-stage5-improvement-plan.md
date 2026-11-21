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

- [x] Python 定位/签名/函数体身份/拼接逐行迁入新模块，AST 行为不变。
- [x] `conflict_verification.py` 改为调用新模块，语言与扩展名按能力判断。

**验收：** `test_real_comparison_protocol.py`、`test_real_safety_verification.py`、`test_automatic_conflict_resolution.py` 全部通过（53 项）。

### 任务 2.2 Java 目标映射

**文件：** `backend/experience/transplant.py`、`backend/tests/test_transplant.py`（新）

- [x] tree-sitter-java 定位方法/构造器，只替换函数体，声明保持目标原文。
- [x] 目标缺失、声明不同、实现相同均拒绝。

**验收：** Java 方法级拼接成功；缺失目标与相同实现被明确拒绝。

### 任务 2.3 Go / C++ 目标映射

**文件：** `backend/experience/transplant.py`、`backend/experience/grounding.py`、`requirements-semantic.txt`

- [x] 新增 `tree-sitter-go`、`tree-sitter-cpp` 到可选依赖。
- [x] Go 方法与 C++ 成员函数按"接收者/类名 + 方法名"限定；`qualified_names()` 与 `grounding.symbols()` 共用命名。
- [x] 语法包未安装时抛 `VerificationUnavailable`（转人工），不崩溃。

**验收：** Go 函数与 C++ 成员函数可拼接；只写方法名、模板成员被拒绝；未装语法包时降级为人工确认。

### 任务 2.4 反例与回归

- [x] 每语言一条"必须拒绝"反例（目标缺失、方法缺少接收者、模板成员）。
- [x] 报告记录拒绝口径与未覆盖范围（无真实工程样例，不推断检出率）。

## 阶段三：自适应检索成本

### 任务 3.1 注入去重

**文件：** `backend/agent/core.py`、`backend/agent/orchestrator.py`、`backend/tests/test_retrieval_withdrawal.py`（新）

- [x] 核查后放弃"跨轮累积"：每轮重核资格是安全不变量，累积会让失效规则继续注入。
- [x] 改为"失败不撤出、成功才替换"：`retrieval_error` 只描述最近一次检索，失败时保留已注入参考。
- [x] 移除 `update_references()` 中被下一轮覆盖的无效 payload 写入。
- [x] 单 Agent 与多 Agent 两条路径都修正。

**验收：** 失败夹具保留参考经验；成功且无匹配时撤出；恢复后仍能撤出。

### 任务 3.2 预算参数化与审计

**文件：** `backend/experience/policy.py`、`backend/agent/core.py`、`backend/tests/test_retrieval_budget.py`（新）

- [x] `RETRIEVAL_BUDGETS`：baseline/contextual = 5 条 / 16000 字符，adaptive = 3 条 / 8000 字符，未知策略回退。
- [x] 单 Agent 按策略传 `limit` 与 `character_budget`；未知策略不会放大预算。
- [x] 审计补充 `reference_limit`、`character_budget`、`injected_characters`。

**验收：** 两种策略的检索参数与审计字段一致；预算取值仍待 3.4 实测确认。

### 任务 3.3 引用追踪

**文件：** `backend/experience/policy.py`、`backend/agent/core.py`、`backend/tests/test_injection_summary.py`（新）

- [x] 核查后改口径：模型回复不含规则 ID，无法解析"是否引用"。
- [x] 改为确定性的重复注入统计（同一规则在多轮被重复注入的比例），由审计事件直接计算。
- [x] 审计新增 `injection_summary`，不依赖模型输出。

**验收：** `injection_summary` 在多次注入同一规则时给出 unique/total/repeated/ratio；无注入时全为 0。

### 任务 3.4 小样本对照

- [x] 前置核查：现有 `scripts/run_real_retrieval_comparison.py`，不带 `--live` 为纯计划（本机实测输出 4 次计划试验、零调用）。
- [x] 记录本轮预算已变为 adaptive 3 条 / 8000 字符，历史结果不可直接比较。
- [ ] 真实执行 4 次试验（2 快照 × 2 策略）：**未执行**，需消耗模型额度并保持快照与服务可用。

**验收（待执行后补）：** 每个快照的配对差值、注入规则数与字符数、最终测试结果；样本 4 次，只能作方向性结论。

## 阶段四：自动判定的证据强度

### 任务 4.1 证据清单

**文件：** `backend/experience/automation.py`、`backend/experience/reviews.py`、`frontend/src/components/ConflictAutomation.vue`

- [x] 核查：`feed()` 已返回 `review.automatic_resolution`（策略、理由、目标、逐次交叉测试与证据哈希），页面已渲染测试矩阵并区分自动/待确认。
- [x] 未新增重复展示；本轮把注入预算与重复注入比例接入任务指标面板（见 5.1）。

### 任务 4.2 误合并撤销

- [x] 机制已存在：`reviews.reopen()` + `POST /api/experiences/conflicts/{id}/reopen`。
- [x] 新增 `backend/tests/test_merge_withdrawal.py`（2 项）：确认等价 → 检索 1 条；撤销 → 立即为空；原始记录不变；历史为 `resolve → reopen`。
- [x] 断言撤销不是开关：没有新决定时复用不会自动恢复。

### 任务 4.3 失效重验

- [x] 核查：失效判定与"失效不注入"由 `recovery.grant_valid()` 与既有 `test_experience_recovery.py`（47/76/149/190 行）覆盖，无需新增代码。
- [x] **未实现自动排队**：`recovery.enqueue()` 必须绑定修复任务才能重跑测试，没有任务时排队无法执行。理由写入报告，不留空入口。

## 阶段五：体验与资料

### 任务 5.1 页面

- [x] 候选盘点与补查进度（阶段一 1.1/1.2，页面已显示"候选 N 条 · 已比较 M · 其余 K 条未比较"）。
- [x] 批量补证入口（既有 `frontend/src/components/ExperienceRecovery.vue`，本轮核查确认可用，未重做）。
- [x] 任务指标面板新增"本次注入上限 N 条 / M 字符"与"同一规则重复注入 x%"，数据来自任务审计。

### 任务 5.2 文档与汇报

- [x] 阶段五进展写入 `docs/reports/`：候选排序、注入、冲突语言适配、撤销与重验四份报告。
- [x] 计划文档逐项勾选并保留未完成项与理由。
- [ ] `README.md` 更新日志：**暂缓**。README 同时带着手机工作台的未提交改动，混提交会误标归属；待手机工作台单独提交后再补一行。

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

### 2026-11-21 阶段三 3.1–3.2

计划调整：原定"跨轮累积去重"与代码中的安全不变量冲突（每个决策轮重核资格、并发合并必须能撤出），改为"失败不撤出、成功才替换"，成本治理交给预算参数化。

实测：新增 `test_retrieval_withdrawal.py` 3 项、`test_retrieval_budget.py` 4 项；Agent / 多 Agent / 经验策略 / 检索配置相关 8 个文件共 86 项通过。

已知未完成：多 Agent 失败保留无独立回归；预算取值待 3.4 实测；3.3 引用追踪未做。详见 `docs/reports/retrieval-injection-2026-11-21.md`。

### 2026-11-21 阶段三 3.3 与阶段二 2.1–2.4

3.3 改口径：模型回复不含规则 ID，"是否引用"无法解析；改为确定性重复注入统计（同一规则跨轮重复注入的比例），由审计事件直接计算。

阶段二：`transplant.py` 统一 Python/Java/Go/C++ 的定位与拼接；Python 行为不变；Go/C++ 语法包进入 `requirements-semantic.txt`；`grounding` 与移植共用命名。

实测：`test_transplant.py` 13 项、`test_injection_summary.py` 3 项、冲突验证相关 4 个文件 53 项通过；全量后端 1020 通过 / 1 失败（缺 cmake）/ 1 跳过。

已知未完成：没有 Java/Go/C++ 真实工程冲突样例，不能推断真实检出率；模板与重载一律转人工。详见 `docs/reports/transplant-languages-2026-11-21.md`。

### 2026-11-21 阶段四与阶段五

核查结论：撤销、失效判定、证据展示这三项**机制已经存在**，缺的是证据而不是功能。因此本轮以验证与说明为主，不新增重复入口。

新增 `test_merge_withdrawal.py`（2 项）：确认等价后检索 1 条、撤销后立即为空、原始记录不变、撤销不是开关。任务指标面板补注入上限与重复注入比例。

明确未实现：自动排队重验（`recovery.enqueue()` 需要绑定修复任务，没有任务时无法执行）；README 更新日志（与手机工作台未提交改动冲突，待其单独提交后补）。详见 `docs/reports/merge-withdrawal-2026-11-21.md`。

### 2026-11-21 3.4 准备

确认 `scripts/run_real_retrieval_comparison.py` 不带 `--live` 时只打印计划（本机输出：4 planned real reuse trials，零调用）。真实执行需要快照与服务，并消耗模型额度，本轮未执行。详见 `docs/reports/retrieval-comparison-prep-2026-11-21.md`。
