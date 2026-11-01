# 经验演进 V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 产品内多 Agent 是交付功能，不要求开发时并行修改共享文件。

**Goal:** 完成真实多经验产出、可追溯合并、多 Agent 分工、仓库隔离和同语言跨仓库通用复用的完整闭环。

**Architecture:** 在现有同步 Agent、受控工具、FastAPI、SQLite 和 Vue 上增量实现。多角色使用独立上下文，协调器唯一写入，真实最终测试决定成功；经验批次、合并及通用发布分别事务化并保存来源。

**Tech Stack:** Python 3.11+、Pydantic 2、FastAPI、SQLite、Vue 3、TypeScript、pytest、Vitest、Java/Maven 与 Python/pytest 工具执行器；沿用已配置模型服务，无新 Agent 框架或外部数据服务。

**Spec:** [完整设计](../specs/2026-10-29-experience-collaboration-v3-design.md)。状态：待审阅的实施计划；所有未勾选步骤均未完成。

## Global Constraints

- 本轮规划和后续实施均不自动 Git 提交、推送、创建 PR 或合并 main；下文提交消息仅为获得后续授权时的建议，完成步骤以验证结果和记录为准。
- 先完成一个可验证步骤再进入下一步；实现过程中持续报告改动、结果、偏差，不把整个计划一次性标为完成。
- 推荐在现有架构上增量扩展、由协调器统一写入；保留 single 模式默认值。
- max_steps 默认 20、上限 100；多角色共享计数；整体默认时限 600 秒；成功提取最多额外一次请求。
- 每批 1–5 条经验；V3 样例各 18 项测试、每样例 3–5 条经验，至少 3 条含 2 个以上相关问题。
- 经验合并保持 2–10 个不同来源；新数据写 schema_version=3，读取兼容 1/2/3。
- 第一期只支持同语言跨仓库；SQLite 逻辑隔离不替代用户权限系统。
- 最终测试须 tests>0 且 failures=errors=skipped=0，工具成功且证据来自实际最终执行。
- 任务身份来自服务端登记；原始样例、验收测试、构建/依赖配置不允许修复 Agent 改写。
- 旧 global 未审阅记录禁止默认注入；旧库只读兼容不等于继续信任来源不明规则。
- live 验收与脚本模型结果分开；运行数据、密钥和工作副本不提交。执行真实模型前先核实本地配置与预计调用规模，不打印密钥。

## Review Focus

1. 默认 global 的旧记录升级后误进入新仓库上下文：任务 1、3 验证迁移状态和每个注入入口。
2. 批量保存中途失败、同任务重复提取造成半批/重复知识：任务 2 使用故障注入和并发验证。
3. 多 Agent 使用旧文件生成提案、预算并发超额、写入半途失败：任务 7、8 验证版本、共享预算和回滚失败终止。
4. 发布后通用文本仍包含原仓库业务含义，或相同异常但目标契约不同：任务 5、6、11 验证抽象边界和反例，保留人工预览与真实目标测试。
5. 后端新增数组、角色事件后旧历史和前端旧 mock 失效：任务 1、9、10 验证旧 JSON 与新接口往返、刷新及重启。

## 实施顺序、里程碑与文件边界

顺序：0 基线 → 1 契约 → 2 批次存储 → 3 隔离 → 4 多经验提取 → 5 合并/通用化 → 6 新样例 → 7 角色基础 → 8 多 Agent 闭环 → 9 API/持久化 → 10 页面 → 11 端到端 → 12 交付。

里程碑 A（0–4）：单 Agent 已能在严格仓库范围内保存多条经验。
里程碑 B（5–6）：新样例与通用化/合并可通过服务层验证。
里程碑 C（7–9）：同一任务内多角色实际协作、过程持久可查。
里程碑 D（10–12）：页面操作和跨仓库闭环完成，证据可复验。

不为每个阶段另造框架。已有模块只接入扩展；新增文件职责如下：

