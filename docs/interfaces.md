# 模块接口设计

## 1. 公共约定

- 公共数据模型统一使用 Pydantic v2，代码定义位于 `backend/shared/models.py`。
- 公共错误码定义位于 `backend/shared/errors.py`。跨模块传递稳定错误码，不依赖具体实现的异常类型。
- Agent 核心执行循环和工具接口均保持同步；API 层负责后台调度，不把异步框架侵入 Agent。
- Agent 只能通过 `backend/tools/` 操作任务工作目录中的业务文件，不能直接调用 `open`、`subprocess` 等底层能力。
- 时间字段在 Python 中使用带时区的 `datetime`，JSON 中序列化为 ISO 8601 字符串。
- 标识符在模型中使用经过 UUID 校验的字符串，便于 JSON 和日志直接使用。
- 模型禁止未知字段，描述和摘要等字符串自动去除首尾空白；`ToolResult.output` 完整保留原始字符串，工具名称也保留原文并拒绝空白名称。路径和输出大小仍必须由实际工具做运行时限制。

## 2. 公共枚举与错误码

### 2.1 工具名称

`ToolName` 保留为已知工具枚举，并导出 `KNOWN_TOOL_NAMES: frozenset[str]`（由枚举值生成）作为工具调度白名单。它不再限制 `ToolCall.name` 和 `ToolResult.tool_name` 的可表示范围。

```python
class ToolName(str, Enum):
    LIST_FILES = "list_files"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    RUN_COMMAND = "run_command"
    RUN_TESTS = "run_tests"
```

### 2.2 任务状态

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"
```

允许的典型状态变化为：

```text
pending -> running -> succeeded
                   -> failed
                   -> stopped
pending ----------------> stopped
```

`AgentResult` 只能使用 `succeeded`、`failed` 或 `stopped`；`pending` 和 `running` 只用于 API 任务状态。

### 2.3 统一错误码

工具、工作目录、经验和 Agent 使用 `ErrorCode`：

- `INVALID_TOOL_CALL`、`INVALID_TASK`、`INVALID_WORKSPACE`
- `PATH_OUTSIDE_WORKSPACE`、`FILE_NOT_FOUND`、`FILE_IO_ERROR`
- `COMMAND_NOT_ALLOWED`、`COMMAND_TIMEOUT`、`COMMAND_FAILED`
- `OUTPUT_LIMIT_EXCEEDED`、`TEST_FAILED`
- `WORKSPACE_CREATE_FAILED`、`WORKSPACE_CLEANUP_FAILED`
- `EXPERIENCE_VALIDATION_ERROR`、`EXPERIENCE_STORAGE_ERROR`
- `LLM_ERROR`、`AGENT_STOPPED`
- `INTERNAL_TOOL_ERROR`、`INTERNAL_ERROR`

可预期的工具失败转换为 `ToolResult`。无法继续的服务错误可抛出携带 `ErrorCode`、安全消息和可选详情的 `ApplicationError`，由任务/API 层统一转换，不直接向前端暴露堆栈。

## 3. 公共数据模型

下列字段以 `backend/shared/models.py` 为准。

以下代码块用于展示字段；所有列表和字典默认值在实际 Pydantic 模型中均使用 `default_factory`，不会共享可变对象。

### 3.1 `ToolCall`

```python
class ToolCall:
    id: str                    # UUID 字符串
    name: str                  # 非空且非纯空白，保留原始名称
    arguments: dict[str, Any] = {}
```

未知工具、缺少参数或参数类型错误时不得执行，统一返回 `INVALID_TOOL_CALL`。

如果 LLM 服务返回的工具调用 ID 不符合内部 UUID 格式，由 Agent 维护模型原始调用 ID 与内部 UUID 的映射。内部 `ToolCall.id`、`ToolResult.call_id` 继续使用 UUID；向 LLM 回传工具结果时必须使用对应的原始调用 ID，保证多轮协议关联正确。本轮仅约定映射责任，不实现映射逻辑。

### 3.2 `ToolResult`

```python
class ToolResult:
    call_id: str
    tool_name: str              # 与对应调用的原始名称按值相等
    success: bool
    output: str = ""
    error_code: ErrorCode | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = {}
