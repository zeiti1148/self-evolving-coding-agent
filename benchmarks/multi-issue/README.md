# 一个任务中的多个异常

两个项目实现同一配送请求场景，由姓名处理、数量解析、商品选择和组合服务构成。原始源码故意保留三个独立缺陷，修复只写入独立工作副本。

| 子问题 | Java | Python | 正确行为 |
| --- | --- | --- | --- |
| 缺失姓名 | NullPointerException | AttributeError | guest |
| 无效数量 | NumberFormatException | ValueError / TypeError | 0；负数也归零 |
| 下标越界 | IndexOutOfBoundsException | IndexError | none；负数也不可选中末项 |

每个项目各有 12 项验收测试，覆盖正常行为、三个边界问题以及一次组合调用。只修复姓名问题时，数量与下标的失败仍会保留。`catalog.json` 提供登记标识、任务描述、语言、仓库 ID 和测试数量。

Java 使用 Maven / JUnit，Python 使用 pytest。测试及构建配置为只读；执行器返回当前运行的测试总数、失败原因和错误类型，供 Agent 在剩余决策预算内继续修复。