| 新文件 | 责任 |
| --- | --- |
| backend/experience/policy.py | 仓库、语言、发布状态、capability 过滤和注入内容边界 |
| backend/experience/batches.py | 原子批量保存与幂等 |
| backend/experience/promotion.py | 通用化预览与发布 |
| backend/experience/provenance.py | 有界来源图 |
| backend/agent/extraction.py | 公用的成功后批量经验提取 |
| backend/agent/budget.py | 多角色共享时限及决策预算 |
| backend/agent/roles.py | 独立角色上下文与模型输出校验 |
| backend/agent/proposals.py | 提案预检、受控应用与失败恢复 |
| backend/agent/orchestrator.py | multi 模式状态流程 |
| backend/agent/context.py | 版本化共享任务事实，复用 policy 的注入序列化 |
| backend/api/experience_routes.py | 经验相关路由，避免继续扩充 main.py |
| backend/api/agent_events.py | 角色事件存取，复用 TaskStore 的事务所有权 |
| frontend/src/components/AgentTimeline.vue | 角色过程与交接 |
| frontend/src/components/ExperiencePromotion.vue | 通用化编辑、最终注入预览与发布 |
| scripts/acceptance_v3.py | 生产流程的脚本/真实模型验收 |

## Task 0：冻结基线和执行边界

**Files:** 读现有 README、docs/experience-v2-contract.md、docs/backend-reliability.md；运行产物放 `.cache/experience-v3/<run-id>/`，必要时使用本会话可写可视化目录。

**Interfaces:** 输入当前 checkout 和已有工具；输出 baseline.json（commit、工作区状态、命令、退出码、测试数、限制）。

- [x] 记录 `git status --short`、`git rev-parse HEAD`、当前分支和 worktree；保存用户已有变更，从包含此前可靠性修复的当前基线创建 `experience-collaboration-v3` 功能分支，遵守用户去掉 codex 前缀的要求。核对远端 main 的差异，不覆盖或遗漏此前功能分支的成果；确需独立工作区时再使用 worktree。
- [x] 在既有虚拟环境运行 `python -m pytest backend/tests -q`；前端运行 `npm.cmd test`、`npm.cmd run build`、`npm.cmd run check:integration`。基准缺陷项目原始失败不算系统回归。
- [x] 记录已有失败与环境错误，禁止把后续新增回归混入历史问题。若 pytest 临时目录不可写，使用一个新的、已确认在允许目录内的 `--basetemp`，加 `-p no:cacheprovider`；不得复用包含用户文件的目录。
- [x] 用 SQLite backup API 备份实际要升级的数据库；本阶段所有迁移实验首先使用备份副本或测试数据库。
- [x] 保存基线报告。进入 Task 1 的条件是系统基线通过，或环境阻塞有明确复现和处理路径。

旧规划记录的定向四组结果为 169 passed；2026-10-31 本轮仅核对代码与规划，未重新执行测试。历史结果不能代替实施时的全量基线。

## Task 1：公共契约与旧数据读取

**Files:** 修改 `backend/shared/models.py`、`backend/shared/__init__.py`、`backend/shared/errors.py`、`backend/experience/store.py` 的详情字段白名单；新建 `backend/tests/test_models_v3.py`；补充 `backend/tests/test_shared_models.py`。

**Interfaces:**

- `ExperienceCreate` 添加设计中的 schema 3、batch_id、reuse_status、derived_from、supersedes_id、required_capabilities。默认 project=active；旧 global=legacy_unreviewed；专用发布入口负责生成 active global。无证据旧资料的整理结果允许 V3/legacy_unreviewed，不能冒充 active 成功经验。
- `AgentResult.experience_ids: list[str]`；`TaskCreateRequest.execution_mode`、`AgentRequest.execution_mode` 默认 single；`AgentRequest.capabilities: list[str]=[]`。
- `AgentEvent(event_id, task_id, agent_id, role, phase, status, sequence, snapshot_revision, summary, created_at, usage)`；UUID、UTC 时间、严格序号。role 为 diagnosis/repair/review/coordinator/extractor，status 为 started/completed/failed/stopped，usage 保存可空 reported_tokens 与 elapsed_ms。summary 只存简短事实。
- `FileChange(path: str, before_sha256: str, content: str)`；`PatchProposal(proposal_id: str, agent_id: str, base_revision: str, changes: list[FileChange])`；每提案 1–20 文件，整批 UTF-8 内容总计最多 200000 字节；沿用受控写文件的具体边界。
- `DiagnosisReport(issues: list[RepairIssue], dependencies: list[tuple[int,int]], summary: str)`；依赖引用 issues 索引、拒绝越界和环。
- `ReviewDecision(accepted: bool, reasons: list[str])`；拒绝时 reasons 非空。
- `PromotionDraft` 仅含可编辑知识字段、language、applicability、issues、required_capabilities；不接受模型/客户端设置来源、证据、scope 或状态。
- `MergePreview(can_merge, conflicts, source_ids, preview)` 保留既有 JSON 形状；`PromotionPreview(can_publish, conflicts, source_id, current_global_id, draft, injection_preview)`；`ProvenanceGraph(nodes, edges, truncated)`。

