# 经验演进 V3

本版本在 Python/FastAPI/SQLite/Vue 3 和此前可靠性修复上增量扩展。默认执行模式仍是 `single`，`multi` 使用独立的诊断、修复提案、复核上下文，文件修改与最终测试由程序协调器执行。设计与实施清单见 `docs/superpowers/`，实际结果见 [2026-10-31 报告](reports/experience-v3-2026-10-31.md)。

## 一次修复，多条经验

程序只有在独立最终测试有正数用例、零失败/错误/跳过且 runner 匹配登记语言时才尝试提取。一次无工具模型请求返回 `experiences` 数组（1–5 条），旧单对象响应继续支持。每条包含独立用途、适用条件和相关问题；模型不能决定仓库、来源、发布状态或测试证据。这些字段由程序绑定到本次任务的真实最终测试。

`save_experiences` 先验证整个批次，再用 `BEGIN IMMEDIATE` 写批次、记录及顺序链接。以来源任务为唯一键、完整内容指纹判断重复：相同请求返回原顺序 ID，改内容重用同一任务会冲突。中途任何插入失败均回滚。修复成功和保存结果分开；保存失败不伪造经验 ID。

结果返回 `experience_ids`，`experience_id` 始终是第一条或 `null`，读取旧结果时补数组。多条经验可以引用同一个最终测试调用，这表示同一次验证，不能累加成多次独立测试。Java/Python checkout 样例各有六个相关缺陷、18 项固定测试；脚本模型直接输出文本、整数和集合选择三组知识，每组含两个相关问题，没有复制或拆分数据库记录。

## 仓库与发布

仓库身份由服务端登记的 `repository_id` 确定，不接受任务提交者覆盖。同一函数策略供 single、multi 的每个角色、后续检索和冻结实验库使用：

1. 相同语言、`reuse_status=active`，且目标登记能力覆盖 `required_capabilities`。
2. 项目经验必须属于当前仓库；通用经验必须已经显式发布。
3. 合并替代来源和旧发布版本停止注入，但保留管理查询与来源追踪。

旧有明确仓库的项目经验继续按仓库使用；旧 global 默认 `legacy_unreviewed`，新 global 默认 `candidate`，均不注入。旧 global 有真实结构化证据才可经人工预览、编辑、发布产生新版本；没有证据的旧记录保留查询，应重新执行修复获得真实来源。

通用化预览提供保守的可编辑草稿，不写数据库。审阅者需编辑适用条件、修复规则及能力要求，检查最终注入预览后点击发布。发布新建 global，保留源记录及 `derived_from`，不能将原 project 直接改成 global。修订通过原来源提交 `supersedes_id=current_global_id`；并发修改返回 409，界面保留当前草稿，要求用户重新预览。

公共注入白名单只包含编辑后的规则、条件、能力、问题和测试计数摘要，不含来源仓库、文件路径、任务 ID、原始任务描述、原始验证文本及来源链。路径/标识检查不能证明任意自然语言已经语义通用，发布仍需要人工审阅。能力是必要条件，业务语义仍由角色核对，并由目标原始测试验证。V3 仅实现同语言复用。

登记示例：

```powershell
.\.venv\Scripts\python.exe scripts/register_project.py --id booking --path D:/projects/booking --language python --repository team/booking --label 预约项目 --capability text-input --capability integer-input --capability indexed-selection
```

在 `.env` 设置 `PROJECT_REGISTRY=runtime/projects.json` 后重启。登记能力表示仓库实际支持的业务特征，不能仅为命中经验随意添加。

## 合并与来源

合并要求语言、范围、仓库、状态、能力、适用条件一致。不同来源在相同异常及重叠文件上给出不同规则时拒绝；同一来源内部已有的相关问题按原顺序保留。检查修复顺序冲突、重复问题和同一验证调用证据不一致。预览不写库；提交重新校验并在单事务内保存合并记录和来源索引，相同来源集合重复或并发合并只返回同一记录。使用已被其他合并消费的来源会冲突。