```

- 成功结果不得包含错误字段。
- 失败结果必须同时包含 `error_code` 和 `error_message`。
- `metadata` 可包含 `exit_code`、`duration_ms`、`truncated`、测试数量等机器可读信息。
- 输出被截断时必须记录 `metadata["truncated"] = True`。
- `output` 不裁剪首行缩进、末尾换行或纯空白内容，多行代码原样保留；原有输出长度限制继续生效。
- 工具名称允许未知字符串，但不授予执行权限。`execute_tool` 必须按 `KNOWN_TOOL_NAMES` 精确匹配；未知名称返回 `INVALID_TOOL_CALL`，结果保留原始名称，不能动态解析为任意函数或 Shell 命令。

### 3.3 `ExperienceCreate` 与 `Experience`

```python
class ExperienceCreate:
    task_summary: str
    error_type: str | None = None
    root_cause: str
    solution: str
    verification: str
    tags: list[str] = []
    source_task_id: str | None = None

class Experience(ExperienceCreate):
    id: str
    created_at: datetime
```

必填文本不能为空，标签去掉首尾空白、拒绝空值，并按大小写不敏感方式去重。数据库层负责生成经验 UUID 和带时区时间。不得保存 API Key、完整工作区副本、无关对话或模型内部推理。

V2 的语言、仓库、多问题及证据字段见 [经验 V2 契约](experience-v2-contract.md)。
`applicability` 与标签使用不同规则：去掉首尾空白后按精确文本去重、保持原顺序，
保留大小写和内部空白，例如 `role == "Admin"` 与 `role == "admin"` 必须同时保留。
字段形状、长度限制和默认值不变；此前被旧规则丢弃的条件无法从现有记录自动恢复。

### 3.4 `AgentRequest`

```python
class AgentRequest:
    task_id: str
    task_description: str
    workspace: Path            # 工具模块创建的绝对路径
    max_steps: int = 20        # 1..100
```

`workspace` 只能来自 `create_workspace`，不能采用模型或客户端自行构造的路径。

### 3.5 `AgentResult`

```python
class AgentResult:
    task_id: str
    status: TaskStatus
    summary: str
    steps: list[ToolCall] = []
    tool_results: list[ToolResult] = []
    repair_succeeded: bool
    tests_passed: bool | None = None
    changed_files: list[str] = []
    experience_saved: bool | None = None
    experience_id: str | None = None
    experience_save_error: str | None = None
    error_message: str | None = None
```

状态约束：

- `status="succeeded"` 必须满足 `repair_succeeded=True` 且 `tests_passed=True`。这是单向约束，不根据 `failed/stopped` 强制改写已经观察到的测试事实。
- `tests_passed=None` 表示没有完成最终测试验证（包括未执行、超时或基础设施错误），`True` 表示最终测试通过，`False` 表示最终测试已执行且未通过。工具超时等原因仍记录于执行轨迹。
- 任何任务状态下，`repair_succeeded=True` 都要求 `tests_passed=True`，并且轨迹中最近一次 `run_tests` 的对应 `ToolResult.success=True`；所有调用与结果继续保持一一对应。
- 最近一次测试后出现 `write_file` 或 `run_command` 时，公共模型保守地认为代码可能变化，拒绝用旧测试证明修复成功，必须重新验证。只读 `list_files/read_file` 不使测试证据失效。模型只能验证记录的一致性；实际测试执行和工作目录完整性由 Agent/工具实现保证。
- 达到最大模型决策步数后状态仍为 `stopped`，可以保留最终验证得到的 `tests_passed=True/False/None`；即使代码已验证修复，也不把终止原因改写为任务成功。
- `experience_saved=None` 表示未尝试经验阶段；用于 `failed/stopped` 任务，这些任务不提取或保存成功经验。
- `experience_saved=True` 时必须有真实的 `experience_id`，且不能有保存错误。
- `experience_saved=False` 表示成功任务的经验提取、校验或保存失败；必须记录 `experience_save_error`，且 `experience_id` 必须为 `None`。提取失败时不得调用数据库保存虚假经验。
- `succeeded` 任务必须报告经验阶段成功或失败；经验提取或保存失败不会改变 `succeeded`、`repair_succeeded` 或 `tests_passed`。
- `changed_files` 使用工作目录内相对路径。

### 3.6 任务 API 模型

```python
class TaskStep:
    sequence: int
    call: ToolCall
    result: ToolResult
    completed_at: datetime

