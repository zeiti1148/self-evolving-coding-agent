# Self-Evolving Coding Agent

## 项目简介

Self-Evolving Coding Agent 是软件工程专业中级实训项目，由三名成员协作开发。系统面向 Java 代码修复任务：Agent 使用大语言模型分析问题，通过受控工具查看和修改代码、运行 Maven 测试，并在修复成功后保存结构化经验，供后续相似任务参考。Web 页面用于提交任务和展示执行过程与结果。

第一版目标是实现最小可运行闭环，不追求复杂的自演化算法。当前 README 描述的是规划和公共工程骨架，不代表业务功能已经实现。

## 技术栈

- 后端：Python 3.11+、FastAPI
- 公共模型：Pydantic v2
- Agent：项目自行实现同步核心执行循环
- LLM：兼容 OpenAI API 的模型服务
- 前端：Vue 3、Vite
- 数据库：SQLite
- Java 基准项目：Maven、JUnit 5
- 版本管理：Git、GitHub

## 目录结构

```text
self-evolving-coding-agent/
├── backend/
│   ├── agent/             # LLM 接入、Agent 循环和任务状态
│   ├── tools/             # 文件、命令和 Maven 测试工具
│   ├── experience/        # SQLite 经验保存与检索
│   ├── api/               # FastAPI 路由与请求/响应模型
│   ├── shared/            # Pydantic v2 公共模型与错误码
│   └── tests/             # 后端测试
├── frontend/              # Vue 3 前端（待初始化）
├── benchmarks/
│   └── java/              # 故意保留缺陷的独立 Maven 样例
├── docs/
│   ├── architecture.md    # 系统架构与版本范围
│   ├── interfaces.md      # 跨模块接口契约
│   ├── development.md     # Git 与开发协作规范
│   ├── architecture/      # 保留的扩展架构资料目录
│   └── reports/           # 实训报告与阶段记录
├── tests/                 # 端到端测试预留目录
├── .env.example           # 无秘密的环境变量示例
├── .gitignore
├── requirements.txt       # 当前公共模型运行时依赖
├── requirements-dev.txt   # 当前后端测试依赖
└── README.md
```

## 团队分工

- 成员一（组长）：负责 `backend/agent/`、`backend/api/`、LLM 接入、任务状态和整体集成。
- 成员二：负责 `backend/tools/`，包括文件操作、命令执行、Maven 测试和工作目录安全。
- 成员三：负责 `frontend/`、`backend/experience/`，包括页面、SQLite 经验存储与检索。

公共数据结构和调用约定见 [`docs/interfaces.md`](docs/interfaces.md)，分支与 PR 规范见 [`docs/development.md`](docs/development.md)。

## 当前开发阶段

仓库处于公共接口准备阶段。目前已建立职责目录、架构/接口/协作文档、Pydantic v2 公共模型及其单元测试，并提供一个可复现空指针错误的 Maven 基准项目；Agent 循环、工具实现、FastAPI 业务接口、前端页面和经验数据库均尚未实现。

第一版已确定：API 后台调度同步 Agent，前端使用 HTTP 轮询；工具模块管理隔离工作目录，Agent 只通过统一工具入口操作业务文件；任务状态暂存于单进程内存，不能使用多 worker 部署。

每个任务使用独立工作目录。任务成功、失败或停止后默认保留现场，开发人员确认无用后再通过 `cleanup_workspace` 手动清理；默认的 `runtime/workspaces/` 已被 Git 忽略，后续版本再考虑 TTL 自动清理。

Java 基准项目位于 `benchmarks/java/`。它包含故意保留的 `NullPointerException`，用于后续验证修复闭环，不应在初始化阶段修复。

## 基础运行要求

- Git
- Python 3.11+
- Node.js 18+ 与 npm（用于后续 Vue/Vite 开发）
- JDK 17+
- Maven 3.8+
- 可访问的 OpenAI API 兼容模型服务（业务功能实现后需要）

安装公共模型和测试依赖：

```bash
python -m pip install -r requirements-dev.txt
```

运行后端单元测试：

```bash
python -m pytest backend/tests
```

本地配置从 `.env.example` 复制并填写，禁止提交真实 API Key：

```bash
cp .env.example .env
```

当前没有可启动的完整应用。可单独验证 Java 缺陷样例：

```bash
cd benchmarks/java
mvn test
```

预期结果是普通字符串测试通过、`null` 输入测试因 `NullPointerException` 不通过，且 Maven 返回非零退出码。只有实际执行命令后才能将其作为测试证据。

## 设计文档

- [`docs/architecture.md`](docs/architecture.md)：项目目标、技术架构、模块、执行流程与版本边界。
- [`docs/interfaces.md`](docs/interfaces.md)：工具、经验和 Agent 模块接口及公共数据结构。
- [`docs/development.md`](docs/development.md)：Git 分支、Pull Request、模块边界和提交规范。
