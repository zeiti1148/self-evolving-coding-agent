# 经验与前端 V2 联调验收（2026-10-28）

> 后续进展：公共模型条件去重和任务记录持久化已在 `backend-reliability` 分支
> 完成，见 [后端可靠性验收](../docs/backend-reliability.md)。下文的问题清单与
> 旧任务限制保留上一轮 `721e73a` 交付时的验收状态。

## 版本与范围

本地分支 `experience-v2-integration` 从 `main@6527421` 创建，再快进载入团队已有的
`feat/experience-evolution-v2@a06d0f3`。项目选择、复杂经验、Python 执行器和合并 API
来自已有 V2 实现；本轮负责复验、经验合并修正、前端响应校验及可复跑的网页验收入口。

新增改动限于 `frontend/`、`backend/experience/` 和经验测试。Agent、API、工具、
公共模型和依赖声明未修改。按交付要求提交并上传至 `experience-v2-integration`，不合并至 main。

## 本轮修正

1. 合并根因、方案和不同记录的适用条件时，保留大小写与内部空白。
   `"Admin"`/`"admin"`、`"a  b"`/`"a b"` 不再被误判为相同规则。
   取舍是保守拒绝部分仅措辞不同的知识，避免悄悄改变代码或字符串的含义。
2. 前端核对请求来源、预览来源及保存结果的 `merged_from`，拒绝来源替换、重复来源、
   结果复用源记录 ID，以及语言与测试执行器不匹配的证据。来源顺序和 UUID 大小写不影响匹配。
3. 提供 `scripts/serve-v2-acceptance.py`：只登记两个受支持的组合缺陷案例，为每个任务
   创建独立脚本模型，连接原有 FastAPI、Agent、Maven/pytest 和 SQLite。

## 本机验证结果

| 检查 | 实际结果 |
| --- | --- |
| 后端全量回归 | 353 项通过，包含本轮新增 5 项；有 1 条 Starlette/HTTPX 弃用提示 |
| 前端全量回归 | 78 项通过，包含本轮新增 11 项 |
| TypeScript、生产构建 | 通过 |
| 既有契约联调 | 5 场景、66 公共模型对象、7 条临时 SQLite 记录通过 |
| 标准 V2 修复脚本 | Java、Python 与 Python 后续复用三个任务，每个最终通过 12 项测试；各保存 3 个结构化问题 |
| 旧库迁移 | 复制上次 main 验收库；2 条旧记录、九列原值、ID 和时间保持一致；12 次并发读取通过；源库未变 |
| 浏览器与真实 HTTP | 页面提交 2 个 Python、1 个 Java 任务，另以 HTTP 提交并发场景与复用场景，共 5 个任务，均通过 12 项最终测试 |
| 合并与恢复 | 跨语言冲突、真实并发 409、重新选择后的合并、重复提交幂等均通过 |
| 范围与分页 | 语言/仓库/范围筛选在分页前生效；旧 Java 通用经验可显示；其他仓库不收到 Python 专属经验 |
| 后续检索 | 后续任务检索到最终合并记录；已被替代的来源及中间合并记录未再次进入检索 |
| 页面与数据 | 浅色/深色页面、390px 宽度无横向溢出；测试库最终 9 条记录，包含保留的 2 条旧经验 |
| 独立审查 | 未发现本轮新增的需修复缺陷；`git diff --check` 通过 |

经验后端新增的 5 项用例在修正前全部失败；前端新增契约用例中的 8 项在修正前失败。
修正后均通过，另覆盖了正常 UUID 排序/大小写及页面 409 恢复和错误预览拦截。

本轮模型均为脚本模型，Java/Python 文件修改、测试、HTTP 和 SQLite 均真实执行。
结果用于证明集成行为，不代表真实大模型修复能力、token 优化或效率提升。

## 如何使用

沿用前一轮补齐的项目 `.venv`、JDK 17、Maven。根 `.env` 中的 `JAVA_HOME` 和
`MAVEN_HOME` 应指向有效安装；脚本模型入口不需要模型密钥。