class TaskCreateRequest:
    task_description: str
    source_project: str        # 服务端登记的项目标识，不是任意文件路径
    max_steps: int = 20

class TaskCreateResponse:
    task_id: str
    status: Literal[TaskStatus.PENDING]
    created_at: datetime

class TaskStatusResponse:
    task_id: str
    status: TaskStatus
    steps_completed: int = 0
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: AgentResult | None = None
    error_message: str | None = None

class TaskStepsResponse:
    task_id: str
    status: TaskStatus
    steps: list[TaskStep] = []
```

`TaskStep` 只表示已经完成的工具调用，`call.id`、`result.call_id` 和工具名称必须匹配。步骤序号从 1 开始、唯一且升序。

## 4. 工具系统接口

所有接口由 `backend/tools/` 实现。具体文件/命令函数不向 Agent 单独暴露；Agent 的统一入口是 `execute_tool`。

### 4.1 统一工具入口

```python
def execute_tool(workspace: Path, call: ToolCall) -> ToolResult:
    ...
```

职责：

1. 确认 `workspace` 是工具模块创建并仍受管理的任务目录。
2. 工具名按 `KNOWN_TOOL_NAMES` 精确匹配并校验参数；未知名称或不合法参数返回 `INVALID_TOOL_CALL`，保留 `call.name`，不执行任何文件、函数或命令操作。
3. 根据 `call.name` 分派到对应实现。
4. 捕获可预期错误并统一转换为与 `call.id` 对应的 `ToolResult`。
5. 对输出应用长度限制并记录截断信息。

`execute_tool` 不接受任意函数名，不允许 Agent 绕过该入口调用底层文件或进程能力。

### 4.2 工作目录管理

```python
def create_workspace(source_project: Path, task_id: str) -> Path:
    ...

def cleanup_workspace(workspace: Path) -> None:
    ...
```

约束：

- 服务端配置唯一的任务根目录；所有工作目录必须是该根目录的直接或受控子目录。
- `task_id` 必须是合法 UUID，目录名由工具模块根据该标识生成，不拼接未经校验的客户端输入。
- `source_project` 必须存在、是获准的项目目录，且不能位于目标工作目录内部。
- `create_workspace` 复制源项目形成隔离工作副本，Agent 只能修改副本，不允许修改原始测试项目。
- 创建前后均使用规范化真实路径检查目录边界；遍历和复制时检查符号链接。指向允许根目录之外的符号链接必须拒绝，后续每次文件和命令操作也必须重新检查，防止 TOCTOU 或符号链接替换造成逃逸。
- `cleanup_workspace` 只清理由工具模块登记、位于任务根目录之下且与任务匹配的工作目录。验证失败时拒绝删除。
- 第一版在任务成功、失败或停止后均默认保留工作目录。`cleanup_workspace` 只由开发人员或明确的管理操作手动调用，不在任务终态自动调用。
- 默认任务根目录为仓库内已忽略的 `runtime/workspaces/`；配置为其他路径时也不得把运行时工作目录纳入版本控制。
- 第一版不提供 TTL 自动清理；后续引入时不得改变上述路径边界与手动清理安全校验。
- 创建失败抛出 `WORKSPACE_CREATE_FAILED` 或更具体的路径错误；清理失败抛出 `WORKSPACE_CLEANUP_FAILED`。调用方负责记录清理问题，但不得扩大删除范围。

### 4.3 文件工具

```python
def list_files(
    workspace: Path,
    path: str = ".",
    recursive: bool = False,
    max_entries: int = 200,
) -> ToolResult: ...

def read_file(
    workspace: Path,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int = 50_000,
) -> ToolResult: ...

