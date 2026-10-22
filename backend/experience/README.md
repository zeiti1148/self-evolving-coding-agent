# SQLite 经验模块

本模块实现第一阶段的经验保存、关键词/标签/错误类型检索、列表分页。
复用 `backend.shared` 的公共模型和错误码，不依赖 Agent、工具、FastAPI 或前端。
不负责经验提取，也不判断 Java 修复是否成功；成功验证和保存时机由 Agent 控制。

## 接入方式

Agent/API 可直接导入契约中的三个同步函数，签名与 `docs/interfaces.md` 一致：

```python
from backend.experience import save_experience, search_experiences, list_experiences
from backend.shared import ExperienceCreate

# data 应由调用方根据真实修复和验证结果构造，而不是虚构成功记录。
def record_and_find(data: ExperienceCreate):
    saved = save_experience(data)
    recent = list_experiences(limit=20, offset=0)
    related = search_experiences(
        "空指针 trim", tags=["java"], error_type="NullPointerException", limit=5
    )
    return saved, recent, related
```

保存返回 `Experience`；列表与检索返回 `list[Experience]`，无结果返回 `[]`。
经验 ID 为新生成的 UUID，时间为 UTC 带时区 `datetime`。写入提交成功后才返回。
`source_task_id` 只记录来源，不依赖当前仍存在内存任务，也没有外键指向任务表。

需要注入数据库时，可使用实现层对象，组长可将其绑定方法注入 Agent：

```python
from pathlib import Path
from backend.experience import ExperienceStore

store = ExperienceStore(Path("data/experiences.db"))
# store.save_experience / store.search_experiences / store.list_experiences
# 参数与对应模块函数相同；测试传入临时目录中的数据库文件即可。
```

## 数据库配置和生命周期

- 模块函数每次调用读取 `DATABASE_URL`，默认 `sqlite:///./data/app.db`。
- 支持 `sqlite:///` 加普通文件路径；Windows 示例：`sqlite:///D:/agent-data/app.db`。
- 相对路径固定以仓库根目录为基准，不随启动终端所在目录变化。
- 配置来自进程环境；本模块不自行读取 `.env`，由 API/启动层加载。
- `ExperienceStore` 接收文件路径而非数据库 URL。
- 不支持 `:memory:`，因为每次操作独立连接会导致内存数据库丢失。
- 导入和构造对象均不创建文件；第一次操作创建目录、表和时间排序索引。
- 每次操作在当前线程创建连接，事务结束后关闭连接。连接不会跨线程共享。
- 写入失败回滚；数据库忙时最多等待 5 秒，再转换为存储错误。
- 重复初始化保留已有数据；目前没有升级旧表结构的迁移机制。
- 标签以 JSON 文本存储；SQL 注册局部 Python 匹配函数，不依赖 SQLite JSON 扩展。
- 数据库和解释器/虚拟环境已由仓库现有忽略规则排除，不要提交运行数据。

## 检索行为

| 条件 | 行为 |
| --- | --- |
| query | 按空白拆词，每个词至少命中一个可搜索字段 |
| 可搜索字段 | task_summary、error_type、root_cause、solution、verification |
| 匹配方式 | Unicode casefold 后的字面子串；不分词中文，不做语义匹配 |
| 空查询 | 不限制文本，可仅按标签或错误类型筛选 |
| tags | 完整标签匹配，去首尾空白、忽略大小写；多个标签要求全部包含 |
| error_type | 去首尾空白、忽略大小写后的完整值匹配 |
| 多类条件 | AND 组合，全部过滤完成后再 LIMIT |
| 顺序 | created_at DESC，再 id DESC；检索不按语义相关性排序 |
| list 分页 | 默认 limit=20、offset=0，越界返回空列表 |
| search 数量 | 默认 limit=5；当前契约不提供 offset |

