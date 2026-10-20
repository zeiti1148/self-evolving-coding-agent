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

必填文本不能为空，标签去除空值并按大小写不敏感方式去重。数据库层负责生成经验 UUID 和带时区时间。不得保存 API Key、完整工作区副本、无关对话或模型内部推理。

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

### 7.2 `GET /api/tasks/{task_id}`

- 响应：`TaskStatusResponse`，包含当前状态、已完成步骤数、时间戳以及终态结果。
- 任务运行时 `result=None`；结束后返回 `AgentResult`，或在调度阶段失败时返回安全的 `error_message`。
- UUID 格式错误返回 `422`，任务不存在返回 `404`。

### 7.3 `GET /api/tasks/{task_id}/steps`

- 响应：`TaskStepsResponse`。
- 只返回已经完成并成功追加到存储的步骤；前端可轮询并按 `sequence` 去重显示。
- 该接口在任务处于 `running` 时也必须可用，不能等 `run_agent` 返回后才一次性生成步骤。

### 7.4 `GET /api/experiences`

- 查询参数：`limit`（默认 20）和 `offset`（默认 0）。
- 响应：`list[Experience]`，按创建时间倒序。
- 参数不合法返回 `422`，存储故障返回不泄漏内部信息的 `500`。

## 8. 任务状态与步骤存储

第一版允许 API 层使用单进程内存任务存储。每个任务记录至少包括：创建请求、当前 `TaskStatus`、时间戳、已完成 `TaskStep` 列表、最终 `AgentResult` 和安全错误消息。

- 创建任务时原子写入 `pending`。
- 后台执行单元开始时改为 `running` 并记录 `started_at`。
- Agent 的步骤回调在锁保护下追加完整 `TaskStep`；读取接口返回快照，避免前端看到半写入步骤。
- Agent 返回后原子写入终态、`finished_at` 和结果。
- 状态只能按第 2.2 节的方向变化；重复终态写入必须拒绝或保持幂等。
- 如使用线程执行同步 Agent，内存存储和步骤列表必须使用锁或等价同步机制保护。

限制：服务重启后任务和步骤会丢失；多进程、多 worker 或多实例之间不能共享状态；运行中任务无法恢复。因此第一版部署必须使用单 API 进程。需要可靠恢复或横向扩展时，再将相同存储契约迁移到数据库/队列。

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
