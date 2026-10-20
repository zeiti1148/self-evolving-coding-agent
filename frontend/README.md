# M3：Vue 修复工作台

实现任务提交、执行过程、最终结果和经验记录展示。默认使用浏览器模拟数据，
无需启动 Python、FastAPI、Java 或模型服务。所有本阶段代码均位于 `frontend/`。

## 启动

需要 Node.js 20.19+（20.x）或 22.12+，本次验证环境为 Node.js 24.11.1、npm 11.6.2。
在项目根目录打开 PowerShell：

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

打开终端打印的本地地址，默认是 `http://127.0.0.1:5173/`。
如果端口占用，Vite 会提示实际使用的端口。按 `Ctrl+C` 停止。
本机已经安装依赖时，可以直接执行 `npm.cmd run dev`。

如果 npm 官方源在当前网络无法连接，可仅对这一次安装指定镜像：

```powershell
npm.cmd ci --registry=https://registry.npmmirror.com
```

不需要创建 `.env` 即可进入模拟模式；如需修改配置：

```powershell
Copy-Item .env.example .env.local
```

## 手动验收

1. 打开“新建任务”，先直接提交，确认提示必填；填写描述后，将决策轮数改成
   `101`，确认提示范围为 `1–100`。
2. 点击“填入修复示例”，恢复决策轮数为 `20`，选择“修复成功并保存经验”并提交。
   页面会跳转到任务详情，约 8 秒后结束，中间逐步出现工具调用。
3. 展开“读取源代码”“修改工作副本”“运行测试”，检查调用参数、输出、时间和错误。
   前一次测试失败后，最后一次测试成功，最终结果应为：
   **修复成功 / 测试通过 / 已保存经验**，并列出修改文件。
4. 执行中或结束后刷新页面，确认同一标签页中仍能读取该任务。
5. 点击“查看经验记录”，最新一条为刚才任务的经验。展开后可看根因、方案、
   验证证据和来源任务；点击“下一页”“上一页”验证分页。
6. 分别新建以下场景，确认三种结果事实独立展示：

| 演示场景 | 任务状态 | 最终测试 | 经验 |
| --- | --- | --- | --- |
| 修复成功并保存经验 | 执行成功 | 测试通过 | 已保存经验 |
| 最终测试未通过 | 执行失败 | 测试未通过 | 未尝试保存 |
| 达到上限，停止执行 | 已停止 | 未完成验证 | 未尝试保存 |
| 修复成功，经验保存失败 | 执行成功 | 测试通过 | 保存失败，有原因 |
| 任务调度失败 | 执行失败 | 无完整执行结果 | 无经验 |

7. 访问不存在的任务，例如
   `http://127.0.0.1:5173/#/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa`，
   确认显示“找不到这个任务”和重试按钮。
8. 将浏览器缩窄到约 390px，确认提交表单、执行步骤和经验详情均可操作，
   页面没有横向溢出。代码输出块可以单独横向滚动。
9. 使用 `Tab` 和 `Enter` 操作导航、表单、折叠详情；
   “跳到主要内容”应移动焦点，同时保留当前页面。

演示数据说明：

- 预置 7 条经验，每页最多 5 条。成功任务会追加 1 条经验，重复读取不会重复保存。
- 模拟任务和经验保存在当前标签页的 `sessionStorage`；刷新可以保留，
  关闭标签页后不保证保留。浏览器禁止存储时退化为内存演示。
- 模拟进度随时间推进，不是实际后台 Agent。轮数小于 4 时演示停止场景。
- 清除本站的会话存储并刷新，可重置演示数据。
- “原始样例保持不变”描述的是接口约定的工作副本机制；
  模拟模式完全不会读取或修改 Java 项目，也不会写入 SQLite。

## 自动检查

```powershell
npm.cmd test
npm.cmd run build
```

`test` 使用 Vitest + Vue Test Utils，覆盖接口请求字段、模拟生命周期、分页、
提交校验与重复提交、结果三态、错误重试、超时取消、轮询去重与旧响应保护，
以及状态已完成但步骤接口延迟/出错的情况。
`build` 先执行 TypeScript/Vue 类型检查，再生成 `dist/`。

本次验收（2026-10-20）：26 项测试全部通过，类型检查和生产构建通过。
Edge 浏览器完成五种场景、刷新、分页、键盘和手机布局检查，无控制台错误/警告。
五种模拟场景导出的 66 个对象通过现有 Pydantic 公共模型校验。
这些结果验证前端和模拟数据；真实 FastAPI、Agent 与 SQLite 闭环需后续联调。

可预览构建产物：

```powershell
npm.cmd run preview
```

## 与真实 FastAPI 联调

编辑 `.env.local` 后重启开发服务；生产构建则需要重新执行 `npm.cmd run build`：

```dotenv
VITE_USE_MOCK=false
VITE_API_BASE_URL=http://localhost:8000
VITE_SOURCE_PROJECT=由后端确认的已登记项目标识
VITE_SOURCE_PROJECT_LABEL=Java 空指针修复样例
```

只有字符串 `false` 会关闭模拟模式。真实模式连接失败时直接显示错误，
不会自动切回模拟数据。`VITE_` 配置会进入浏览器，不能放密钥。

调用严格依据 `../docs/interfaces.md`：

| 用户操作 | 接口 |
| --- | --- |
| 提交任务 | `POST /api/tasks` |
| 查询状态和结果 | `GET /api/tasks/{task_id}` |
| 查询已完成步骤 | `GET /api/tasks/{task_id}/steps` |
| 经验分页 | `GET /api/experiences?limit=6&offset=0` |

提交只发送 `task_description`、`source_project`、`max_steps`。
分页多取 1 条用于判断有无下一页，不依赖接口未提供的 `total`。
轮询在一轮请求结束后等待 1.2 秒再继续；HTTP 请求 15 秒超时。
退出任务页面会取消请求；终态后补齐最终步骤再停止；404/422 停止自动重试。
`max_steps` 是模型决策预算，因此页面不会用它计算工具执行百分比。

以下公共接口事项留给团队确认，没有修改其他成员模块：

1. `source_project` 的实际登记名称尚未约定；`java-null-greeting` 只是演示默认值。
2. API 服务需允许前端实际访问地址的 CORS；默认开发来源为
   `http://127.0.0.1:5173`。跨域 POST JSON 会预检。
3. 当前契约只有修改文件列表，没有 Diff 内容接口；M3 展示修改文件和工具轨迹，
   不伪造实际 Diff。任务取消、历史列表、经验全文搜索也未新增接口。
4. 第一版后端内存任务在服务重启后丢失；持久化经验中的来源任务可能返回 404，
   前端会如实提示。前端不直接访问 SQLite。

## 代码位置

```text
src/api/contracts.ts       与共享模型对应的 TypeScript 类型
src/api/http.ts            真实 HTTP 适配器
src/api/mock.ts            模拟适配器及标签页存储
src/api/fixtures.ts        演示经验、Java 代码与工具步骤
src/features/taskPoller.ts 可取消的串行轮询
src/views/                 提交、执行/结果、经验三个路由页面
src/components/            状态标签、结果面板与图标
src/App.vue                导航、模式提示与整体布局
src/styles.css             桌面/手机样式
tests/                     前端自动测试
```

分支 `frontend-m3` 从 `origin/main` 的 `a5a58d4` 创建。
经验后端仍保留在独立的 `codex/experience-store` 分支，本阶段不依赖其合并。
