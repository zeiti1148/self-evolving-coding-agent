# 经验 V2 实施契约

此文件用于当前开发集成，所有新增公共字段均须有兼容旧记录的默认值。

## 经验模型

ExperienceCreate 保留既有字段，增加：schema_version=2，language(java/python，默认 java)，scope(global/project，默认 global)，repository_id(nullable)，source_revision(nullable)，applicability(string[])，issues(RepairIssue[])，evidence(VerificationEvidence[])，merged_from(UUID[])。project scope 必须有 repository_id，global scope 不携带 repository_id。

RepairIssue 字段：error_type、root_cause、solution、files(string[])。issues 的数组顺序表示推荐修复顺序；相同错误类型可以有不同位置，但每项必须完整。VerificationEvidence 字段：source_task_id(UUID)、tool_call_id(UUID)、runner(maven/pytest)、tests(正整数)、failures=0、errors=0、skipped=0。真实证据由程序根据最终工具结果补充，不采信模型编造的计数和来源。

`applicability` 去掉每项首尾空白后按精确文本去重，保留大小写、内部空白及顺序。
它包含代码标识符或字符串条件，不能复用标签的大小写不敏感去重规则。

## 存储与合并

保留 save_experience、list_experiences、search_experiences 现有签名与旧数据行为。增加 get_experience(id)，retrieve_experiences(query='', *, language, repository_id, error_types, limit=5)，以及 preview_merge(experience_ids) 和 merge_experiences(experience_ids)。

preview_merge 返回 {can_merge:bool, conflicts:string[], source_ids:string[], preview:ExperienceCreate|null}。至少 2 个、最多 10 个不同 ID。合并只接受相同 language、scope 和 repository_id；相同问题位置/类型出现互斥或不同方案应标记冲突，不能悄悄任选。合并保留证据和来源、生成新记录；原记录保留。相同来源集合重复合并返回同一结果。源记录已被其他合并取代时拒绝再次重复使用。检索排除已被合并取代的源记录，优先同仓库经验，再选通用经验，按异常交集和关键词排序。

## API 与项目

- GET /api/projects 返回 [{id,label,language,repository_id,source_revision,task_example}]，所有项目均来自服务器登记。
- GET /api/experiences 保留数组响应和分页，增加可选 language、repository_id、scope 筛选。
- POST /api/experiences/merge-preview，POST /api/experiences/merge，body={experience_ids:string[]}；前者返回预览，后者返回 Experience，冲突返回 409。
- TaskCreateRequest 保持 source_project，不允许客户端传入任意磁盘路径；服务器向 AgentRequest 写入 language、repository_id 和 source_revision。

## 执行器

ToolService 的现有两个位置参数保持兼容，可增加 python_executable 关键字参数（默认 sys.executable）。run_tests 按受保护的登记项目标记/构建文件分发：Java 使用 Maven，Python 使用 pytest。结果保留 tests/failures/errors/skipped，增加 runner、error_types、test_failures，单次测试报告必须是新产生的结果。Agent 在最终测试失败且仍有决策预算时收到反馈继续修复；耗尽保持 stopped，工具或环境错误不冒充已修复。