def write_file(
    workspace: Path,
    path: str,
    content: str,
    create_parent: bool = False,
) -> ToolResult: ...
```

- 所有路径均为工作目录相对路径，禁止绝对路径、`..` 逃逸和越界符号链接。
- `read_file` 只读取文本文件；行号从 1 开始，`end_line` 包含在结果内。
- `write_file` 以 UTF-8 原子覆盖单个文件；第一版没有删除文件工具。

### 4.4 命令与测试工具

```python
def run_command(
    workspace: Path,
    command: list[str],
    cwd: str = ".",
    timeout_seconds: int = 60,
) -> ToolResult: ...

def run_tests(
    workspace: Path,
    project_path: str = ".",
    timeout_seconds: int = 180,
) -> ToolResult: ...
```

- 不得接收或执行任意 Shell 字符串，不使用 `shell=True`，不解释管道、重定向、命令替换或连接符。
- 命令必须以参数数组传递，并同时通过可执行文件白名单和参数规则校验。
- `cwd` 和 `project_path` 必须解析到受管工作目录内部。
- 每个命令必须有硬超时、标准输出/错误输出长度限制和进程退出状态记录。
- 白名单外命令返回 `COMMAND_NOT_ALLOWED`，超时返回 `COMMAND_TIMEOUT`，非零退出返回 `COMMAND_FAILED`。
- `run_tests` 第一版只执行受控的 Maven 测试流程，不接受客户端或模型提供的任意测试命令。测试正常运行但存在失败/错误时返回 `TEST_FAILED`。

## 5. 经验管理接口

经验模块不依赖 Agent、工具或 FastAPI。数据库连接通过实现层依赖注入。

```python
def save_experience(data: ExperienceCreate) -> Experience: ...

def search_experiences(
    query: str,
    tags: list[str] | None = None,
    error_type: str | None = None,
    limit: int = 5,
) -> list[Experience]: ...

def list_experiences(
    limit: int = 20,
    offset: int = 0,
) -> list[Experience]: ...
```

- 第一版采用 SQLite 文本、标签和错误类型过滤，不承诺语义相似度。
- `list_experiences` 按 `created_at` 倒序分页。
- 校验问题使用 `EXPERIENCE_VALIDATION_ERROR`，数据库问题使用 `EXPERIENCE_STORAGE_ERROR`。
- 经验提取、输入校验或 `save_experience` 失败时 Agent 返回成功的代码/测试状态、`experience_saved=False` 和安全错误消息，不伪造 ID，也不将修复改判为失败。

## 6. Agent 接口与步骤回调

同步核心接口保留为：

```python
def run_agent(request: AgentRequest) -> AgentResult:
    ...
```

为支持执行中查询，具体实现可以接受一个可选、同步的步骤回调，但不得把核心循环改为异步：

```python
StepCallback = Callable[[TaskStep], None]

def run_agent(
    request: AgentRequest,
    *,
    on_step: StepCallback | None = None,
) -> AgentResult:
    ...
