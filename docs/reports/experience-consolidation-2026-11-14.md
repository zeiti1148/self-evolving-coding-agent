# 自动经验合并与冲突处理验证记录

日期：2026-11-14（Asia/Shanghai）。基线：远端 `origin/main@d724266`；执行 `git fetch origin --prune` 后，本地主分支与远端一致。在 `feature/experience-auto-merge` 分支完成本次修改。

## 本次交付

- 成功批次保存后自动整理精确重复规则；历史经验可按仓库与语言分批整理。
- 规则重叠、验证证据不一致、修复依赖成环的结构化诊断，以及持久化冲突队列。
- 待处理来源隔离，采纳来源或保留原记录并暂停复用，版本检查、幂等重试与处理依据留存。
- 复用现有 SQLite 事务、唯一约束、索引及 Python 标准库 graphlib；没有增加运行时依赖，也没有添加额外 LLM 调用。
- 页面来源对比、处理状态筛选、分页、处理历史、过期决定的输入保留，以及大库整理游标。
- 只读实验快照同步应用冲突过滤；来源任务、批次 ID、历史经验和验证证据保持可追溯。

## 实际执行

| 验证 | 结果 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest backend/tests -q` | 最后一轮全量为 **599 passed、1 skipped**，耗时 113.81 秒 |
| `.venv/Scripts/python.exe -m pytest backend/tests/test_experience_consolidation.py backend/tests/test_experience_v2.py backend/tests/test_experience_promotion.py -q` | 全量检查后补充「单条导入记录内部证据不一致」边界修复与一项测试，最终相关 **121 passed** |
| `npm.cmd --prefix frontend test` | **129 passed**，13 个测试文件 |
| `npm.cmd --prefix frontend run build` | Vue/TypeScript 检查与 Vite 生产构建通过 |
| `npm.cmd --prefix frontend run check:integration` | 5 个场景、66 个公共模型对象、SQLite 7 条记录及真实 HTTP 适配器/代理检查通过 |
| `.venv/Scripts/python.exe scripts/acceptance_v3.py` | **13 个场景符合预期**，使用脚本模型与真实 API、Maven、pytest、SQLite |
| 浏览器实测 | 独立测试库中检查 4 条来源、生成 1 条合并记录、显示 1 项冲突；来源对比、采纳来源、保存依据及刷新后读取历史均通过 |
| `git diff --check` | 通过 |

后端跳过项为 Windows 当前环境无法创建符号链接的既有测试。现有 Starlette/HTTPX 弃用提示仍存在。未执行真实模型付费验收，以上结果不证明语义等价判断能力或真实模型效率提升。

V3 场景包括 Java/Python 来源、合并后复用、发布前后跨仓库隔离、能力不匹配、single 兼容、预算耗尽、角色异常与经验保存失败。错误路径按预期出现 stopped/failed 或“修复成功但经验保存失败”，不把这些场景写成全部任务修复成功。

本机详细 V3 报告：`.cache/acceptance-v3/20261114T100030Z-fcd6c440/report.json`。浏览器使用 `.cache/experience-consolidation-ui-f9rmvr8s/` 内的合成数据与独立数据库，截图为该目录的 `review-history.jpg`；测试服务已停止。该界面样例仅验证交互，不作为真实修复证据。未用业务数据库执行整理，也未改动 `self-evolving-coding-agent-next` 中的未提交内容。

## 覆盖与限制

新增测试覆盖批次自动合并、原始 ID 重试、仓库/条件/能力隔离、非重复知识独立保留、多问题规则去重、来源追踪、规则/顺序/证据冲突、隔离后的检索及发布阻断、冲突持久化、并发保存与相反决定、事务故障回滚、旧索引回填、扫描上限与游标、实验快照只读性，以及 HTTP 校验和前端过期响应。

自动合并只认结构化 `issues` 的精确重复，不对自然语言改写作语义等价承诺。每个分组最多检查 60 条当前来源，超限整体保留；每次历史整理最多 100 组，返回游标继续。具体使用方法和内容上限见 [功能说明](../experience-consolidation.md)。
