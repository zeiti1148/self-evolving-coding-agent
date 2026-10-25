# Self-Evolving Coding Agent

三人中级实训项目：使用兼容 OpenAI 的模型服务修复 Java/Maven 项目，在真实测试通过后保存结构化经验，供相似任务参考。

## 当前进度

已实现同步 Agent 循环、受控工具、FastAPI 四个业务接口、SQLite 经验模块及 Vue 工作台。两个 Java 样例已用**测试专用脚本模型 + 真实 HTTP API、文件工具、Maven、SQLite**完成可重复验收。脚本模型只用于测试，不能证明真实大模型的修复能力。

截至 2026-10-25，真实模型验收尚未执行：本地 LLM 配置仍为占位值。填写配置后执行下方 `--live` 验收，不把脚本模型结果写成真实模型实验结果。本轮基于 `main@5a09f3b`，经验模块和前端此前已通过 PR #2–#5 合并。

## 目录与分工

| 目录 | 内容 | 建议维护者 |
| --- | --- | --- |
| `backend/agent/` | 模型接入、同步循环、经验提取与复用 | 成员一 |
| `backend/api/` | 后台调度、任务状态、HTTP 路由 | 成员一 |
| `backend/tools/` | 工作副本、文件工具、进程和 Maven | 成员二 |
| `backend/experience/`、`frontend/` | SQLite 与 Vue 工作台 | 成员三 |
| `backend/shared/` | 既有 Pydantic 公共契约 | 三方共同维护 |
| `benchmarks/java/`、`benchmarks/java-profile/` | 两个故意保留缺陷的 Maven 项目 | 团队 |
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

登记项目为 `java-null-greeting`（null 字符串）和 `java-null-profile`（对象及嵌套字段为 null）。第二个样例可通过验收脚本提交，或修改前端项目配置后使用页面。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
# 测试脚本模型，实际运行两个 Maven 项目
.\.venv\Scripts\python.exe scripts/acceptance.py
# 真实模型配置，独立执行两个样例
.\.venv\Scripts\python.exe scripts/acceptance.py --live
cd frontend
npm.cmd test
npm.cmd run build
npm.cmd run check:integration
```

闭环验收使用独立 SQLite 文件与工作目录。结果位于 `.cache/acceptance/<时间戳>/`，包含模型模式、任务结果、工具步骤和检索经验 ID。成功要求两次修复通过、经验持久化、第二次收到第一次的经验、运行中能读取步骤、原始样例未变化。脚本模型验收还要求观察到真实初始测试失败。

`check:integration` 是已有前端契约检查，不执行 Agent 或 Maven。测试脚本模型位于 `tests/scripted_llm.py`，生产模块不导入它。浏览器验收专用入口为 `tests.ui_server:app`（建议 8001 端口），不得用于真实模型演示。

基准项目原始测试预期失败，修复只发生在工作副本。不要把原始样例的非零退出码当作系统回归。

## 执行边界与限制

- 仅用于本机、团队可信 Java 项目。路径白名单和进程管理不是操作系统沙箱，不用于公网或不可信项目。
- 客户端只提交登记项目标识；文件工具拒绝目录穿越、绝对路径、Windows 设备路径、符号链接和目录联接。
- 验收测试、pom.xml 和 Maven 配置只读。最终测试使用 clean test 和新报告；零测试、跳过测试、构建故障或超时不算通过。
- 进程有超时与输出上限，截断标记为 metadata.truncated。Windows Job/POSIX 进程组清理子进程，Java/Maven 不继承模型密钥或自定义 JVM/Maven 参数。
- 默认 20 轮模型决策。独立最终验证不占预算；耗尽预算保持 stopped，即使测试通过也不保存成功经验。成功后经验提取至多另调用模型一次。
- 按 Java 标签与错误类型检索最多五条经验，内容与 ID 注入上下文。检索故障记录警告，保存失败不改变已验证的修复成功。
- 单 API 进程、内存任务，重启丢失状态；经验持久化。暂不实现任务恢复、取消、历史、Diff、向量检索或模型训练。
- 默认保留 runtime/workspaces/<task UUID>；同根目录 .records 保存登记信息。开发人员使用 cleanup_workspace 校验后清理，禁止扩大删除范围。
- 本轮 Starlette TestClient 对 HTTPX 发出弃用提示，当前测试通过；后续依赖升级需要复测。

## 文档

- [系统架构](docs/architecture.md)
- [公共接口](docs/interfaces.md)
- [开发协作](docs/development.md)
- [本轮验收记录](docs/reports/mvp-2026-10-25.md)

完整阶段报告和下一阶段目标另存于仓库同级的“项目阶段文档”，提供 Word 与 Markdown 两版。