```

省略 `on_step` 时与基础签名完全相同。每次 `execute_tool` 返回后，Agent 先形成不可变语义的已完成 `TaskStep`，再调用回调。回调不得执行慢速业务逻辑；API 层只将步骤追加到线程安全的任务存储。回调失败应记录为内部任务错误，不能悄悄丢失步骤。

Agent 职责：检索经验、调用 LLM、生成 `ToolCall`、调用 `execute_tool`、记录结果、运行最终测试、形成 `AgentResult`，以及在成功后尝试保存经验。Agent 不负责创建/清理工作目录，也不直接操作业务文件。

### 6.1 多轮结束与最终验证

- 每轮模型响应及其工具调用、工具结果都加入下一轮上下文；工具失败作为结构化结果回传，Agent 可以在剩余预算内继续请求模型纠正。
- `max_steps` 统计模型决策轮数（一次请求得到一个响应算一轮）；同一响应中的多个工具调用按顺序执行并分别记录步骤。
- LLM 主动结束只表示请求结束，不代表任务成功。Agent 必须通过 `execute_tool(workspace, ToolCall(name="run_tests", ...))` 执行最终测试，才能确认修复成功。
- 最终测试必须记录独立 UUID 的 `ToolCall` 和对应 `ToolResult`，加入 `AgentResult.steps/tool_results`；同时形成 `TaskStep`，通过 `on_step` 提供给 API。系统发起的最终验证无需模型原始调用 ID。
- 最终测试不计入模型决策步数，但计入执行轨迹和 API 已完成步骤数；因此执行轨迹数量可以超过 `max_steps`。
- 达到最大步数后停止继续调用模型，可以执行一次最终验证记录测试事实，任务状态仍为 `stopped`，不提取或保存成功经验。
- 正常结束后，最终测试通过才能返回 `succeeded`；测试执行完成但未通过返回 `failed`。未完成验证时 `tests_passed=None`，不得宣称修复成功。
- 最终测试后如果代码再次被修改，之前的结果不再是最终成功证据。必须对修改后的代码重新测试；不能复用修改前的通过结果。
- 经验提取或保存位于成功验证之后，该阶段失败只更新经验状态和失败原因，不改变代码修复或测试结果。

## 7. HTTP API 契约

API 统一使用 JSON；第一版由前端轮询，不提供 SSE 或 WebSocket。

### 7.1 `POST /api/tasks`

- 请求：`TaskCreateRequest`。
- 行为：将 `source_project` 映射为服务端登记的只读项目目录，生成任务 UUID，以 `pending` 写入任务存储，然后后台执行“创建工作目录—调用同步 Agent”。任务终态默认保留工作目录，客户端不能提交任意服务端路径。
- 响应：`202 Accepted` 与 `TaskCreateResponse`。
- 请求校验失败返回 `422`；源项目不允许或不存在返回安全的 `400` 响应。
- 任务存储不可用返回 `503`，不能在任务尚未持久化时返回成功受理。

### 7.2 `GET /api/tasks/{task_id}`

- 响应：`TaskStatusResponse`，包含当前状态、已完成步骤数、时间戳以及终态结果。
- 任务运行时 `result=None`；结束后返回 `AgentResult`，或在调度阶段失败时返回安全的 `error_message`。
- UUID 格式错误返回 `422`，任务不存在返回 `404`。
- 数据库不可用、记录损坏或后台终态无法落库时返回安全的 `503`，不伪装成 `404` 或运行成功。

### 7.3 `GET /api/tasks/{task_id}/steps`

- 响应：`TaskStepsResponse`。
- 只返回已经完成并成功追加到存储的步骤；前端可轮询并按 `sequence` 去重显示。
- 该接口在任务处于 `running` 时也必须可用，不能等 `run_agent` 返回后才一次性生成步骤。

### 7.4 `GET /api/experiences`

- 查询参数：`limit`（默认 20）和 `offset`（默认 0）。
- 响应：`list[Experience]`，按创建时间倒序。
- 参数不合法返回 `422`，存储故障返回不泄漏内部信息的 `503`。

### 7.5 `GET /api/tasks`

- 新增历史分页接口，响应为 `list[TaskStatusResponse]`，不改变既有四个端点的返回结构。
- `limit` 默认 20，范围 1–100；`offset` 默认 0，范围 0–2^63-1。
- 按创建时间倒序、同时间按任务 ID 倒序，先排序再分页；没有记录时返回空数组。
- 参数错误返回 `422`；数据库故障或已知未落库终态返回安全的 `503`，不能显示虚假的空历史。

## 8. 任务状态与步骤存储

2026-10-28 的可靠性改进将 API 任务记录迁移到现有 `DATABASE_URL` 对应的 SQLite 文件，
使用独立的 `api_tasks` 和 `api_task_steps` 表，不重写经验表。记录包括创建请求、
当前 `TaskStatus`、时间戳、已完成 `TaskStep`、最终 `AgentResult`、安全错误消息和内部审计。
无参 `TaskStore()` 仍提供进程内临时存储；正式 API 使用 `TaskStore(settings.database_path)`。

- 创建任务时原子写入 `pending`。
- 后台执行单元开始时改为 `running` 并记录 `started_at`。
- Agent 的步骤回调在锁和 SQLite 事务保护下追加完整 `TaskStep` 并更新计数；读取接口返回独立快照，避免前端看到半写入步骤。重复调用 ID 和非连续序号被拒绝。
- Agent 返回后在同一事务写入终态、`finished_at`、结果及审计；失败则全部回滚。
- 最终调用顺序、调用参数和对应工具结果必须与已存步骤一致，不能仅凭相同调用 ID 替换输出或验证证据。
- 状态只能按第 2.2 节的方向变化；重复终态写入必须拒绝或保持幂等。
- 文件数据库按操作打开并关闭连接；线程之间使用锁保护任务变更。关闭服务先等待后台任务结束，既有实验脚本仍能在服务关闭后读取持久化审计。

启动时将遗留的 `pending/running` 任务原子标记为 `stopped`，保留已完成步骤和审计，
并明确提示服务重启中断、需要重新提交。已结束任务保持原有时间及结果。不会重放工具调用、
自动再次请求模型或宣称中断任务已经成功；旧内存实现丢失的历史无法补回。

限制：仍须单 API 实例、单进程部署；SQLite 持久化不等于多 worker 调度或断点续跑。
后台终态写入失败且无法记录失败状态时，当前实例对该任务和历史列表返回 `503`，
恢复存储并重启后按上述中断规则处理。

## 9. 模块调用关系

```text
frontend --HTTP polling--> backend/api --background scheduling-->
                                  |
                                  v
                           backend/agent ----------> LLM API
                              |       |
                              |       +-----------> backend/experience -> SQLite
                              v
                      backend/tools -> managed workspace / Maven

