# Self-Evolving Coding Agent

三人中级实训项目：使用兼容 OpenAI 的模型服务修复 Java/Maven 和 Python/pytest 项目，通过独立测试后保存结构化经验，供同仓库或适用的通用任务参考。

## 经验演进 V3

新增自动经验整理：成功保存后自动合并同条件、同规则的已验证项目经验；规则重叠、证据不一致或修复顺序矛盾会进入持久化冲突队列，暂停相关经验的模型注入。经验页面支持整理历史记录、对比来源、选择可信来源或暂停两条记录，并保存处理依据。采用 Python 标准库拓扑排序、SQLite 索引与事务，无新增运行时依赖。详见 [自动合并与冲突处理](docs/experience-consolidation.md)。

当前实现：一次成功修复直接生成并原子保存多条经验、仓库隔离、可追溯合并、显式通用发布，以及诊断—修复提案—独立复核的运行时协作。保持 Python + FastAPI + SQLite + Vue 3，默认 single，可选 multi。最终成功始终由程序独立运行 Maven/pytest 验证。

Java/Python checkout 来源各有 18 项固定测试，booking 目标各有 12 项不同业务合同测试。页面支持角色过程、多条经验直达、仓库/状态筛选、合并预览、来源图和通用草稿编辑发布。

```powershell
# 脚本模型 + 真实 API、SQLite、代码工具、Maven/pytest
.\.venv\Scripts\python.exe scripts/acceptance_v3.py
# 单独执行真实模型验证；配置缺失时明确 not_run，绝不回退为脚本模型
.\.venv\Scripts\python.exe scripts/acceptance_v3.py --live
# 旧 V2 单经验链路兼容性回归
.\.venv\Scripts\python.exe scripts/acceptance_v2.py
```

[实现与升级说明](docs/experience-v3.md) · [2026-10-31 历史测试报告](docs/reports/experience-v3-2026-10-31.md) · [演示步骤](docs/demo.md)。旧 global 经验默认未审阅，不会自动跨仓库注入；先人工通用化、编辑和显式发布，目标仍需验证自身业务条件。2026-10-31 的验证环境没有真实模型配置，该历史脚本验收不代表真实模型效果；2026-11-03 的真实调用记录见下文，尚无效率提升的对照结论。

2026-11-01 阶段汇报：[阶段三正文与下一阶段计划](docs/reports/stage-3-2026-11-01.md) · [9 页 PPT](docs/presentations/coding-agent-v3-stage-report.pptx) · [逐页讲稿](docs/presentations/v3-stage-report-speech.md) · [本次回归与交付记录](docs/reports/delivery-2026-11-01.md)。上传前重跑后端 498 项、前端 87 项测试、生产构建与接口联调，结果均通过。

课堂汇报可使用 [通俗版 PPT](docs/presentations/coding-agent-v3-stage-report-simple.pptx) 和 [通俗版讲稿](docs/presentations/v3-stage-report-simple-speech.md)：用姓名、数量和列表问题说明功能，减少术语，保留相同测试数据与验证范围。

2026-11-03 本地提交准备：已同步 `main@f4efa6c`，补充模型单次调用预算、明确经验输出结构并记录脱敏提取诊断。后端全量 560 项通过、1 项因本机无法创建符号链接跳过，后续验证脚本的输出目录保护另有 5 项相关测试通过（含 2 项新增）；前端 111 项及构建、联调通过；V3 脚本工程验收 13 个场景符合预期。真实 `deepseek-flash` 多角色任务完成 Java 结算项目修复（18 项测试通过、3 条经验保存）；直接审核发布其中的文本输入规则后，新的 Java 预约任务在 120.267 秒内通过其 12 项原始测试并保存 3 条经验，两次诊断、修复和复核的实际请求均含已发布规则。重启后，两个任务的记录、经验、代码对比和测试源码均可读取。

这次单项真实复用验证由 `scripts/verify_live_reuse.py` 执行：默认只读预检，显式 `--live` 才备份原数据库与工作副本、发布助手审核的草稿并提交一个新目标任务；输出独立报告，不重跑来源任务，不作效率对照。原始完整 V3 真实验收仍未通过：来源经验的问题细分数量未达到该验收要求，另一次尝试遇到 HTTP 503 和响应读取超时；使用成功任务原证据单独复提炼后问题数量为 1/3/3，也不算完整验收通过。来源经验的适用条件和能力各异，生产规则应拒绝合并，不能为了满足验收的同批兼容经验对约束而改写条件。本轮原始报告保存在 `.cache/submission-checks/`，提交图片、说明、源码快照和补丁位于仓库同级 `项目阶段文档/比赛提交材料/`。