- [x] 先写 `test_old_result_expands_single_id`、`test_result_rejects_mismatched_ids`、`test_legacy_global_is_unreviewed`、`test_v3_project_requires_repository`、`test_proposal_limits_and_paths`、`test_event_json_roundtrip`。断言旧 result 的单 ID 读为同值数组、冲突被拒绝、旧字段和日期不丢失。
- [x] 运行 `python -m pytest backend/tests/test_models_v3.py -q`，确认缺失字段/校验导致预期失败。
- [x] 实现兼容规范化：在模型校验前区分字段缺失与显式冲突；只有保存成功允许非空 ID 数组，失败和未尝试数组为空。扩展导出和详情白名单，不改旧九列值。
- [x] 运行新增测试及 `test_shared_models.py`、`test_experience_store.py`；预期全部通过。
- [x] 审阅变更并记录完成；提交建议 `feat: define v3 experience and collaboration contracts`。

## Task 2：多经验批次原子保存

**Files:** 新建 `backend/experience/batches.py`、`backend/tests/test_experience_batches.py`；修改 `backend/experience/database.py`、`backend/experience/store.py`、`backend/experience/__init__.py`。

**Interfaces:** `ExperienceStore.save_experiences(items: list[ExperienceCreate], *, source_task_id: str) -> list[Experience]`；保持旧 `save_experience`。新增 `experience_batches(batch_id, source_task_id UNIQUE, payload_hash, created_at)` 和 `experience_batch_items(batch_id, ordinal, experience_id UNIQUE)`；ordinal 保持输出顺序，关联外键。

- [x] 写 `test_batch_three_records_share_task_and_evidence`、`test_invalid_second_record_writes_nothing`、`test_insert_failure_rolls_back_batch`、`test_concurrent_same_batch_returns_same_ids`、`test_same_task_changed_batch_conflicts`。断言 3 条 ID 不同、证据同一测试不是三次测试、失败零新增、并发只一批。
- [x] 运行 `python -m pytest backend/tests/test_experience_batches.py -q`，确认预期失败。
- [x] 实现先校验、规范化知识载荷哈希、事务幂等检查和批量插入；哈希排除服务器生成 batch_id/id/time，但包含真实来源与最终证据；拒绝同批完全重复记录，避免把仅换标题的相同规则当新知识。
- [x] 使用 SQLite 触发器在第二条插入时注入失败；重开数据库验证经验、批次和关联均无残留。验证旧单条保存行为不变。
- [x] 跑新增及经验旧测试；提交建议 `feat: save experience batches atomically`。

## Task 3：统一仓库隔离与注入策略

**Files:** 新建 `backend/experience/policy.py`、`backend/tests/test_experience_policy.py`；修改 `backend/experience/store.py`、`backend/projects.py`、`scripts/register_project.py`、`backend/agent/core.py`。

**Interfaces:**

- `RetrievalContext(language: str, repository_id: str | None, capabilities: frozenset[str])`。
- `is_eligible(entry: Experience, context: RetrievalContext) -> bool`；`to_model_reference(entry: Experience) -> dict`。
- `retrieve_experiences` 保留已有参数，新增 keyword-only `capabilities: list[str] | None = None`；排序仍先同仓库，再异常交集、关键词与稳定顺序。
- `ProjectDefinition.capabilities: list[str]=[]`；登记脚本接受可重复 `--capability`，项目 API 返回能力标识，不返回磁盘路径。

- [x] 写表驱动用例覆盖 A/B/无仓库、Java/Python、active/candidate/legacy、被替代记录、能力匹配/缺失。断言 A project 永不进入 B；无身份只得到 active global；limit 应在过滤后生效。Task 5 增加发布替代关系后补测旧发布版不能再次注入。
- [x] 先运行 `python -m pytest backend/tests/test_experience_policy.py -q` 确认失败。
- [x] 在生产检索入口统一使用 policy。管理列表仍可显式列出历史，不能用于注入。梳理 `rg 'search_experiences|retrieve_experiences|reference_experiences' backend scripts tests` 的所有调用者，更新实际注入路径及实验夹具。
- [x] 对旧 global 缺少状态按 legacy_unreviewed 解码，不改写原始内容；对实验中可信训练数据显式绑定登记仓库或执行发布流程，禁止用“为了测试通过”方式全量信任旧数据。
- [x] 验证 `test_global_payload_omits_private_provenance`：注入 JSON 不含 repository_id、source_revision、源任务描述和来源图；管理详情保留。回归隔离、项目 API 和实验测试并记录结果。