backend/api, backend/agent, backend/tools, backend/experience
                              |
                              v
                       backend/shared
```

- `api` 可以调用 Agent、工具工作目录管理和经验查询接口。
- `agent` 可以调用 `execute_tool` 和经验接口，但不直接操作文件或进程。
- `tools` 与 `experience` 相互独立，也不依赖 Agent 或 FastAPI。
- `shared` 不依赖任何业务模块。
- 前端只访问 HTTP API，不直接读取 SQLite 或工作目录。

## 10. 并行开发联调约定

- 成员一依赖 `execute_tool`、经验接口和 `backend/shared`，可用 stub 进行 Agent 测试。
- 成员二负责工作目录生命周期和所有工具分派，返回本文定义的 `ToolResult`。
- 成员三实现经验接口和轮询前端，可用固定 API 数据开发页面。
- API/Agent 联调必须验证运行中任务能够读到已完成步骤。
- 字段重命名、错误语义、状态迁移、同步/异步模型或工作目录归属变化都属于公共接口变更，合并前必须由团队确认并同步更新文档和公共模型测试。

## 11. MVP 实现配置与验收记录

本轮保持既有函数签名、模型字段和四个 HTTP 端点不变。以下为工具实现的具体运行约束：

- 登记项目见 `backend/config.py` 和 `benchmarks/catalog.json`，包含六个评估项目；独立训练项目仅由实验脚本登记。任务目录通过 UUID 与根目录外侧的 `.records` 所有权记录管理；任务状态和步骤按第 8 节持久化，中断任务不自动续跑。
- 文件工具采用相对 POSIX 路径，拒绝全部符号链接和 Windows reparse points，包括指向工作区内部的链接。基准测试、pom.xml、Maven 配置只读。
- 命令参数数组白名单：`java -version`、`javac -version`、`mvn -version`、`mvn compile`、`mvn test`；后两项可带固定 `-B`。不接收自由 `-D`、插件目标、重定向或 Shell 文本。
- 普通命令默认 60 秒、最大 180 秒；测试默认 180 秒、最大 300 秒。进程输出最多 50000 字符，超出设置 `truncated`。
- `run_tests` 固定运行 `clean test`，只分析新生成的 Surefire 报告；存在失败/错误为 `TEST_FAILED`，零测试、跳过测试、构建/报告故障为 `COMMAND_FAILED`，超时为 `COMMAND_TIMEOUT`。
- 在已有 `ToolResult.metadata` 扩展字段中记录测试数量、退出码、执行时间、实际修改文件、`final_verification` 和 `retrieved_experience_ids`。这些是诊断信息，不增加必填公共字段。
- 经验检索故障通过 metadata 记录并允许任务继续。只有成功最终验证后才发起一次无工具的经验提取请求；结构化校验或保存失败保持修复成功状态。
- `USE_EXPERIENCE=false` 禁用检索与注入。实验脚本另外将训练库冻结为只读 SQLite，正式结果只写入输出库，不影响后续检索。
- `Agent.last_run_audit` 与 `TaskStore` 内部审计记录决策轮数、工具次数、实际请求的经验 ID/内容哈希、返回模型与提供商用量；不记录请求正文或密钥，不新增 HTTP 端点或公共必填字段。
- 模型客户端清理失败不能覆盖已经形成的任务结果；内部审计记录 `client_cleanup_failed=true`，日志只记录任务 ID 和固定提示，不暴露异常原文。
- 模型连接建立最多三次尝试；读取超时、HTTP 错误和无效回复不重发。响应大小上限 2 MB，使用未压缩原始块检查任务剩余时间。任务预算耗尽归为 stopped，经验提取故障仍独立于已验证的修复事实。
- 任务时限为协作式预算，不承诺所有网络阶段严格按总秒数中断。HTTPX 空闲读取超时仍可能被持续慢响应头延后；硬取消、同步 DNS 边界及请求隔离留待下一阶段处理。
- 工作副本、数据库和验收报告属于运行时数据，不提交到版本控制。

## V3 增量接口

`TaskCreateRequest.execution_mode` 可为 `single`（默认）或 `multi`。仓库、语言和 capabilities 由服务器登记解析。`AgentResult.experience_ids` 为有序数组，旧 `experience_id` 对应第一条，读取旧单 ID 自动归一化。两者不一致、重复 ID 或未保存却带 ID 的结果被拒绝。

| 方法 | 路径 | 输入/输出 |
| --- | --- | --- |
| GET | `/api/projects` | 登记项目，含仓库、语言、版本与 capabilities |
| GET | `/api/tasks/{id}/agents?after_sequence=0&limit=100` | 连续、持久化角色事件数组，单任务游标 |
| GET | `/api/experiences` | 增加 language/repository_id/scope/reuse_status/source_task_id/active_only 筛选 |
| GET | `/api/experiences/{id}` | 包含旧记录及被替代记录的详情 |
| GET | `/api/experiences/{id}/provenance` | nodes、edges、truncated；最多 100 节点/深度 10 |
| POST | `/api/experiences/merge-preview` | `{experience_ids:[UUID,...]}`，返回 can_merge/conflicts/preview |
| POST | `/api/experiences/merge` | 同预览输入，提交时重新校验，重复来源集合幂等 |
| POST | `/api/experiences/promotion-preview` | `{source_id:UUID}`，返回草稿、最终注入预览、current_global_id |
| POST | `/api/experiences/publish-global` | `{source_id,draft,supersedes_id:null或当前globalID}`，返回新记录 |

参数/结构错误 422、未知来源 404、语义或版本冲突 409、存储故障 503。预览无写入。`active_only` 排除被合并或修订替代记录，复用状态仍应另用 `reuse_status` 过滤；管理查询结果不代表可注入集合。

Experience 支持 schema_version 1/2/3，新增 batch_id、reuse_status、derived_from、supersedes_id、required_capabilities。新成功提取为 V3 active project。通用发布草稿仅允许知识字段，不接受来源、仓库或伪造证据；真实证据从源记录绑定。普通单条保存不能绕过批次/合并/发布操作写入来源索引或 active global。

角色事件含 event_id/task_id/agent_id/role/phase/status/sequence/snapshot_revision/summary/created_at/usage；usage.reported_tokens 未报告为 null，不能当 0。角色无工具权限；PatchProposal 必须含提案/角色 UUID、快照版本、路径、before_sha256 和完整 UTF-8 替换内容。程序统一验证应用并独立测试。

所有模型入口使用 `ExperienceStore.retrieve_experiences` + `policy.to_model_reference`，不可直接注入管理 search/list 结果。检索审计与实际模型请求 ID/内容哈希分开记录；终态的测试事实另行判断。

## 任务测试源码链接

| 方法 | 路径 | 返回 |
| --- | --- | --- |
| GET | `/api/tasks/{id}/test-sources` | `{task_id, files:[{path,language,sha256}]}` |
| GET | `/api/tasks/{id}/test-source?path=相对路径` | `{task_id,path,language,sha256,content,size_bytes}` |

两个接口只读，不发起模型调用，不执行测试，也不生成工具步骤。文件从该任务保留的工作副本读取，归属必须与任务原始请求一致；不回退到当前项目目录或 GitHub 最新版本。`path` 使用正斜杠相对路径，查询参数应按 URL 编码。

只列出任务创建时记录的 Java 测试目录下的 `.java` 文件，以及 Python 测试目录或 `test_*.py`、`*_test.py`、`conftest.py`。不提供业务源码、配置、密钥、任务后新增文件或任意磁盘路径。读取前沿用工作副本的路径、链接和重解析点保护；内容 SHA-256 必须与任务基线一致。

最多列出 1000 个文件；单文件最多 200000 字节，须为 UTF-8 文本。`content` 保留缩进、原始换行和末尾空白。成功响应带 `Cache-Control: no-store`。任务、文件或工作副本不可用为 404；拒绝链接路径为 403；测试文件已改动为 409；超出大小限制为 413；编码不支持为 415。

Vue 直接访问地址为 `/#/tasks/{id}/tests?file=相对路径`。省略 `file` 时打开首个测试文件；复制链接时包含所选文件。模拟前端不生成虚构源码。链接依赖可访问的前后端、持久化任务和工作副本，本机 `127.0.0.1` 地址不会自动成为公开分享地址。