仓库根目录启动后端：

```powershell
.\.venv\Scripts\python.exe frontend/scripts/serve-v2-acceptance.py
```

将 `frontend/.env.local` 配置为以下内容。本机已设置，其他成员需自行配置：

```dotenv
VITE_USE_MOCK=false
VITE_API_BASE_URL=
API_PROXY_TARGET=http://127.0.0.1:8002
```

第二个终端进入 `frontend` 后启动前端：

```powershell
npm.cmd run dev -- --port 5173 --strictPort
```

打开 <http://127.0.0.1:5173/>：

1. 选择 Java 或 Python 组合异常项目，点击“填入修复示例”，然后提交。
2. 执行过程中会先出现缺陷测试失败和部分修复失败，最后应显示“测试通过、已保存经验”。
3. 在经验记录中筛选 Python、仓库专属、`python-fulfillment`，展开查看三个问题及测试证据。
4. 在新建测试库中，可提交两次 Python 任务，勾选产生的两条经验，先预览再确认合并。
   本机已有验收历史，部分来源已被合并；遇到冲突时选择未被合并的新记录或当前合并结果。
5. 重复合并同一组来源返回同一记录；Java/Python 混选时应显示冲突并禁用确认。

默认网页验收库是 `.cache/frontend-v2-acceptance/experiences.db`，不是正常业务库
`data/app.db`。重新开始独立演示可给后端传入一个新的 `--data-dir` 目录，无需删除旧库。
接口说明位于 <http://127.0.0.1:8002/docs>。服务重启后经验保留，旧任务状态不会恢复。
页面“后端连接模式”只说明 HTTP 真实连接；本入口使用的仍是脚本模型。

标准闭环复验在仓库根目录执行，每次创建独立验收数据：

```powershell
.\.venv\Scripts\python.exe scripts/acceptance_v2.py
```

全量回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q -o cache_dir=.cache/pytest-v2
# 在 frontend 目录执行：
npm.cmd test
npm.cmd run build
npm.cmd run check:integration
```

## 待协调事项（未修改其他成员模块）

- **公共模型的适用条件去重**：`backend/shared/models.py` 将 `applicability` 复用了标签
  的大小写不敏感去重。单条输入同时包含 `role == "Admin"`、`role == "admin"` 时，
  后一个条件在到达存储层之前已被丢弃。本轮只修正了不同记录合并时的比较；建议公共模型
  负责人改为精确文本去重，标签仍保留原规则，并补充单条条件数组的回归测试。
- **任务历史**：任务和步骤仍在内存中，服务重启后来源任务返回 404；SQLite 经验不丢失。
  需要 API/任务模块负责人先确定持久化方案，再安排前端历史入口验收。
- **测试目录敏感性**：`test_catalog_and_task_metadata_are_server_owned` 假设临时项目不属于
  Git 仓库。在仓库内指定 `--basetemp` 时会得到 `git:...;tree:...` 而不是 `tree:...`。
  使用默认系统临时目录即可通过；本轮未修改该 API 测试或项目版本实现。
- **依赖提示**：Starlette TestClient 的 HTTPX 弃用提示仍存在，应随团队依赖升级统一处理。

## 本机证据

证据目录：`.cache/v2-integration-20261028/`。

- `backend-final.log`、`backend-final.xml`、`frontend-final.log`、`build.log`、`contracts.log`
- `merge-red.log`、`frontend-red.log`：修正前的失败用例
- `migration-report.json`：真实旧库副本迁移与源库不变
- `repair-final/report.json`：标准脚本模型三任务、合并与实际收到经验的证据
- `browser-http-report.json`：浏览器/HTTP 五任务、并发冲突、幂等、范围检索与原始项目校验
- `shared-contract-issue.json`：公共模型问题的最小复现

截图与浏览器操作脚本位于 `frontend/output/playwright/v2-20261028/`。
这些本机数据被 Git 忽略；分支交付包含可复用的入口、测试、修正与本说明。
