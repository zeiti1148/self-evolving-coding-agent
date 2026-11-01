# 经验演进 V3 实施与实际验证报告

执行日期：2026-10-31（Asia/Shanghai）。代码、脚本模型验收和实际页面联调完成；**真实模型验收未完成：环境缺少模型配置，0 次真实模型调用**。没有配对效率对照，不宣称更快或更省 token。

分支 `experience-collaboration-v3`，基线 `backend-reliability@8b4aa9394ea54f2392f636798d1befb94f77d2ac`。保留此前可靠性改动和原有未跟踪设计/计划，原文备份在 `.cache/experience-v3/20261031/`。未提交、上传、创建 PR 或合并 main。

机器可读摘要：[experience-v3-2026-10-31.json](experience-v3-2026-10-31.json)。完整模型请求引用、工具输出、角色事件和任务状态位于本地 `.cache/experience-v3/20261031/acceptance-final/report.json`；摘要保留任务 ID、实际注入 ID/内容哈希、测试结果及最终代码哈希所在位置。

## 阶段结果

| 阶段 | 实际交付 | 验证事实 |
| --- | --- | --- |
| M0 | 指定分支、保留可靠性基线、原文副本、数据库检查 | 基线后端 392 通过（41.19 秒）、前端 78 通过；构建/联调通过；只 fetch main，无合并 |
| M1 | V3 契约、一次提取 1–5 条、原子批次/任务去重、多 ID 兼容 | M1 全量 440 通过；两种复杂来源直接各产生 3 条独立经验，各含两个相关问题；最终 18 用例；第二项插入故障整批回滚 |
| M2 | 全入口隔离、合并冲突/去重/并发、来源图 | 发布前两个不同仓库目标实际注入均为 0；合并后任务只收到有效合并记录，不收到两个被替代来源；并发批次/重复合并/重叠合并测试通过 |
| M3 | 可编辑通用化预览、显式发布、版本冲突、原始来源保留 | Java/Python booking 的诊断、修复、复核均收到已发布 global；各通过自身 12 项测试；能力不匹配目标注入 0 |
| M4 | 独立角色上下文、共享预算、版本提案、程序应用和独立测试 | 真实生产协调器执行角色调用和交接；旧提案/拒绝/部分修复/预算/异常/恢复覆盖；编译失败真实 Maven/pytest 恢复测试通过。模型回复本轮为脚本，不冒充真实模型能力 |
| M5 | 执行模式、角色时间线、多经验链接、筛选、合并、发布和来源详情 | 真实 Edge→Vue→API 三个页面任务 18/12/18 测试；深浅色截图；刷新及 API 重启前后任务、角色、经验、来源图一致；控制台错误 0 |
| M6 | 四个复杂/迁移样例、异常测试、验收脚本、升级说明、独立审查 | 最终后端 **498 passed**（132.96 秒，1 既有弃用提示）；前端 **87 passed**；TypeScript/Vite 构建、接口联调、V2 和 V3 验收均通过 |

### 数据库与基线环境

配置的 `data/app.db` 实际不存在，因此没有可备份运行库，未创建空库冒充备份；交付时仍不存在。历史缓存数据库未被升级。新增 `scripts/backup_database.py` 使用 SQLite backup API、包含已提交 WAL、拒绝覆盖、验证完整性并生成 SHA-256 清单；两个实际 SQLite 测试通过。

初次 Vitest 在受限系统临时目录产生 EPERM，改用工作区 TEMP/TMP 后通过。本机 HTTP/后端 API 测试需允许本地服务/子进程；受限尝试停止后，获准运行通过。这些环境尝试与产品缺陷分开记录。

## 最终执行命令

在仓库根目录 PowerShell，使用现有 `.venv`、Maven 3.9.11 和登记测试环境：

```powershell
$env:PYTHONUTF8 = '1'
$env:TEMP = "$PWD\.cache\experience-v3\20261031\node-temp"
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp .cache/experience-v3/20261031/full-final
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run check:integration
.\.venv\Scripts\python.exe scripts/acceptance_v3.py --output-dir .cache/experience-v3/20261031/acceptance-final
.\.venv\Scripts\python.exe scripts/acceptance_v3.py --live --output-dir .cache/experience-v3/20261031/acceptance-live
.\.venv\Scripts\python.exe scripts/acceptance_v2.py --output-dir .cache/experience-v3/20261031/acceptance-v2-final
```

重跑时为 `--basetemp` 和 `--output-dir` 选择新目录，保留本轮证据。前端联调：5 场景、66 公共对象、7 条 SQLite 记录，包含 HTTP/Vite 代理与错误结构拒绝；该联调本身不执行模型/Maven。日志分别为 `full-final.log`、`frontend-final.log`、`build-ui-final.log`、`integration-final.log`、`acceptance-final.log` 和 `acceptance-v2-final.log`。