## 任务代码对比链接

| 方法 | 路径 | 返回 |
| --- | --- | --- |
| GET | `/api/tasks/{id}/code-changes` | task_id、status、mode、notice、files、omitted_files |
| GET | `/api/tasks/{id}/code-diff?path=相对路径` | 单文件前后原文、SHA-256、增删计数及带原行号/新行号的 hunks |

文件清单包含 path、change_type（modified/added/deleted）、前后 SHA-256、available 和 unavailable_reason。完整契约见 `backend/shared/code_diff.py`。前后原文保留缩进、换行与末尾空白；不存在的版本为 null，空文件为 `""`。差异按文字行生成；只有换行或末尾换行变化时 `format_only=true`，不伪造文字增删。

新任务创建副本时保存限量原文，结束时原子写入一次前后快照，存放在工具不可写入的 `.records` 下。`mode=snapshot` 始终返回该历史快照，后续修改原项目或工作副本不改变它。快照保存失败只影响对比可用性，不覆盖已有修复或测试结论。重启中断、缺少结束快照的新任务返回 409，不拿当前文件冒充最终版本。

旧任务为 `mode=recorded_writes`：原项目内容必须与任务创建时基线哈希相同；修复后内容必须有成功的 write_file 记录，并与保留副本相同。不能核对时显示 unavailable，不从最新源码或模型说明推测。旧任务的对比来源在页面明确标注。

仅支持 Java/Python 业务代码，测试、配置、凭据和其他路径不可通过该接口读取。单版本单文件上限 200000 字节，每侧快照总原文上限 2000000 字节；单任务最多展示 100 个改动文件，超出的数量通过 omitted_files 告知。在线差异限制每个版本 4000 行，超过返回 413。快照损坏/版本不符/任务未结束返回 409；任务或文件不存在返回 404。成功响应带 `Cache-Control: no-store`。

Vue 地址：`/#/tasks/{id}/changes?file=相对路径`，可选 `view=before` 或 `view=after` 指定原文版本。默认展示带三行上下文的增删对比，支持长行折行。两种查看页面共用任务与工作副本归属检查。同任务切换文件复用目录索引，正文仍重新读取；切换任务会清空索引并取消旧请求。