`%`、`_`、引号和反斜杠等按普通文本处理，不解释为 SQL 通配符。
关键词不搜索标签；按标签查找时使用 `tags` 参数。
空标签列表等同于无标签条件；列表中的空白标签属于校验错误。
limit 必须是正整数，offset 必须是非负整数，且不超过 SQLite 有符号 64 位整数范围；
布尔值、浮点数和数字字符串不会被隐式转换。

第一版适合小型经验库，文本匹配可能扫描候选行；没有向量检索、相关度排名、更新、
删除、去重或重试幂等接口。对同一内容调用两次保存会产生两条不同 UUID 的记录。

## 错误处理

- 模块输入校验失败：`ApplicationError(EXPERIENCE_VALIDATION_ERROR, ...)`。
- 配置错误、SQLite/I/O 故障或读取到损坏数据：`EXPERIENCE_STORAGE_ERROR`。
- 对外消息不包含原始 SQL、数据库路径或底层异常内容。
- 保存要求 `ExperienceCreate` 实例，并在写入前重新验证其内容，防止标签列表被原地
  修改后绕过模型校验。调用方构造模型时发生的 `ValidationError` 应由调用方处理。
- 不将数据库故障伪装为“无结果”，不返回虚假 ID。
- Agent 应按现有 `AgentResult` 契约记录经验保存失败，不覆盖已验证成功的修复结果。

## 本地验证

在仓库根目录的 PowerShell 中运行（无需激活虚拟环境）：

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/test_experience_store.py backend/tests/test_experience_search.py -q
.venv/Scripts/python.exe -m pytest -q
```

本次 M0 使用 uv 将 Python 3.11 安装在已忽略的 `.cache/python` 下，再创建 `.venv`，
依赖来自原有 `requirements-dev.txt`，没有新增业务依赖。不要单独移动或删除该解释器
目录，否则需要重建虚拟环境。可用以下命令重新安装现有测试依赖：

```powershell
uv --cache-dir .cache/uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
```

测试使用临时文件数据库，覆盖持久化、参数验证、稳定分页、组合检索、字面匹配、
Unicode、损坏数据、真实 SQLite 触发器导致的回滚和多线程独立连接。

本次验证结果：经验模块 67 passed；全量 124 passed、1 failed，唯一失败为下述
原有 Windows 路径测试。独立审查发现的非法 Unicode 查询和过深 JSON 异常问题已修复，
并加入回归测试。未执行真实 Agent、LLM 或 Maven 集成测试。

## 已报告的公共契约问题

### 与前端的可重复验收（2026-10-22）

在已经安装前端依赖的环境中，从仓库根目录运行：

```powershell
cd frontend
npm.cmd run check:integration
```

脚本读取实际前端场景，使用现有公共模型校验，并调用 `ExperienceStore` 在独立
临时数据库中验证保存、重新连接后读取、组合检索和分页。真实 SQLite 返回值按
`model_dump(mode="json")` 序列化后，通过本机验收 HTTP 服务与 Vite 代理交给
前端 HTTP 适配器检查。不会使用 `DATABASE_URL` 指向的业务数据库。

此验收不实现生产 FastAPI 路由，也不执行真实 Agent、LLM 或 Maven。
接口实现者应将 `Experience` 通过 Pydantic/FastAPI 标准 JSON 序列化，保留 UUID、
带时区时间、标签数组以及可空的 `error_type`、`source_task_id`。

### 仍需协调的事项

1. 文档称“标签去除空值”，公共模型实际拒绝空白标签；本模块保持公共模型行为。
2. 检索组合语义及配置注入细节未完全指定；本页记录实现选择，公共函数签名未变。
3. 原有 `test_normal_models_are_created_with_defaults` 使用
   `Path('/tmp/tasks/example')`，Windows 上不是绝对路径，导致基线 57 通过、1 失败。
   这是新增实现之前复现的问题；共享模型与共享测试保持未修改，需由维护者将测试
   样例改为跨平台绝对路径（如基于 tmp_path）后验证。
