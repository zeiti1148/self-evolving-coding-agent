# 库存经验 → 积分转赠：真实复用演示记录

## 执行与结果

录制开始于北京时间 2026-10-25 23:23:50。使用真实模型 `deepseek-flash`，新项目 `java-points-gift` 包含 3 个业务类、12 项 JUnit 测试。它处理接收权限、正数校验、余额边界、赠送手续费及失败时的状态一致性。

从已完成的订单、库存录制库制作 SQLite 工作副本，保留两条经验的原始 ID 与内容。源数据库未被修改。本次在工作副本中开启经验检索，执行一个新的积分转赠任务。

| 项目 | 实际结果 |
| --- | --- |
| 原始测试 | 12 项；10 项断言失败、1 项运行错误、0 项跳过 |
| 原经验库数量 | 2 条 |
| 检索条件 | `tags=["java"]`，`error_type="IllegalArgumentException"`，`limit=5` |
| 检索结果 | 命中 1 条库存经验 |
| 成功模型请求中的经验 | ID 与正文 SHA-256 同源经验一致 |
| 模型请求 | 6 次成功，包含 1 次经验提取 |
| 决策轮数 / 工具调用 | 5 / 10，工具数包含程序最终验证 |
| 实际任务耗时 | 23.710 秒 |
| 独立最终测试 | 12 项全部通过，0 失败、错误、跳过 |
| 修改文件 | GiftRequest、PointsAccount、PointsGiftService |
| 原始 Java 源码与验收测试 | 录制结束核查均未变化 |
| 新增经验 | 1 条，工作库最终为 3 条 |

## 经验怎样进入新任务

源经验编号为 `3a1fc9e4-9bc1-463d-a9b5-501604e7f9bc`，来自库存修复任务 `cfde883d-c16e-43c3-b697-cdc507e609bd`。其方案要求先检查请求、源仓可用量和目标容量，再修改状态。

Agent 根据积分任务描述抽取 `IllegalArgumentException`，按 Java 标签和该异常类型检索。`RecordingClient` 只观察传给真实 ChatClient 的用户消息，模型调用仍由生产 ChatClient 完成。记录中保存了实际 `task`、`max_decisions`、`reference_experiences` 内容及对应成功请求审计。

导出时复核四点：源经验确实存在于录制前数据库与检索结果；完整经验对象出现在成功决策请求；该请求的经验 ID 和内容哈希对应；新任务独立终验通过且新经验已保存。演示使用这些记录展示完整链路。

## 修复如何落地

原始 `PointsGiftService` 先扣发送方积分，再给接收方入账。当接收方关闭收礼时，入账抛出异常，发送方已经从 1000 分变成 800 分。

本次模型在 `sender.debit` 之前加入 `recipient.acceptsGifts()` 检查，并提前检查请求、自转赠与包含手续费的余额。相关辅助类同时修复数量边界和手续费合计。最终测试验证拒绝转赠时发送方保持 1000 分、接收方保持 100 分；成功赠送 200 分且手续费 10 分时，发送方扣 210 分，接收方加 200 分。

新增经验编号为 `ed6bd4f5-0423-487f-a96d-78f65d809bd8`。此次为演示新增的一项后续任务，原有 24 项对照数据保持在原实验报告中。

## 文件与运行方法

原始报告：`.cache/reuse-demo/20261025T152350Z-ef50f4ad/report.json`。本次录制的基线提交为 `4152c6a`，新案例与录制器当时尚未提交，报告保留 `git_dirty=true`。

```powershell
.\.venv\Scripts\python.exe scripts/reuse_demo.py --source-report '.cache/demo/20261025T111656Z-5f2470e1/report.json' --live
.\.venv\Scripts\python.exe scripts/export_replay.py '.cache/demo/20261025T111656Z-5f2470e1/report.json' 'docs/demo/recorded-demo.html' --reuse-report '.cache/reuse-demo/<本次目录>/report.json'
```

本次验证：后端 248 项通过；回放逻辑 5 项通过；前后端 `check:integration` 通过。浏览器验证经验字段选择、检索、请求正文、旧经验到新代码、经验数量增长以及原修复案例切换。