## V3 脚本模型真实 API 验收

每个任务使用原始缺陷样例的新副本。来源的真实测试进程先失败，脚本先提交部分修复再失败，然后完整修复通过；不是通过复制或人工拆分数据库记录得到多经验。两种语言各从本次自然产出的相容记录选择两条合并。目标默认值/数值越界政策与来源不同，用原始目标测试验证，未修改测试或配置。

“注入数”是该任务所有实际请求中的不同经验 ID 数，不能与检索命中混为一谈；“保存数”是返回的新经验 ID 数。最终测试次数是不同工具调用数，多条经验引用同一工具调用不重复累计。

| 场景 | 任务 ID | 终态 | 最终测试 | 实际注入数 | 保存数 |
| --- | --- | --- | --- | --- | --- |
| java-source | `f80f80d4-2e9b-4ac1-843e-3693d35cae8c` | succeeded | 18 通过 | 0 | 3 |
| java-after-merge | `4e498bd6-3d4e-4d2e-9f5c-2af9737c01f1` | succeeded | 18 通过 | 2 | 3 |
| python-source | `6b4b707d-1b4e-4c1e-9189-437bbe16768c` | succeeded | 18 通过 | 0 | 3 |
| python-after-merge | `56496132-b806-4874-a65c-a4ce8e222c16` | succeeded | 18 通过 | 2 | 3 |
| java-before-publication | `602b5df8-cb09-4d52-96a9-d428a76b2735` | succeeded | 12 通过 | 0 | 3 |
| python-before-publication | `a4d77af6-cf37-4ad2-9666-5016b093d0cc` | succeeded | 12 通过 | 0 | 3 |
| java-after-publication | `079f8fbb-140e-4888-85e1-db75fe5f2d81` | succeeded | 12 通过 | 4 | 3 |
| python-after-publication | `c6f0eb96-1766-4e09-b697-3a547aa0b51d` | succeeded | 12 通过 | 4 | 3 |
| capability-mismatch | `5e13f605-8ba5-4afa-9cf9-cf7791953627` | succeeded | 12 通过 | 0 | 3 |
| single-compatibility | `abf6c16a-cf9c-4e4e-bc9b-76551d3f4676` | succeeded | 12 通过 | 5 | 3 |
| shared-budget-exhaustion | `10520521-1bb4-46fa-983f-cc85f89a4893` | stopped | — | 5 | 0 |
| role-exception | `3ab93bb7-1599-4d31-aca4-a971404c631c` | failed | — | 5 | 0 |
| atomic-save-failure | `7fc6b62d-7fd6-4f48-9e31-52b2ef06ae36` | succeeded | 12 通过；整批保存失败 | 5 | 0 |

检索、每角色请求 ID/哈希、真实测试计数分别保存在 JSON 的 `retrieved_ids`、`actual_injections`、`test_progression`。发布前目标没有来源 A 的 project 注入；发布后只有经过发布的 global 可以跨仓库，公共载荷无仓库、路径、源任务、源版本、原始验证文本。目标自身 project 可以共存，不因此算跨仓库泄漏。所有原始样例哈希保持不变。

负例：不同语言合并 409；能力不符、candidate 和 legacy 不注入；共享 2 步预算 stopped 且不写源代码；复核角色异常 failed；第二条批次索引插入被 SQLite trigger 拒绝时保留修复成功但返回保存失败、空 ID、无批次/经验残留。重启后 13 个任务、全部角色事件、经验及来源图一致。

## 并发、过期、恢复与独立审查

| 风险 | 实际覆盖 |
| --- | --- |
| 批次失败/去重/并发 | `test_experience_batches.py`：先验证后事务、插入中断零残留、同任务同内容复用 ID、不同内容冲突；验收 API trigger 故障 |
| 合并并发 | `test_experience_v2.py`：并发同集合只写一条，重叠来源不能被消费两次；跨状态/能力/条件/规则和修复顺序冲突 |
| 发布并发 | `test_experience_promotion.py`：重复发布、不同修订只允许一个胜出、事务回滚、合并后再次发布和多次修订 |
| 过期提案与回滚 | `test_agent_proposals.py`、`test_orchestrator.py`：快照与源文件 SHA 不符拒绝，路径/只读边界，部分写入恢复及恢复失败明确终止 |
| 预算与角色失败 | `test_agent_budget.py`、`test_agent_roles.py`、`test_orchestrator.py`：共享决策/截止时限、独立客户端、无角色工具执行、复核拒绝/异常不应用 |
| 重启 | `test_collaboration_api.py`：进行中父任务/角色 stopped，终态记录保留；13 任务脚本及 3 任务浏览器实际重启查询 |
| 旧数据 | `test_models_v3.py`、旧经验/Agent/API 全组：旧单结果归一化、旧 global 未审阅、旧项目仓库隔离、无静默升级为发布 |
| 前端竞态 | `experienceV2.test.ts`、`experienceV3.test.ts`：过期列表/预览响应、发布冲突保留草稿、终态发生于旧轮询期间仍补读最终事件 |

