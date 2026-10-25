# 跨文件修复交互回放

将 `recorded-demo.html` 下载或复制到本机，用浏览器打开。HTML 包含本次真实运行的数据与全部显示代码，不依赖网络、API、数据库或旁边的文件。它是已完成运行的回放，点击播放不会发起新的模型请求。

如果在 GitHub 中打开看到的是源代码，请下载文件；也可在仓库根目录运行 `python -m http.server 8765 --bind 127.0.0.1 --directory docs/demo`，然后访问 `http://127.0.0.1:8765/recorded-demo.html`。

## 建议讲解路线（约 3 分钟）

1. **故障**：默认打开库存案例。看两座仓库的数量和方块：转移被拒绝，源仓却从 10 件变成 7 件。用一句话点出原因：程序先扣了源仓，才发现目标仓装不下。
2. **定位**：Agent 读取请求、库存、服务三个业务类和验收测试。三个卡片分别说明数量校验、存取边界、执行顺序的问题。
3. **改代码**：默认展示 TransferService 的关键行。左侧先调用 `source.withdraw`，右侧在它之前增加目标容量检查。顺着图讲：容量不足 → 提前抛出异常 → 不执行扣减 → 源仓保持 10 件。点击另两个文件，可看对应修改。
4. **测试通过**：看测试格子从失败变成全部绿色，12 项验收全部通过。源仓保持 10 件，目标仓保持 4 件；下方有 Maven 的实际测试结果。
5. **保存经验**：成功经验写入 SQLite。讲出留下来的规则：先校验全部前置条件，再修改状态，供相似任务检索参考。

有时间再切到订单案例：原来 21 元，应为 46.5 元；在“改代码”页看 `subtotalCents * (percent / 100)` 如何改成 `subtotalCents * percent / 100`，一眼看出整数除法的顺序问题。

上方切换案例；五个章节按钮直接跳到故障、定位、改代码、测试通过、保存经验。播放键每幕推进，默认 8 秒；可调为 4 秒或 1.5 秒。左右箭头、进度条、从头播放和大字代码控制讲解节奏。完整文件差异收在“改代码”页的展开项。

需要追查具体调用时，点击“查看完整执行记录”，可按实际顺序检查每次工具调用。原始失败测试由准备脚本在独立副本中运行；模型步骤、程序终验及保存的经验原文均可展开查看。

## 案例与证据

| 案例 | 修复前（总数 / 失败 / 错误） | 独立终验 | 实际写入的业务文件 |
| --- | --- | --- | --- |
| 订单结算 | 12 / 10 / 0 | 12 项通过，零跳过 | CartLine、DiscountPolicy、CheckoutService |
| 库存转移 | 12 / 5 / 2 | 12 项通过，零跳过 | TransferRequest、StockLedger、TransferService |

两项任务使用真实 `deepseek-flash`、受控文件工具、Maven 与 SQLite。原始项目保留缺陷，验收测试与构建配置只读。当前录制展示修复后的经验保存，经验检索处于关闭状态。此前 24 项对照实验的数据单独保存在实验报告中。

## 重新录制与导出

在仓库根目录，按 README 配置本地 `.env` 和 Maven：

```powershell
# 只列出案例，不调用模型
.\.venv\Scripts\python.exe scripts/demo.py
# 调用真实模型，两项任务使用独立工作副本和独立 SQLite
.\.venv\Scripts\python.exe scripts/demo.py --live
# 也可只录制一个案例
.\.venv\Scripts\python.exe scripts/demo.py --live --project java-stock-transfer
# 把命令输出的 REPORT 路径替换到下面
.\.venv\Scripts\python.exe scripts/export_replay.py '.cache/demo/<本次目录>/report.json' 'docs/demo/recorded-demo.html'
```

每次录制新建 `.cache/demo/<UTC时间戳>-<随机标识>/`，保留失败、步骤、审计、源文件快照及数据库。导出器只接受完成的真实模型录制；已完成但修复失败的任务仍以失败展示。异常中断的录制不可伪装成完整回放。重新运行可能获得不同结果和不同步骤，不自动重跑整个失败任务。

原始 JSON 留在本机；便携 HTML 省略模型地址、连接配置与完整请求审计，并隐藏工具输出中的工作副本绝对路径。调用参数、代码及结果通过文本节点展示；内嵌数据转义脚本结束标签，页面 CSP 禁止网络连接。

## 现场提交入口

原有前端仍可连接真实 API。将 `frontend/.env.local` 的 `VITE_SOURCE_PROJECT` 设为 `java-order-checkout` 或 `java-stock-transfer`，标签设置为对应中文名称，重启 Vite。任务描述使用 `benchmarks/demo/catalog.json` 对应的 `task_description`，将轮数设为 25 后提交。页面的“填入修复示例”仍是旧 Greeting 描述，不适用于这两个新项目。现场提交会再次调用真实模型，结果与录制可能不同。