## Task 4：成功任务直接生成多条复杂经验

**Files:** 新建 `backend/agent/extraction.py`、`backend/tests/test_agent_extraction_v3.py`；修改 `backend/agent/core.py`、`tests/scripted_llm.py` 及受影响模型夹具。

**Interfaces:** `extract_and_save_experiences(llm: LLM, store: ExperienceStore, request: AgentRequest, *, summary: str, changed_files: list[str], calls: list[ToolCall], results: list[ToolResult], deadline: float) -> list[Experience]`。输入是实际最终通过后的轨迹，供 single/multi 两种模式共用。

- [x] 写 `test_one_repair_saves_three_complex_experiences`：一次无工具提取响应含三项、各含两个问题，结果 3 IDs 且旧 experience_id 等于第一项；只有一次提取调用。
- [x] 写 `test_model_cannot_override_scope_or_evidence`、`test_failed_or_stopped_task_never_extracts`、`test_invalid_batch_preserves_repair_success`、`test_missing_repository_never_autopublishes`。断言事实来自程序、失败保存不改变修复已通过事实。
- [x] 运行新增测试观察预期失败；抽取当前 core.py 中提取逻辑，使用 `{experiences:[...]}` 形状和 Task 2 原子接口。
- [x] 允许旧单对象提取响应被包装为一条用于兼容旧测试/模型；V3 验收必须返回数组并满足多条指标。校验每项使用真实 changed_files/轨迹，未知文件、无证据问题拒绝；不尝试用文本校验声称证明全部根因正确。
- [x] 回归 Agent、V2、实验与 API；旧 V2 acceptance 可继续检查首条的兼容字段，V3 另行严格检查整批。提交建议 `feat: extract multiple verified experiences per task`。

## Task 5：合并补强、来源查询和通用发布

**Files:** 新建 `backend/experience/promotion.py`、`backend/experience/provenance.py`、`backend/tests/test_experience_promotion.py`、`backend/tests/test_experience_provenance.py`；修改 store.py/database.py；扩展 `backend/tests/test_experience_v2.py`。

**Interfaces:** `preview_promotion(source_id: str) -> PromotionPreview`、`publish_global(source_id: str, draft: PromotionDraft, *, supersedes_id: str | None=None) -> Experience`、`get_provenance(experience_id: str, *, max_nodes: int=100, max_depth: int=10) -> ProvenanceGraph` 均由 ExperienceStore 暴露。新增 `experience_promotions(promotion_key PRIMARY KEY, source_id, global_id UNIQUE)`、`experience_publication_heads(source_id PRIMARY KEY, global_id UNIQUE)`、`experience_publication_revisions(previous_id PRIMARY KEY, new_id UNIQUE)`；来源详情从关联关系构建。外键约束引用真实经验记录。

- [x] 写合并测试：不同 reuse_status/capabilities 拒绝；same sources 幂等；预览后来源被另一合并消费时提交冲突；相同最终证据不重复计算验证次数；无证据 legacy 合并后仍 legacy、evidence 为空、不能注入及发布。
- [x] 写发布测试：只接受有真实证据的 active project、当前有效 global 合并结果或已审阅重发的 legacy global；candidate 和被替代来源拒绝；发布保留原记录、设置 global/active 和 derived_from；客户端不能伪造 evidence；并发重复发布同一版本只一条；事务失败无残留。对有效 global 合并结果重新通用化时，新发布记录也须建立替代关系排除旧合并结果注入，防止重复规则。
- [x] 写修订测试：同源有当前版本而缺少 supersedes_id 时返回冲突；指定当前前版后原子替代；过期前版及已被合并消费的前版拒绝；并发两种修订只一方成功；旧版本仍可查但不再注入。发布幂等键覆盖 source_id、规范化 draft 和 supersedes_id；先检查已有幂等结果再检查当前版本。
- [x] 写来源图测试：二级合并再通用化可回溯，包含 merged_from/derived_from/supersedes_id 三类关系；无环、超过节点/深度限制时 truncated=True；缺失来源明确报错，不编造节点内容。
- [x] 运行新增测试失败后实现：复用既有合并事务；通用预览只返回内存 candidate 草稿，未提交不写库；最终发布重新校验且为独立新记录。自由文本由用户改写，展示最终注入预览；不以扫描器保证语义通用。
- [x] 验证 A 专属经验不消失；B 只能得到新 global 的公开载荷；冲突业务前提保留在 applicability；重跑经验全组并记录结果。

