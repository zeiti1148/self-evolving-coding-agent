# 经验合并建议实施计划

> **For agentic workers:** Use superpowers:executing-plans，按任务在本会话内实施，完成后一次独立审查。用户要求先拟定详细方案再执行，并明确选择“先只生成合并建议，仍手动确认”；数据库合并操作仍由产品用户确认。

**Goal:** 自动发现兼容且相关的经验配对，解释推荐/拒绝依据，复用人工预览与确认流程。

**Architecture:** SQLite 有界只读扫描 → 纯兼容性预览 → GET 建议接口 → Vue 建议区 → 既有预览/确认。无后台调度、无模型请求、无自动业务写入。

**Tech Stack:** Python、FastAPI、SQLite、Vue 3、TypeScript、pytest、Vitest。

**Spec:** `docs/superpowers/specs/2026-11-14-merge-suggestions-design.md`

## 全局约束

- 保持已有合并、检索、发布与实验行为，原始记录保留。
- 仅 active V3、有适用条件/问题/证据的当前记录；分语言、范围、仓库。
- 扫描上限 1000、候选上限 200、返回默认 10/上限 20；建议为双记录，所有截断显式报告。
- 新读取严格只读，不通过会迁移的连接帮助函数；前端能力可选，兼容旧接口对象。
- 不使用真实模型、不写生产经验、不提交或推送，不触碰现有演示文件和 output。

## Review Focus

1. 同标签但无共同规则/文件的经验不能被拼接（Task 1）。
2. 条件中的大小写、空白、子集关系不能被“相似度”抹掉（Task 1）。
3. 只读请求不能触发旧库升级或创建数据库（Task 1/2）。
4. 建议到确认之间来源已被替代，必须刷新或拒绝（Task 2/3）。
5. 快速筛选/卸载/确认期间旧建议响应不能覆盖当前状态或偷偷保存（Task 3）。

### Task 1：有界只读建议与兼容性复用

**Files:** 新建 `backend/experience/suggestions.py`、`backend/tests/test_merge_suggestions.py`；修改 `backend/experience/store.py`。

**Interfaces:**
- `build_merge_preview(sources: list[Experience]) -> dict`：从 `_preview` 提取纯业务校验，返回既有预览结构，稳定排序而不修改传入对象；`_preview` 仍负责当前状态、历史幂等与缺失来源检查。
- `suggest_merges(path: Path, *, limit=10, language=None, repository_id=None, scope=None, reuse_status=None) -> MergeSuggestions`：严格遵循规格的响应字段和上限。
- `ExperienceStore.suggest_merges(...) -> MergeSuggestions`：薄封装，后续 API 调用它，不更改 store 构造和保存路径。

- [x] 写测试：相同问题去重、同文件互补问题、无关记录拒绝；条件/能力/仓库/语言/状态/替代/版本提示；顺序/规则/证据/容量冲突。
- [x] 运行新测试观察缺少接口导致的失败；再提取纯预览、实现只读候选和排序。
- [x] 写并验证真实库哈希不变、缺失/九列旧库不创建升级、扫描/候选/返回截断、重复请求稳定；观察失败后补齐实现。
- [x] 运行 `pytest backend/tests/test_merge_suggestions.py backend/tests/test_experience_v2.py backend/tests/test_experience_promotion.py -q`，预期全部通过。

### Task 2：建议 API 与确认边界

**Files:** 修改 `backend/api/experience_routes.py`；新增 `backend/tests/test_merge_suggestions_api.py`；更新 `docs/interfaces.md`。

**Interfaces:** `GET /api/experiences/merge-suggestions` 返回 Task 1 的 `MergeSuggestions`，query 参数与规格一致，注册在 UUID 动态路由前。

- [x] 写 API 测试验证 200/422/503、筛选、路由、无自动合并；先看到失败再注册路由及响应模型。
- [x] 建立两条经验→取建议→人工合并一条来源→再次预览旧建议的集成用例，断言现有冲突逻辑拒绝或刷新，不额外生成记录。
- [x] 运行两组建议测试及既有 V3 API 回归，预期通过；同步公共接口字段说明。

### Task 3：页面建议、HTTP 合约与手动确认

**Files:** 新增 `frontend/src/components/MergeSuggestions.vue`、`frontend/tests/mergeSuggestions.test.ts`；修改 `frontend/src/api/contracts.ts`、`http.ts`、`validation.ts`、`frontend/src/views/ExperiencesView.vue`。

**Interfaces:**
- `AgentApi.getMergeSuggestions?(filters?: ExperienceFilters, signal?: AbortSignal): Promise<MergeSuggestions>`，只传后端支持的四项筛选，默认 limit=10。
- `MergeSuggestions.vue` 接收 `filters`、`refreshKey`、`disabled`，发出 `select(sourceIds: string[])`；内部读取与取消独立，UI 文案来自规格。
- 页面选建议时替换 `selected` 后调用现有 `prepareMerge`；确认/发布成功递增刷新键，分页不刷新建议。

- [x] 写 Vitest：首次自动 GET、筛选/刷新、错误重试、零结果及截断、过期响应丢弃、卸载取消；先观察失败，再实现组件。
- [x] 写 HTTP 解析测试：来源 UUID 唯一、两标题、有限计数、已知原因、截断枚举、错误结构抛 502；再实现类型、校验和适配器。
- [x] 页面测试断言点建议只发 preview，确认前 merge 调用为 0；确认后刷新建议，409 保留明确错误并要求重新预览。
- [x] 新 API 方法为可选：既有 mock 和旧测试客户端继续运行，未支持时隐藏建议组件；不构造假“真实建议”。
- [x] 运行前端全量测试和 build，预期全部通过。

### Task 4：只读现状验证、审查与交付

**Files:** 更新 `docs/experience-v3.md`、`README.md`；新增 `docs/reports/merge-suggestions-2026-11-14.md`；当前计划复选框。

- [x] 对当前生产库只读运行新服务，核对扫描数、建议和拒绝数，前后数据库指纹相同；保存结果，零建议如实解释。
- [x] 运行后端全量测试、前端全量测试/构建/接口联调及 `git diff --check`；不重复已通过且后续未影响的检查。
- [x] 完成一次独立最终审查，重要发现先回归复现再修复；记录未覆盖的真实模型效果边界。
- [x] 文档说明建议入口、手动确认、扫描上限、版本提示和无结果含义；完成计划，保持未提交状态交付。

## 执行说明

已有工作区包含本会话完成的 V3 检索与实验改动，将在其上增量实施，不重建或清理用户内容。所有写测试使用独立临时库。当前用户已指定手动确认策略；实施完成后不会替用户点击产品中的确认合并。

**完成记录：** 详见 `docs/reports/merge-suggestions-2026-11-14.md`。上传前全量后端 666 passed、1 skipped；建议服务专项 22 passed；前端 135 passed，类型检查/构建/联调通过。独立审查的文件级只读过度承诺已按 SQLite 的真实边界修正并补充验证，经验内容和表结构保持不变。用户随后授权将本轮开发更新提交并上传 GitHub，演示文稿和 output 明确排除。
