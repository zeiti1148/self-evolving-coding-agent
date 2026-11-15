# 自动经验整理与规则冲突处理

经验保存和整理分开执行。成功保存立即得到稳定的原始 ID；后台完成规则检查后，符合目标条件的知识才进入模型引用。原始内容和测试证据不被自动改写，重复经验通过共享规范规则去重。

## 使用流程

1. 成功修复后，原始经验、规范规则和待处理作业在一个短事务中保存。后台持续处理，无须手动点击。
2. 在「经验记录」按语言及仓库筛选，可查看后台进度、重试失败作业，或对历史记录发起整理。队列优先显示失败、运行和等待项，当前页最多 50 项。
3. 展开经验详情查看每条规则状态、独立证据数量及分页来源。历史列表可见不代表当前模型必然引用。
4. 审阅项分为已识别冲突和关系尚不确定的建议。确认业务条件后，可以采纳一个来源、暂停两方、确认等价，或确认兼容。
5. 保存处理依据后，可在「已处理」中查看决定历史。撤销需要当前版本和新的依据；系统追加重新审阅事件，并重新检查相关规则及依赖。

409 表示版本已经变化。页面保留输入，刷新后须重新核对。相同已提交决定或撤销请求可以安全重试。旧版整条经验冲突仍支持原有两种决定和撤销；它们与新规则审阅一起分页展示。

## 实现与判断边界

- **不可变原始资料**：`experiences` 保留原记录。`knowledge_rules` 按语言、仓库、兼容域、适用条件、约束、能力、代码目标及规则正文生成稳定指纹。`knowledge_sources` 和 `knowledge_rule_evidence` 分别关联来源和验证记录。
- **证据去重**：同一 `(source_task_id, tool_call_id)` 只记一次。相同键出现不同测试事实时暂停关联规则，人工重试作业也不能绕过这个暂停。来源测试通过只证明来源项目，不证明自然语言等价或跨项目正确。
- **事务与恢复**：使用 SQLite transactional outbox、唯一键、租约和提交令牌。每页最多比较 40 个候选，保存游标；进程退出后租约过期可续做。模型推理在写事务外进行。失败按 1/5/30/120 秒退避，累计五次后等待显式重试。原始经验不因整理失败回滚。
- **候选完整性**：按精确兼容域及可能重叠的文件/符号范围分页检查，没有 60 条跳过规则。相同文件、同样异常名不足以判定冲突。文件路径保留大小写。Tree-sitter 校验 Java/Python 符号；解析失败、符号无法核对时退回较宽的文件范围，不丢掉经验。
- **语义分类**：精确规则共享一个规范节点；不同目标或版本分别保留。可选 E5 仅用于检索排序，不能授权合并；双向 mDeBERTa NLI 在校准允许时识别等价/矛盾。能力不符、条件无法确认、保护字面量不同或输入超过模型长度时保守处理。无法确认的关系进入 `unknown`。
- **不确定性**：未知关系不会把整条原始经验永久停用；同时满足某次查询条件的未知重叠规则不会一起注入。等价组必须全部两两确认，不通过 A≈B、B≈C 推断 A≈C。
- **明确依赖**：`RepairIssue.depends_on` 是同条经验内的零基索引；数组顺序只用于展示。环会被拒绝或暂停。V3 多问题记录缺少独立约束，按整组保守处理；V4 支持独立规则。来源被暂停时，依赖它的规则和已发布通用规则递归失效。
- **目标契约**：登记项目可设置 `experience_contract`，表示管理员确认兼容的业务版本；否则用 `source_revision` 隔离。不同树版本默认不自动复用项目规则。通用规则仍通过显式发布，不从相似度自动晋升。
- **模型引用**：任务描述、观察到的错误类型和最近失败输出组成查询。FTS5 trigram 与 E5 排名按 RRF 合并。引用携带完整规则条件、约束、必要前置规则和证据计数；正文最多 5 条/16000 字符，超限整条省略。完整证据与来源通过管理接口查看。发送前重查状态和审阅版本。

V4 的结构化约束仍依赖提取或审阅质量，不是形式化证明。旧资料的顶层根因和方案完整保留在约束中；对不能拆解的旧组合规则不冒险部分复用。通用 NLI 在代码、否定嵌套、业务隐含条件上的能力有限，目标原始测试及额外测试仍是必要验证。

## 本地模型

基础安装不要求 Torch；没有模型或校准时仍可精确去重、分页整理和人工审阅。可选安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-semantic.txt
.\.venv\Scripts\python.exe scripts/prepare_knowledge_models.py
.\.venv\Scripts\python.exe scripts/knowledge_semantic_experiment.py
```

下载只在显式准备命令发生，运行时 `local_files_only=True`，不发送经验到语义服务。E5 和 NLI revision 固定在准备脚本中，实际清单及校准保存在 `.cache/knowledge-models`。模型清单或校准改变会重新排队已检查规则。`KNOWLEDGE_MODELS_ENABLED=false` 禁用本地模型；可用 `KNOWLEDGE_MODELS_DIR` 指定目录。校准命令使用受控样本，仅作为可复现起点，生产应增加有人工标签的领域样本。

SQLite 默认保留原日志模式。本机 3.50.4 受官方 WAL-reset 问题影响；仅在修复版本且显式 `EXPERIENCE_SQLITE_WAL=true` 时启用 WAL。初始化和普通读取已分离，读取不会每次执行 DDL 或获取保留写锁。

## 接口与实验

公共模型见 `backend/shared/knowledge_models.py`，完整 HTTP 契约见 [interfaces.md](interfaces.md)。详细问题、方案、官方依据和验证记录持续写在 [修复文档](experience-consolidation-remediation.md)。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-experiments.txt
.\.venv\Scripts\python.exe scripts/knowledge_semantic_experiment.py
.\.venv\Scripts\python.exe scripts/knowledge_system_experiment.py --output .cache/system-run-2
# 只打印 108 项计划
.\.venv\Scripts\python.exe scripts/knowledge_live_experiment.py
# 离线核查本项目配置来源、域名、模型与脱敏指纹，不发送网络请求
.\.venv\Scripts\python.exe scripts/knowledge_live_experiment.py --inspect-api --llm-env-file .env
# 如需另开真实实验：填入上一步的 identity；文件内容变化会拒绝启动
# 新输出目录必须不存在。既有 108 次实验已完成，不需要为了展示而重跑。
.\.venv\Scripts\python.exe scripts/knowledge_live_experiment.py --live --llm-env-file .env --expect-api-identity <上一步的identity> --output .cache/live-run-2
```

容量实验的 100/1000/10000 条是互不重叠文件目标；密集重叠单独测试 61 条。任意密集同域仍可能需要平方级比较，不能把稀疏容量结果当成这种情况的性能承诺。大型跨进程服务可继续迁移 outbox 到成熟任务队列；本轮不引入额外部署服务。