Python 项目需有 `pyproject.toml`，解释器中已安装 pytest 及项目依赖；可通过 `PYTHON_EXECUTABLE` 指定解释器。工作副本不复制 `.env*`、Git 元数据和常见私钥文件；测试、依赖和构建配置保持只读。

## 已合并的第二阶段基线

已实现同步 Agent 循环、受控工具、FastAPI 四个业务接口、SQLite 经验模块及 Vue 工作台。两个 Java 样例已通过脚本模型回归与真实模型 `deepseek-flash` 验收，均运行真实文件工具、Maven 和 SQLite。

2026-10-25 的真实验收中，Greeting 通过 2 项测试，Profile 通过 3 项测试；第二任务的成功 HTTP 请求包含第一任务保存的经验。该阶段交付已由 PR #6 合并到 `main@6527421`；经验模块和前端此前已通过 PR #2–#5 合并。历史 24 项对照结果保留原统计。

经验检索归一化和模型连接路径已修复。连接故障复现为环境代理路径上的 TLS 握手提前关闭；默认模型调用改为显式直连，保留可选环境代理及证书验证。修复后完整 24 项对照：关闭组 12/12、开启组 12/12 成功，经验组 12/12 实际收到参考。双方成功配对的已报告 token 相对变化 +7.14%；不将网络稳定性改善误称为经验效果。当时后端 239 项、前端 58 项及构建/集成检查通过；这些是历史数据，当前 V3 结果见单独报告。当前验收与定位证据详见 [故障定位](docs/reports/connection-diagnosis-2026-10-25.md) 和 [实验报告](docs/reports/live-evaluation-2026-10-25.md)。

## 目录与分工

| 目录 | 内容 | 建议维护者 |
| --- | --- | --- |
| `backend/agent/` | 模型接入、同步循环、经验提取与复用 | 成员一 |
| `backend/api/` | 后台调度、任务状态、HTTP 路由 | 成员一 |
| `backend/tools/` | 工作副本、文件工具、进程和 Maven | 成员二 |
| `backend/experience/`、`frontend/` | SQLite 与 Vue 工作台 | 成员三 |
| `backend/shared/` | 既有 Pydantic 公共契约 | 三方共同维护 |
| `benchmarks/catalog.json`、`benchmarks/training/` | 六个评估案例与三个独立训练案例，均保留原始缺陷 | 团队 |
| `scripts/`、`tests/` | 环境准备、闭环验收、测试专用模型 | 团队 |

## 本地准备

需要 Python 3.11+、Node.js 20.19+（20.x）或 22.12+、JDK 17+。本轮使用 Windows、Python 3.13.7、Node.js 24.10.0、JDK 21.0.8、Maven 3.9.11；Java 编译目标为 17。