## Task 6：构造能产出多条复杂经验的独立样例

**Files:** 新建 `benchmarks/experience-v3/catalog.json`、`README.md`、`java-checkout/`、`python-checkout/`、`java-booking-transfer/`、`python-booking-transfer/`；新建 `backend/tests/test_experience_v3_catalog.py`、`backend/tests/fixtures/experience_v3_fixes.json`；修改 `backend/config.py` 的目录加载及配置测试。

**Interfaces:** 目录契约沿用 ProjectDefinition；源项目 ID 为 `java-checkout-complex`、`python-checkout-complex`，独立目标为 `java-booking-transfer`、`python-booking-transfer`，四个 repository_id 不同。

- [x] 为 checkout 设计三组知识，每组两个缺陷：缺失与空白规范化、数值格式与范围校验、索引边界与集合空值。每组 4 个边界/异常测试，共 12；另加 3 正常行为和 3 组合流程测试，共 18。两种语言行为契约对应，但文件与语言规则独立。
- [x] 为 booking 目标各设计 12 项测试：每组三项迁移/正常输入测试共 9，再加 3 个不同默认值或拒绝策略的反例，确保必须根据目标约束适配。capabilities 只声明真正共有的机制，不把业务默认值写成共有能力。
- [x] 构造 baseline 测试：每个原样例真实执行均失败且实际发现测试数等于 catalog；修复夹具在受控副本通过全部测试；原目录哈希不变。
- [x] 在生产目录保存有缺陷源码和固定测试；修复答案只放 tests 夹具，不注入模型上下文；任务描述不包含目标修复代码。
- [x] 运行 `python -m pytest backend/tests/test_experience_v3_catalog.py backend/tests/test_config.py -q`；检查无测试/依赖目录可写权限回归。记录样例和对应说明。

## Task 7：角色上下文、共享预算和修改提案

**Files:** 新建 `backend/agent/context.py`、`budget.py`、`roles.py`、`proposals.py`、`backend/tests/test_agent_roles.py`、`test_agent_proposals.py`、`test_agent_budget.py`。

**Interfaces:**

- `RunBudget(max_decisions: int, deadline: float)`：`reserve_decision() -> int`、`remaining_seconds() -> float`；线程安全，超预算抛现有 AGENT_STOPPED。
- `TaskContext` 保存 AgentRequest、snapshot_revision、只读源码快照、失败事实、诊断、已审提案和公开经验引用；上下文更新由 Coordinator 执行，不向角色暴露可变共享对象。
- `RoleRunner(llm_factory: Callable[[], LLM], budget: RunBudget)`：`diagnose(context: TaskContext, agent_id: str) -> DiagnosisReport`、`propose(context: TaskContext, agent_id: str) -> PatchProposal`、`review(context: TaskContext, proposal: PatchProposal, agent_id: str) -> ReviewDecision`。
- `apply_proposal(proposal: PatchProposal, request: AgentRequest, execute_tool: Callable, *, snapshot_revision: str) -> list[tuple[ToolCall, ToolResult]]`；返回实际工具调用/结果，失败通过明确错误中止，不隐藏已经完成的写入轨迹。

- [x] 写并发预算测试：两个线程争取最后一个决策名额，恰好一方成功；deadline 已过无新模型/工具请求；各客户端审计独立。
- [x] 写角色工具测试：诊断/修复/评审请求写文件或执行命令被拒绝；提案由协调器才可应用；角色消息包含相同任务身份但不共享会话列表。
- [x] 写提案测试：旧哈希、重复路径、路径穿越、大小写别名碰撞、测试/配置写入、超大提案全部拒绝；多个文件预检失败时零写入；第二文件写失败恢复第一文件，恢复失败停止任务。
- [x] 运行上述三组确认失败；实现受控读取及 before_sha256 检查、统一写入、真实工具轨迹与恢复记录。并行分析期间不执行写入。
- [x] 重跑工具路径保护和预算测试，确认不扩大 ToolService 权限并记录结果。

## Task 8：多 Agent 协调闭环

**Files:** 新建 `backend/agent/orchestrator.py`、`backend/tests/test_orchestrator.py`；按需抽取 core.py 可复用的真实最终验证辅助函数，不复制整个单 Agent 循环。

