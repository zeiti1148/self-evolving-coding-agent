# Self-Evolving Coding Agent

三人中级实训项目：使用兼容 OpenAI 的模型服务修复 Java/Maven 和 Python/pytest 项目，通过独立测试后保存结构化经验，供同仓库或适用的通用任务参考。

## 第三阶段：复杂经验与多语言

本分支基于已合并 PR #6 的 `main@6527421`，实现五项下一阶段目标：多问题结构化经验、可追溯合并、单任务多异常修复、Python 测试执行器、按仓库管理经验。计划见 [第三阶段计划](docs/next-week-plan.md)，设计和使用见 [经验 V2 说明](docs/experience-v2.md)。

真实模式前端会读取服务器登记项目，直接选择 **Java 配送请求多异常修复** 或 **Python 配送请求多异常修复**，点击“填入修复示例”提交。每个案例有 4 个业务模块、3 类组合缺陷、12 项验收测试。经验页可以按语言、仓库和范围筛选，查看问题明细、修复顺序和测试证据，选择记录预览合并后再确认。

```powershell
# 脚本模型驱动真实 Maven、pytest、SQLite，含部分修复失败和合并后复用
.\.venv\Scripts\python.exe scripts/acceptance_v2.py
# 使用本地 .env 配置的真实模型验收 Java 与 Python
.\.venv\Scripts\python.exe scripts/acceptance_v2.py --live
# 登记可信本地仓库；在 .env 设置 PROJECT_REGISTRY=runtime/projects.json 后重启后端
.\.venv\Scripts\python.exe scripts/register_project.py --id my-python-app --path D:/projects/my-python-app --language python --repository team/my-python-app --label 我的Python项目
```

Python 项目需有 `pyproject.toml`，解释器中已安装 pytest 及项目依赖；必要时通过 `PYTHON_EXECUTABLE` 指定项目解释器。默认使用后端当前解释器。工作副本不复制 `.env*`、Git 元数据和常见私钥文件。项目的测试、依赖和构建配置保持只读。

## 已合并的第二阶段基线

已实现同步 Agent 循环、受控工具、FastAPI 四个业务接口、SQLite 经验模块及 Vue 工作台。两个 Java 样例已通过脚本模型回归与真实模型 `deepseek-flash` 验收，均运行真实文件工具、Maven 和 SQLite。

2026-10-25 的真实验收中，Greeting 通过 2 项测试，Profile 通过 3 项测试；第二任务的成功 HTTP 请求包含第一任务保存的经验。该阶段交付已由 PR #6 合并到 `main@6527421`；经验模块和前端此前已通过 PR #2–#5 合并。历史 24 项对照结果保留原统计。

经验检索归一化和模型连接路径已修复。连接故障复现为环境代理路径上的 TLS 握手提前关闭；默认模型调用改为显式直连，保留可选环境代理及证书验证。修复后完整 24 项对照：关闭组 12/12、开启组 12/12 成功，经验组 12/12 实际收到参考。双方成功配对的已报告 token 相对变化 +7.14%；不将网络稳定性改善误称为经验效果。后端 239 项、前端 58 项及构建/集成检查通过。当前验收与定位证据详见 [故障定位](docs/reports/connection-diagnosis-2026-10-25.md) 和 [实验报告](docs/reports/live-evaluation-2026-10-25.md)。

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
| GET | `/api/tasks/{task_id}` | 状态与终态结果 |
| GET | `/api/tasks/{task_id}/steps` | 运行中读取步骤 |
| GET | `/api/experiences?limit=20&offset=0` | 经验分页 |

登记项目为 `java-null-greeting`、`java-null-profile`、`java-null-list`、`java-null-map`、`java-range-end` 和 `java-parse-count`。案例描述、错误类型与预期测试数量见 `benchmarks/catalog.json`。其他样例可通过脚本/API 提交，或修改前端项目配置后使用页面。

## 验证

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

- 仅用于本机、团队可信 Java 项目。路径白名单和进程管理不是操作系统沙箱，不用于公网或不可信项目。
- 客户端只提交登记项目标识；文件工具拒绝目录穿越、绝对路径、Windows 设备路径、符号链接和目录联接。
- 验收测试、pom.xml 和 Maven 配置只读。最终测试使用 clean test 和新报告；零测试、跳过测试、构建故障或超时不算通过。
- 进程有超时与输出上限，截断标记为 metadata.truncated。Windows Job/POSIX 进程组清理子进程，Java/Maven 不继承模型密钥或自定义 JVM/Maven 参数。
- 默认 20 轮模型决策。独立最终验证不占预算；耗尽预算保持 stopped，即使测试通过也不保存成功经验。成功后经验提取至多另调用模型一次。
- `TASK_TIMEOUT_SECONDS` 默认 600 秒，协作式截止时间覆盖后续模型请求和工具执行；网络读等待还受 `LLM_TIMEOUT_SECONDS` 限制。连接建立失败最多尝试三次，读取超时不自动重发付费请求。
- 上述任务预算不是操作系统级硬时限。同步 HTTPX 的读取超时衡量空闲间隔，持续缓慢到达的响应头、DNS 等底层操作仍可能延后退出；CI 另设 job/step 硬时限。本机原型暂未加入独立请求进程或自定义网络传输层。
- 按 Java 标签与错误类型检索最多五条经验，内容与 ID 注入上下文。检索故障记录警告，保存失败不改变已验证的修复成功。
- `USE_EXPERIENCE=false` 可关闭检索和注入；成功后的保存行为保持一致，便于公平对照。正式实验使用独立训练经验的冻结快照，不从评估结果继续学习。
- 单 API 进程、内存任务，重启丢失状态；经验持久化。暂不实现任务恢复、取消、历史、Diff、向量检索或模型训练。
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