`get_provenance` 返回最多 100 个节点、深度 10 的图，明确 `truncated`；损坏、缺失或循环来源不是空结果。图包含合并、通用化和修订关系。来源记录不删除。

## 运行时协作

两个独立诊断调用在同一只读快照上并行，之后修复角色提交完整 `PatchProposal`，独立复核角色检查提案和目标合同。每个角色使用新模型客户端和新上下文，没有工具执行通道。模型的事实摘要可交接，不存储内部推理。

协调器在写入前检查整份快照版本、提案身份、所有源文件 SHA-256、路径、文件类型及只读边界，检查完所有文件再统一应用。过期提案重新进入受预算限制的修复；应用失败尝试恢复已经触及的文件，恢复失败明确终止。工作副本仍是可信本地目录，不提供针对外部并发写入者的文件系统事务。

复核拒绝、部分测试失败及可识别的源代码编译/语法错误会反馈给修复角色。运行环境、工具时限、空测试、跳过测试等不能冒充验证成功。两次诊断、每次提案和复核共用 `max_steps`；真实工具和角色请求共用任务截止时间。成功后的提取至多额外调用一次，仍受同一时限。保留既有模型连接重试和进程清理限制，任务时限是协作式预算，不能宣称所有底层网络阶段都被硬中断。

角色事件以任务+连续序号持久化，包含角色 ID、阶段、状态、快照、简短事实及可得的 token/耗时。未知 token 为 `null`。独立最终测试结果和文件步骤另存，不用复核者的“同意”代替测试。重启后完成记录可查，进行中任务及未结束角色标记 stopped；不会自动重放修改或继续消耗模型。

## 升级与回退

停止旧 API 实例后，先备份配置对应数据库（含已提交 WAL），验证备份，再用副本验证升级：

```powershell
.\.venv\Scripts\python.exe scripts/backup_database.py --output .cache/backups/pre-v3.db
# 如果数据库尚不存在，脚本明确失败且不创建空库冒充备份。
# 首次打开 V3 数据库会进行兼容初始化；正式 API 仍只启动单实例。
.\.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

备份目的地必须不存在，旁边生成 SHA-256 与完整性检查清单。旧 experience 主字段不重写；初始化补结构化详情及批次、合并、发布、事件表。升级前保留相匹配的旧代码和数据库备份。回退时停止服务，另行保全升级后的数据库及 WAL，再让旧代码指向升级前备份的**副本**；不要直接让旧代码读取已发布 V3 数据，也不要仅替换正在打开的 `.db` 文件。

本次 M0 配置的 `data/app.db` 不存在，因此报告记录“无运行库可备份”；所有迁移/备份测试和验收均使用独立临时库，没有创建伪备份。

## 可复现验收

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe scripts/acceptance_v3.py
.\.venv\Scripts\python.exe scripts/acceptance_v3.py --live
.\.venv\Scripts\python.exe scripts/acceptance_v2.py
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run check:integration
```

可用 `--output-dir` 指向一个尚不存在的目录。每次独立数据库、工作副本和 run_id，过程报告逐任务落盘。脚本响应仅替代模型，其他路径使用生产组件与实际 Maven/pytest。默认完整验收包含 13 个任务；live 至少两个来源、两个合并后来源复用、发布前后四个目标任务，受每任务预算限制，不自动无限重试。live 配置缺失返回退出码 2，报告 `not_run`，不回退脚本模型。

查看报告中 `retrieved_ids`、`actual_injections`（逐角色 ID/内容哈希）及 `test_progression`。检索命中不等于实际注入，注入不等于修复通过。脚本模型能证明流程与边界，不能证明真实模型能力或经验带来的效率提升。

页面演示入口与步骤见 [demo.md](demo.md)。浏览器 mock 仅为显式标记的合成数据；复杂多经验和模型协作的验收应使用脚本模型真实 API 服务或配置好的真实模型服务。