**Interfaces:** `MultiAgentCoordinator(llm_factory, execute_tool, experience_store, *, use_experience=True, timeout_seconds=600)`；`run(request: AgentRequest, *, on_step: Callable[[TaskStep], None] | None=None, on_event: Callable[[AgentEvent], None] | None=None) -> AgentResult`；保留 `last_run_audit`。

- [x] 写 happy path：真实工具复现失败 → 两个只读诊断可并行 → 修复提案 → 独立评审 → 协调器写入 → 真实最终验证 → Task 4 批量提取；断言至少 diagnosis/repair/review 三种独立角色调用。
- [x] 写恢复用例：评审拒绝不写文件；只修一组最终测试仍失败后继续；新异常重新检索仍受仓库策略；重复失败最终 stopped；任一角色客户端报错释放资源且无假成功。
- [x] 写预算/证据用例：全角色合计不超过 max_steps；失败和预算耗尽不提取；最终测试后再发生写入必须重新验证；每个 role event 标明快照与调用归属。
- [x] 实现有限状态流程和固定最多两个并行诊断 worker；不建立自由发言循环。所有模型调用共享截止时间，各 client 在 finally 关闭；发生角色错误不悄悄切换 single 冒充 multi 成功。
- [x] 汇总只记录事实、token（缺失为 null）、耗时、提案/经验 ID。跑新增及旧 Agent 全组；提交建议 `feat: coordinate diagnosis repair and review agents`。

## Task 9：API 与角色事件持久化

**Files:** 修改 `backend/api/main.py`、`backend/api/store.py`；新增 `backend/api/experience_routes.py`、`backend/api/agent_events.py`、`backend/tests/test_collaboration_api.py`、`backend/tests/test_experience_v3_api.py`；补充 task_store 测试。

**Interfaces:**

- `TaskStore.append_agent_event(task_id: str, event: AgentEvent) -> None`、`get_agent_events(task_id: str, *, after_sequence: int=0, limit: int=100) -> list[AgentEvent]`；limit 1–100，事件序号由单协调器分配。
- 新增 `api_agent_events(task_id, sequence, event_id UNIQUE, event_json)`，task_id/sequence 唯一；事件和必要的任务状态变化在 TaskStore 事务内更新。
- `POST /api/tasks` 接受 execution_mode；旧请求省略则 single；GET task 结果包含 experience_ids，保留旧 ID。
- `GET /api/tasks/{id}/agents?after_sequence=0&limit=100` 返回角色事件；未知任务 404，存储故障 503。
- `GET /api/experiences/{id}`、`GET /api/experiences/{id}/provenance`；`POST /api/experiences/promotion-preview` body={source_id}；`POST /api/experiences/publish-global` body={source_id,draft,supersedes_id?}。
- 现有 `/merge-preview`、`/merge` 和列表响应保持兼容。固定路径在动态详情路径前注册；来源缺失 404、语义/版本冲突 409、请求形状不合法 422、存储不可用 503。
- `GET /api/experiences` 和 `list_experiences` 新增可选 reuse_status、source_task_id、active_only=false 筛选；先过滤再分页，默认保留管理列表旧行为。前端 ExperienceFilters 同步扩展。

- [x] 写兼容 API 测试：旧请求跑 single；multi 使用独立 factory；不能提交 repository_id/任意路径；新数组、事件、预览响应可序列化。
- [x] 写持久化测试：重建 API 后终态、角色事件和经验链接保留；未完成任务/角色 stopped；重复事件和终态后追加拒绝；并行诊断完成顺序不破坏全局事件序号。
- [x] 实现路由与持久化；API 上层作为组装入口，不把角色逻辑放进 HTTP handler。沿用现有后台任务执行器与单实例边界。
- [x] 验证同一 task 的事件不出现在其他 task；存储错误不返回伪空列表；任务结果记录失败时沿用现有明确故障路径。
- [x] 跑 API、task_store、project_api 和新增全组；提交建议 `feat: expose persistent collaboration and experience workflows`。

## Task 10：Vue 页面和接口联调

**Files:** 修改 `frontend/src/api/contracts.ts`、`http.ts`、`validation.ts`、`mock.ts`、`fixtures.ts`、`mockStorage.ts`、`views/NewTaskView.vue`、`views/TaskView.vue`、`views/ExperiencesView.vue`、`components/ExecutionSteps.vue`、`components/ResultSummary.vue`、`components/ExperienceKnowledge.vue`；新增 AgentTimeline.vue、ExperiencePromotion.vue、对应 tests。