在仓库根目录的 PowerShell 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/setup_maven.py
Copy-Item .env.example .env
cd frontend
npm.cmd ci
Copy-Item .env.example .env.local
```

已有配置文件时保留并编辑，避免覆盖。Maven 从官方仓库下载并校验 SHA-512，放入 Git 忽略的 `.cache/maven/`；也可设置 `MAVEN_HOME` 使用已有安装。工具通过 Java 启动 Maven，不拼接批处理或 Shell 命令。Linux/macOS 使用 `.venv/bin/python`。

根目录 `.env` 填写 `LLM_API_BASE_URL`（通常以 /v1 结尾）、`LLM_API_KEY`、`LLM_MODEL`。模型必须支持 Chat Completions function tool calls。配置缺失或服务失败会显示真实错误，不回退为模拟成功。密钥不能放入任何 `VITE_` 变量。

地址、密钥、模型现在从同一个配置文件读取，默认使用本仓库 `.env`，不会用继承的 `LLM_*` 值覆盖其中某一项，也不会从环境补齐缺项。需要另一个文件时，在启动环境设置 `LLM_CONFIG_FILE`；仅使用部署环境变量时显式设置 `LLM_CONFIG_SOURCE=environment`。文件里的模型配置不作 `${...}` 插值。每次模型请求记录脱敏来源、API 域名、模型及密钥指纹，不记录密钥内容。两个项目若填入同一把密钥，仍然共享同一账户额度；配置隔离不等于账户隔离。详见 [本次 API 来源核查](docs/reports/knowledge-remediation-2026-11-14/api-configuration-audit.md)。

模型连接默认 `LLM_TRUST_ENV=false`，直接连接服务，避免后台继承终端的全局代理。需要使用环境代理或自定义 CA 时显式设为 `true`，此时 HTTPX 会读取 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 和 `SSL_CERT_*`。两种模式都保留 HTTPS 证书验证。连接审计只记录阶段、异常类型和机器错误码，不记录密钥、异常原文或请求正文；连接重试仍最多三次，读取超时不自动重试。

将 `frontend/.env.local` 设置为：

```dotenv
VITE_USE_MOCK=false
VITE_API_BASE_URL=
API_PROXY_TARGET=http://127.0.0.1:8000
VITE_SOURCE_PROJECT=java-null-greeting
VITE_SOURCE_PROJECT_LABEL=Java 空指针修复样例
```

## 启动真实应用

Windows 可运行 `./scripts/start_local.ps1`，以后台进程启动前后端，固定读取本仓库 `.env`；已有端口不会被强制占用。访问 http://127.0.0.1:5173/#/ ，在“新建任务”上传自己的项目 ZIP、填写问题并提交修复。

当前支持 **Python/pytest、Java/Maven、Go/go test、C++/CMake+CTest**。新建任务默认提供上传入口，已登记样例保留为备选。上传支持 GitHub 的单层包装目录和手动指定子目录；项目、测试设置与任务记录持久保存。Python 可以选本机解释器、测试范围和插件；声明精确依赖版本的包会匹配本机环境。编译器和项目依赖仍须安装，上传副本不等于操作系统沙箱，请仅运行自己信任的项目。

任务页展示执行过程、测试前后对比和代码差异；成功修复并保存经验后，后台自动与已有经验比较，同仓库合并等价规则，不同项目归纳并复核共同规则。在“经验记录”直接查看合并前后对照、原文证据、业务约束，以及冲突处理结果和撤销历史。比较失败可以单独重试，修复不重跑。详见 [上传项目与运行环境](docs/uploaded-projects.md)。

C++ 可选工具安装：`.\.venv\Scripts\python.exe -m pip install -r requirements-native.txt`，另需本机 C/C++ 编译器；Go 需要 Go 工具链。

仓库根目录终端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

另开终端：

```powershell
cd frontend
npm.cmd run dev
```

访问 Vite 输出的地址，填写示例并提交。运行过程中可查看已完成步骤；终态分别展示修复、最终测试和经验保存事实。API 文档为 http://127.0.0.1:8000/docs。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/tasks` | 提交登记项目的任务，返回 202 |
| GET | `/api/tasks?limit=20&offset=0` | 分页查询持久化任务记录 |
| GET | `/api/tasks/{task_id}` | 状态与终态结果 |
| GET | `/api/tasks/{task_id}/steps` | 运行中读取步骤 |
| GET | `/api/experiences?limit=20&offset=0` | 经验分页 |

内置登记还包括 V3 checkout/booking 和 V2 fulfillment 两种语言样例，以及 `java-null-greeting`、`java-null-profile`、`java-null-list`、`java-null-map`、`java-range-end` 和 `java-parse-count`。案例描述、错误类型与预期测试数量见 `benchmarks/catalog.json`。页面读取 `/api/projects` 目录，可直接选择登记项目。

## 验证

