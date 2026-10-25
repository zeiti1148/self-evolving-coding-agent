# 跨文件修复交互回放

将 `recorded-demo.html` 下载或复制到本机，用浏览器打开。HTML 包含本次真实运行的数据与全部显示代码，不依赖网络、API、数据库或旁边的文件。它是已完成运行的回放，点击播放不会发起新的模型请求。

如果在 GitHub 中打开看到的是源代码，请下载文件；也可在仓库根目录运行 `python -m http.server 8765 --bind 127.0.0.1 --directory docs/demo`，然后访问 `http://127.0.0.1:8765/recorded-demo.html`。

## 建议讲解路线（约 4 分钟）

1. **订单基线**：15 元 × 3 件，优惠 10%，再加 6 元运费，应该是 46.5 元；原始代码实际算成 21 元。页面显示的数字来自命名验收用例和 Maven 失败断言。
2. **订单第 7、8、9 步**：点击左侧“写入修改”，分别看 CartLine 数量计算、DiscountPolicy 百分比运算、CheckoutService 运费规则。红色是删除，绿色是新增；行号同时标出修改前后位置。
3. **订单第 10–13 步**：第 10 步中间测试已通过，但模型第 11 步又写入了代码，因此之后仍需复测。第 13 步是模型结束后由程序执行的独立最终测试。
4. **库存基线与第 8 步**：源仓 10 件，目标仓 4 件、容量 5，要转移 3 件。原始程序拒绝请求，却已经把源仓扣到 7 件。看 TransferService 如何先检查请求、可用量与容量，再改动库存。
5. **库存最终结果**：12 项测试全部通过，3 个业务文件有实际修改，经验已保存。强调这里保证的是单线程内存案例中的失败不改变状态，不是数据库事务或并发安全证明。

上方切换案例；左侧点击任意步骤；播放键自动推进；左右箭头、进度条、从头播放和大字代码用于控制讲解节奏。默认每步 4 秒，与真实模型运行耗时无关。代码较长时可在代码框内滚动。

“原始失败基线”由准备脚本在独立副本中产生，不计为模型的工具调用。库存任务没有主动调用修复前测试，回放如实保留这点。没有虚构模型隐藏思考。最终经验保留模型实际生成的英文内容。

## 案例与证据

| 案例 | 修复前（总数 / 失败 / 错误） | 独立终验 | 实际写入的业务文件 |
| --- | --- | --- | --- |
| 订单结算 | 12 / 10 / 0 | 12 项通过，零跳过 | CartLine、DiscountPolicy、CheckoutService |
| 库存转移 | 12 / 5 / 2 | 12 项通过，零跳过 | TransferRequest、StockLedger、TransferService |

两项任务使用真实 `deepseek-flash`、受控文件工具、Maven 与 SQLite。原始项目保留缺陷，验收测试与构建配置只读。当前录制关闭经验检索，但成功后正常保存经验；不能据此判断经验复用是否改善效果，也不把它加入此前固定 24 项实验。

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