**Interfaces:** AppApi 增加 `getAgentEvents(taskId, afterSequence, signal?)`、`getExperience(id, signal?)`、`getProvenance(id, signal?)`、`previewPromotion(sourceId, signal?)`、`publishGlobal(sourceId, draft, supersedesId?, signal?)`，返回 Task 1/9 对应 DTO。JSON validation 与 mock 同时更新。

- [x] 写组件/适配器测试：新建页默认 single；切 multi 后提交正确字段；任务页展示角色与真实测试；旧单 ID 和新多 ID 都可点开详情；保存失败仍准确显示修复成功。
- [x] 写经验页测试：按仓库/语言/状态查看、多选合并预览、冲突理由、来源链、通用化可编辑草稿、最终注入内容及发布后新记录；修订携带 current_global_id，冲突时刷新预览而不自动覆盖；过期 HTTP 响应不得覆盖新筛选或草稿。
- [x] 实现页面并保持原视觉体系；候选/未审阅明确显示，不能显示成“已可跨仓库使用”；失效来源、服务报错、空结果都有可理解提示。
- [x] 协作事件使用分页/游标轮询，终态停止，组件卸载取消请求；不重复渲染角色事件，不暴露模型原始内部推理。
- [x] 运行 `npm.cmd test`、`npm.cmd run build`、`npm.cmd run check:integration`；在真实 API 页面走一遍 single/multi、来源详情、合并、发布、刷新。提交前保留截图及命令结果。

## Task 11：五项能力的端到端验收

**Files:** 新建 `scripts/acceptance_v3.py`、`tests/scripted_multi_agent.py`、`backend/tests/test_acceptance_v3.py`；新增报告模板 `docs/reports/experience-v3-2026-10-29.md`（实际执行日更新）；沿用 acceptance_v2.py 作为旧链路回归。

**Interfaces:** `python scripts/acceptance_v3.py` 为脚本模型 + 真工具；`--live` 使用配置的实际模型；`--output-dir` 指定全新目录。每次独立 SQLite、工作副本及 run_id。

- [x] 脚本模型只替代模型响应，走相同生产 Agent/API/提取/发布路径。先执行两种语言 checkout，保留原始失败、部分修复失败、最终 18 通过、multi 角色交接和自然生成的 3–5 个经验 ID。
- [x] 从产出的相容记录选择两条，预览并合并；校验幂等、冲突负例、来源图；后续实际模型请求只注入合并有效记录，不同时注入被替代来源。若两条条件不同必须如实拒绝，另外使用设计为同适用域的真实产出测试成功合并。
- [x] 发布合适的源经验为通用规则，再运行两个 booking 目标，各 12 测试。检查每个角色请求的引用内容：未发布时不含 A 的 project；发布后包含 global ID 且无 A 私有来源载荷。
- [x] 执行语言不同、能力不符、目标业务默认值冲突、candidate/legacy、预算耗尽、角色异常、保存故障负例；重启 API 复验记录及链接；所有原样例哈希不变。
- [x] 先让脚本验收通过，再核实 live 运行规模：首轮至少 4 个任务（两个来源、两个目标），加合并后复用任务；受共享 max_steps 限制，失败记录原样保留，不自动无限重试。
- [ ] live 的实际模型调用验收未完成：2026-10-31 执行 --live 检查到配置缺失，独立报告 not_run（0 次真实调用）；已如实记录限制，后续配置后仍须验证模型、角色用量、耗时、测试和实际注入，不能用脚本结果替代。
- [x] 运行后端全量、前端全量/构建/联调和旧 V2 acceptance。报告每项完成事实及限制；不使用训练目标混合的统计宣称经验提高效率。

## Task 12：复查、演示和交付

**Files:** 更新 README.md、docs/architecture.md、docs/interfaces.md、docs/demo.md、backend/experience/README.md；新增 docs/experience-v3.md；回填本计划与实际报告。

- [x] 独立审查重点：跨仓库每个注入入口、旧 global 迁移、角色工具权限、共享预算、提案恢复、事务幂等、UI 兼容。处理实质发现并重跑受影响检查。
- [x] 编写可复现演示：选 checkout + multi → 查看角色交接 → 最终测试 → 多条经验 → 预览合并 → 发布通用经验 → 切 booking → 看到通用引用及通过测试 → 查询来源及重启历史。
- [x] 写清升级/备份/回退命令、仓库登记和 capabilities 的用途、默认 single、未审阅旧经验处理、同语言限制。清理旧 README 中与 V3 冲突的介绍。
- [x] 核对五项验收矩阵：每项关联代码、测试、运行报告和页面入口，缺失任何一项不能标全完成。
- [x] 最终 `git diff --check`，检查没有运行数据/密钥/原始样例修复内容混入变更；按实际授权处理提交、推送与 PR，不把本计划中的建议提交当作已完成动作。