自动经验整理现采用后台持久化作业、规范规则与扁平证据关联，支持规则级冲突决定及撤销。本地 E5/NLI 为可选依赖，安装和校准步骤见 [自动整理说明](docs/experience-consolidation.md)；问题、官方方案和实验结果集中记录在 [修复文档](docs/experience-consolidation-remediation.md)。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
# 测试脚本模型，实际运行两个 Maven 项目
.\.venv\Scripts\python.exe scripts/acceptance.py
# 真实模型配置，独立执行两个样例
.\.venv\Scripts\python.exe scripts/acceptance.py --live
# 独立训练3任务 + 试运行4任务 + 正式对照24任务；会调用真实模型
.\.venv\Scripts\python.exe scripts/experiment.py --live --stage all
# 重新生成训练经验并仅跑正式24项对照
.\.venv\Scripts\python.exe scripts/experiment.py --live --stage formal
# 归一化后的定向验证：3训练 + 2目标任务，不代替完整对照
.\.venv\Scripts\python.exe scripts/experiment.py --live --stage retrieval-check
cd frontend
npm.cmd test
npm.cmd run build
npm.cmd run check:integration
```

闭环验收使用独立 SQLite 文件与工作目录。结果位于 `.cache/acceptance/<时间戳>/`，包含模型模式、任务结果、工具步骤和检索经验 ID。两种模式均先用独立副本复现原始失败，要求最终测试数量一致、两次修复通过、经验持久化、第二次收到第一次的经验、运行中能读取步骤、原始样例未变化。真实模型报告另含实际请求的经验 ID/内容哈希、返回模型、响应 ID 与提供商 token 用量；不记录密钥。

`check:integration` 是已有前端契约检查，不执行 Agent 或 Maven。测试脚本模型位于 `tests/scripted_llm.py`，生产模块不导入它。浏览器验收专用入口为 `tests.ui_server:app`（建议 8001 端口），不得用于真实模型演示。

`experiment.py` 不带 `--live` 时只打印执行规模。每次执行新建 `.cache/experiments/<时间戳及随机标识>/`，逐任务更新 JSON 记录，失败也纳入统计；不恢复旧任务、不自动重跑整项任务。正式两轮反转条件顺序。经验由三个独立训练项目生成后冻结，六个评估项目及试运行结果不会反馈到该库。查看 `comparison_valid`、提供商用量覆盖率与失败原因后再解读汇总，不能把注入成功直接视为效果提升。

基准项目原始测试预期失败，修复只发生在工作副本。不要把原始样例的非零退出码当作系统回归。

## 执行边界与限制

- 仅用于本机、团队可信 Java/Python 项目。路径白名单和进程管理不是操作系统沙箱，不用于公网或不可信项目。
- 客户端只提交登记项目标识；文件工具拒绝目录穿越、绝对路径、Windows 设备路径、符号链接和目录联接。
- 验收测试、pom.xml 和 Maven 配置只读。最终测试使用 clean test 和新报告；零测试、跳过测试、构建故障或超时不算通过。
- 进程有超时与输出上限，截断标记为 metadata.truncated。Windows Job/POSIX 进程组清理子进程，Java/Maven 不继承模型密钥或自定义 JVM/Maven 参数。
- 默认 20 轮模型决策。独立最终验证不占预算；耗尽预算保持 stopped，即使测试通过也不保存成功经验。成功后经验提取至多另调用模型一次。
- `TASK_TIMEOUT_SECONDS` 默认 600 秒，协作式截止时间覆盖后续模型请求和工具执行。`LLM_TIMEOUT_SECONDS` 默认 60 秒，同时限制网络空闲等待和每次模型调用的固定预算（包含连接重试，且不晚于任务截止时间）；收到响应头、每个原始响应块、解析完成和返回前检查，持续空白保活不会重置调用预算。调用超时返回 `LLM_ERROR`，任务总时限耗尽返回 `AGENT_STOPPED`。连接建立失败最多尝试三次，读取超时或调用预算耗尽不自动重发付费请求。
- 上述预算不是操作系统级硬时限。同步 HTTPX 的读取超时衡量空闲间隔，慢响应头、DNS 等底层阻塞仍可能延后执行截止检查，响应块后停止发送还可能再等待一次网络读取超时；迟到的完整响应不会被接受为成功。CI 另设 job/step 硬时限，本机原型暂未加入独立请求进程或自定义网络传输层。
- 按语言、仓库、发布状态、能力和错误类型检索最多五条有效经验；通用注入使用白名单，省略私有来源载荷。检索故障记录警告，保存失败不改变已验证的修复成功。
- `USE_EXPERIENCE=false` 可关闭检索和注入；成功后的保存行为保持一致，便于公平对照。正式实验使用独立训练经验的冻结快照，不从评估结果继续学习。
- 单 API 实例和进程；任务、步骤、结果、审计与经验保存到 SQLite。重启后已结束任务仍可查询，未完成任务标记为中断，不自动续跑。已支持任务绑定的修复前后代码对比；暂不实现断点恢复、取消、独立任务历史页面、向量检索或模型训练。
- 默认保留 runtime/workspaces/<task UUID>；同根目录 .records 保存登记信息。开发人员使用 cleanup_workspace 校验后清理，禁止扩大删除范围。
- 本轮 Starlette TestClient 对 HTTPX 发出弃用提示，当前测试通过；后续依赖升级需要复测。

## 文档

- [系统架构](docs/architecture.md)
- [公共接口](docs/interfaces.md)
- [开发协作](docs/development.md)
- [本轮验收记录](docs/reports/mvp-2026-10-25.md)
- [真实模型与对照实验](docs/reports/live-evaluation-2026-10-25.md)
- [双平台持续集成](docs/reports/ci-validation.md)
- [答辩演示操作说明](docs/demo.md)

完整阶段报告和下一阶段目标另存于仓库同级的“项目阶段文档”，提供 Word 与 Markdown 两版。
