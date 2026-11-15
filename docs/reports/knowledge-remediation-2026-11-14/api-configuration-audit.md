# API 配置来源复核（2026-11-14）

用户已明确指定 `D:\中级实训\self-evolving-coding-agent\.env`。本次按该路径重新读取，确认 API 为 `https://api.deepseek.com`，模型为 `deepseek-flash`，使用直连。核查及修复未发送新的模型请求。

## 查到的事实

- 当前仓库与 `self-evolving-coding-agent-next` 的 `.env` 使用同一 API 地址、模型及同一把密钥；仅比较摘要，未输出密钥。密钥 SHA-256 的前 12 位均为 `c5f83d792da0`。这会使用同一账户额度，不能通过不同文件路径实现计费隔离。
- 本次启动进程未继承 `LLM_API_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。旧代码使用 `load_dotenv(..., override=False)`，若其他启动环境存在这些变量，会覆盖文件中的对应项，甚至混用不同来源的地址与密钥。
- 108 项真实实验记录了 677 个模型请求/重试事件，均请求 `deepseek-flash`，均记录为直连；其中 669 个成功响应返回 `deepseek-flash`。请求事件数包含失败/重试，不等于计费次数。
- 旧请求事件未保存 API 域名、配置文件或密钥指纹。因此历史日志无法独立证明每次请求用了哪把密钥，也不能仅凭本次文件内容反推全部历史凭据。当前重复密钥是已经证实的配置事实。
- `.env` 中的 `LLM_PROVIDER=openai` 不决定路由；当前传输实际使用 `LLM_API_BASE_URL` 拼接 `/chat/completions`，这是一种接口协议，并不意味着调用了 OpenAI 账户。
- 前端 `.env.local` 的 `VITE_API_BASE_URL` 为空，开发代理 `API_PROXY_TARGET` 指向 `http://127.0.0.1:8000`。核查时本机 8000/5173 端口没有监听服务；因此未发现此时页面连接到另一份运行中后端的证据，不能据此推断过去的服务归属。

之前只核对了配置是否可用，未在付费实验前核对项目凭据隔离，是实验预检的疏漏。按用户此次确认继续以当前仓库的 `.env` 为准，未修改该文件里的密钥，也未修改另一个仓库。

## 已补上的防护

1. `backend/llm_configuration.py` 将地址、密钥和模型作为完整配置组读取。默认来自本仓库 `.env`；缺项保持缺失，不从共享环境补齐，不把模型凭据写入进程环境。
2. 文件值按原文字面读取，拒绝 `${...}` 间接导入其他环境凭据。专用配置文件可通过显式参数或 `LLM_CONFIG_FILE` 选择；环境部署需显式设置 `LLM_CONFIG_SOURCE=environment`。
3. 每次请求及未来实验起始报告记录来源文件、域名、模型、密钥摘要和完整配置身份。用户信息、URL 查询参数、可能含秘密的路径及密钥原文不会写入该审计。
4. 实验脚本新增离线 `--inspect-api`。实际运行必须显式传入配置文件与刚核对的 `identity`；地址、模型或密钥发生变化时，在建实验目录和创建模型客户端之前拒绝启动。

```powershell
.\.venv\Scripts\python.exe scripts/knowledge_live_experiment.py --inspect-api --llm-env-file "D:\中级实训\self-evolving-coding-agent\.env"
```

验证：新增 15 项测试；相关配置/请求传输测试 73 项通过；全量后端 628 项通过、1 项跳过。测试使用本地模拟传输，不调用真实模型 API。脱敏核查数据见 [api-configuration-audit.json](api-configuration-audit.json)。

本轮仅复核已有实验、修复配置隔离和增加效果展示；原始 108 项实验与原源码快照未改写。若将来需要按账户区分两个项目的用量，需要在选定配置文件中配置项目专属凭据，文件位置本身无法区分账单。