## 逐步工作的汇报格式

每完成一个任务，汇报：本步结果、改动文件、验证命令与真实结果、下一步。完成条件满足才勾选对应项。普通实现选择按本设计推进；只有目标变化、无法保持兼容或关键验收无法实现时，带具体证据提出调整。

2026-10-29 规划时仅交付设计和计划。2026-10-31 已从 Task 0 执行到 Task 12，实际结果见 docs/reports/experience-v3-2026-10-31.md；真实模型调用验收因配置缺失保留未完成标记。建议提交信息仅是规划建议，本轮按用户要求不自动提交。

## 2026-10-31 执行提示词

以下提示词用于启动实施；本节不表示代码、测试或上传已经完成。优先落实五项用户目标，阶段顺序和数量指标是工程验收方案。

```text
请在 self-evolving-coding-agent 现有 Python + FastAPI + SQLite + Vue 3 项目上，按照 docs/superpowers/specs/2026-10-29-experience-collaboration-v3-design.md 和本实施计划，逐阶段完成经验演进 V3。先核对实际代码与文档差异；普通实现选择由你决定，不重复询问已经明确的需求。

首先保留当前工作区变更，核对 backend-reliability 与最新 main 的差异，从包含此前经验、前端和可靠性修复的基线创建 experience-collaboration-v3 分支。运行现有后端测试、前端测试、构建及接口检查并记录基线。备份要升级的 SQLite 数据库；不直接在原始运行库上试迁移。

依次实现以下内容：
1. 多条复杂经验：一次成功修复直接产出并原子保存 1–5 条独立经验，各有适用条件、问题关系、修复步骤和程序生成的测试证据。失败任务不产出成功经验，不复制或人工拆分记录凑数量。新增 experience_ids，保留 experience_id 兼容旧结果；失败回滚、重复请求幂等。
2. 仓库隔离：由服务端登记确定仓库身份。所有角色和后续追加检索只使用当前仓库有效经验及符合条件的已发布通用经验；其他仓库专属、未审阅和已被替代经验不能注入。管理列表与模型检索分开。
3. 合并和通用复用：完善现有合并预览、冲突拒绝、事务幂等及来源追溯。提供项目经验通用化草稿、最终注入预览与显式发布；发布新记录并保留来源，不把原记录直接改成 global。先支持同语言跨仓库，保留业务适用条件与能力限制，不宣称任何任务都适用。
4. 产品运行时多 Agent：保留 single 默认模式，增加 multi。诊断、修复、复核角色使用独立上下文；修复角色提交修改提案，程序协调器检查文件版本和工具权限后统一应用，独立执行真实最终测试。角色共享步数与总时限，记录交接、拒绝、重试和实际调用用量；模型意见不能替代测试结果。
5. 样例与前端：增加 Java/Python 复杂来源样例及不同仓库目标样例。来源样例设计目标各 18 项测试、6 个相关缺陷，实际产出 3–5 条经验，其中至少 3 条包含 2 个相关问题。目标样例各 12 项测试，包含业务条件不同的反例。Vue 增加执行模式、角色过程、多经验链接、仓库筛选、合并与通用发布操作，保持现有深浅色风格。

每阶段先补关键失败测试，再实现并运行对应检查。覆盖批量保存中断、合并冲突与并发、跨仓库误注入、过期提案、权限越界、预算耗尽、复核拒绝、服务重启及旧数据兼容。用脚本模型替代模型响应，其他部分走真实生产 API、工具、数据库、Maven/pytest；保留原始缺陷样例和固定验收测试。通过后进行页面验收、全量回归和独立审查。

验收必须证明：A 的专属经验不会进入 B 的任何角色请求；发布的通用经验确实进入符合条件的 B 任务；B 按自身约束完成修复并通过真实测试。分别记录检索、注入和最终验证，不把脚本模型结果写成真实模型效果，也不在没有对照数据时宣称更快或更省 token。真实模型验证单独记录，配置缺失时如实说明。

边做边更新计划勾选项和报告，交付代码、迁移说明、验收脚本、实际测试结果及演示步骤。不要只交空接口或模拟页面，不自动提交、推送、创建 PR 或合并 main。若发现无法实现的验收条件，保留证据并说明具体差距，不虚报完成。
```
