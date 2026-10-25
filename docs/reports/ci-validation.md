# 双平台 CI 检查说明

工作流：[`MVP regression`](../../.github/workflows/ci.yml)。每次 push、Pull Request 更新及手动触发时，分别在 GitHub 托管的 `ubuntu-latest` 和 `windows-latest` 上运行。两个平台独立报告，单个平台失败不会取消另一个平台。

## 环境与检查范围

运行环境固定为 Python 3.11、Eclipse Temurin JDK 17、Node.js 22；Maven 版本由 `scripts/setup_maven.py` 固定为 3.9.11，并校验官方 SHA-512 校验和。前端使用锁文件执行 `npm ci`。

每个平台依次执行以下命令：

| 检查 | 工作目录 | 命令 | 步骤时限 |
| --- | --- | --- | --- |
| Python 依赖 | 仓库根目录 | `python -m pip install -r requirements-dev.txt` | 5 分钟 |
| 前端依赖 | `frontend` | `npm ci` | 5 分钟 |
| 便携 Maven | 仓库根目录 | `python scripts/setup_maven.py` | 5 分钟 |
| 后端测试 | 仓库根目录 | `python -m pytest backend/tests -q` | 5 分钟 |
| 前端测试 | `frontend` | `npm test` | 5 分钟 |
| 生产构建 | `frontend` | `npm run build` | 5 分钟 |
| 接口联调检查 | `frontend` | `npm run check:integration` | 5 分钟 |
| 两样例闭环 | 仓库根目录 | `python scripts/acceptance.py --output <runner.temp>/coding-agent-ci/acceptance-report.json` | 15 分钟 |

单个平台总时限为 35 分钟；同一 ref 的新运行会取消旧运行。安装或前序检查失败时，后续检查按 GitHub Actions 默认行为停止，失败日志保留在对应步骤中。

闭环验收使用仓库内测试专用 `ScriptedLLM`，驱动真实工作副本、Maven、HTTP API 和 SQLite，检查缺陷复现、修复验证、原始样例完整性、经验保存及第二个任务接收第一条经验。此流程没有传入 `--live`，其成功不能作为真实模型验收证据。

## 权限与失败证据

工作流仅授予 `contents: read`，checkout 不持久化 Git 凭据，模型地址、模型名和 API Key 均显式设置为空，不引用仓库 Secrets。Python、Node 与 Maven 依赖不使用 Actions 缓存。

仅在失败时上传指定的 `acceptance-report.json`，保留 7 天。报告包含 CI 样例任务、步骤、测试结果和托管 runner 的临时路径；不打包整个 `.cache`、工作副本、数据库、`.env` 或用户环境目录。验收尚未运行、或进程被超时强制结束且未写出报告时，不会生成附件，应查看步骤日志。不要将上传路径扩展成目录通配符，也不要在此流程中注入真实模型密钥。

## 平台差异与结果判定

- Windows 覆盖目录联接、大小写路径以及 Job Object 进程清理；Ubuntu 覆盖符号链接、大小写敏感路径以及进程组清理。应分别检查两个 job，不能用一个平台结果替代另一个。
- Maven 通过 Java 启动 Classworlds，不依赖 ZIP 解压后 `mvn` 脚本的 Unix 可执行位。工作流显式设置项目内 `MAVEN_HOME`，覆盖托管 runner 可能预设的系统 Maven。JDK 17 同时用于编译和测试；项目编译目标保持 Java 17。
- `PYTHON=python` 指向 setup-python 安装的解释器，使前端联调检查使用已安装后端依赖的 Python；`PYTHONUTF8=1` 减少 Windows 默认编码差异。
- 两个平台的真实托管 runner 结果以本次提交对应的 Actions 运行记录为准。新增工作流本身不代表 CI 已通过；首次推送后必须确认两个 job 的全部检查完成。
- 脚本的轮询截止时间不能保证后台线程立即退出，故另外设置 Actions 步骤及 job 时限。超时属于失败，不能当作测试成功。

## 官方 Actions 版本依据

2026-10-25 核对以下官方发布，并在工作流中固定对应完整提交 SHA：

- [actions/checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)
- [actions/setup-python v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0)
- [actions/setup-java v6.1.0](https://github.com/actions/setup-java/releases/tag/v6.1.0)
- [actions/setup-node v7.1.0](https://github.com/actions/setup-node/releases/tag/v7.1.0)
- [actions/upload-artifact v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)

这些 Actions 自身的 JavaScript 运行时由 Action 声明决定，与项目使用的 Node.js 22 分开管理。升级时同时核对官方版本和提交标识，并重新运行双平台检查。