最终独立审查覆盖契约、事务、检索/迁移、发布/来源、协作、持久化与页面。实现、自查和独立审查中发现并修复：

- 同一来源内部的两个相关问题被误判为跨来源冲突：先复现，再修复；相关 93 测试通过。
- 已合并 global 再发布后无法继续修订，以及共享节点掩盖来源环：两个失败测试转通过，相关 16 项通过。
- 时间线终态遇到正在进行的旧轮询时漏读最后事件：失败测试转通过，最终前端 87 项通过。
- 源代码编译/收集错误被当环境故障提前终止：Java/Python 的初始错误与错误提案四个真实测试先失败，再成功修复到 18 用例通过；环境错误仍拒绝。
- 提取器/失败角色终态事件遗漏可得用量：两个失败测试转通过，未知 token 仍为 null。编译/事件相关协作 13 项全部通过。

首次 V3 验收因上述合并问题失败，保存于 `acceptance-scripted-1/report.json`。修复后第二轮和最终轮均通过。旧 V2 初次因拷贝 V3 批次字段被保护机制拒绝；改为两次真实修复自然生成的完整记录合并，最终 4 个任务均通过，各 12 项测试，旧单经验响应保持兼容。没有放宽生产保存权限来迁就旧脚本。

## 真实浏览器验收

使用 `frontend/scripts/serve-v3-acceptance.py`、独立 `.cache/experience-v3/20261031/browser/experiences.db`、Vue 真实连接模式和本机 Edge/Playwright CLI。模型仍为明确标记的脚本替身，API/代码工具/SQLite/pytest 均实际运行。

- Python checkout multi：`58b9fce1-f52d-4951-b46a-1968f791e2f5`，18 测试、3 条经验，角色过程/多个链接可查看。
- 页面合并两条后编辑并显式发布 global：`0237cf8b-84d2-489a-bed7-64f75792fc1c`，来源图可追踪回原记录。
- Python booking multi：`3d139fe5-a7d6-44ae-a0a2-47c9acaab045`，12 测试、3 条经验；SQLite 中实际诊断/修复/复核请求审计均包含该 global ID。
- 默认 single：`8ca0d26c-3004-4230-8abd-7880a0247a51`，18 测试、3 条经验；刷新后链接可查询。
- 按 Python / project / `v3-python-booking` 筛选只显示目标仓库记录；控制台 0 错误。API 进程停止重启后逐项比对 3 个任务、全部角色事件、经验和来源图一致，并在浏览器重开目标结果。

本地证据：`browser-report.json`、`browser-*-snapshot.txt`/页面结果、`browser-network.txt`（均在本轮 `.cache` 目录）；截图在 `output/playwright/v3-published-light.png`、`v3-published-dark.png`、`v3-target-roles.png`。截图已实际打开检查，沿用原深浅色与布局。

## 真实模型与限制

`--live` 检查配置后报告 **not_run**（退出码 2），没有模型调用。缺少完整 `LLM_API_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 配置，真实模型修复能力、多条经验语义质量、跨仓库效果和 token/耗时对照均未验证。脚本模型 token 未报告，不记为零。配置后运行同一验收入口，首次至少执行四类来源/目标及合并后复用任务；失败原样记录，不自动无限重试。

当前限定同语言、可信本地仓库、单 API 实例；capabilities 是必要过滤条件而非语义证明，通用发布需人工核对。目标测试覆盖了不同默认值与边界，但有限样例不能保证任意业务正确。时限保持既有协作式模型/工具预算，非所有底层网络阶段的硬中断。重启只恢复查询并停止中断任务，不自动续跑。

没有训练/评估隔离的成对对照数据，因此没有效率提升结论。最终交付代码、验收/备份脚本、此报告及 [演示步骤](../demo.md)；分支和工作区保留给用户审阅，未自动提交或上传。

收尾：`git diff --check` 通过；原始设计说明 SHA-256 与 M0 副本一致；108 个变更路径中未发现凭据模式、数据库或运行产物。验收临时浏览器/API/Vue 服务已关闭，数据、日志和截图保留。
