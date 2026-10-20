# Null Greeting Benchmark

这是一个供 Coding Agent 调试修复流程使用的独立 Maven 项目，缺陷是**故意保留**的。

`GreetingService.greet` 会直接调用 `name.trim()`：

- 普通字符串用例 `greetsNormalName` 应通过。
- `null` 用例 `greetsNullName` 期望返回访客问候，但当前实现会抛出 `NullPointerException`，因此测试应不通过。

## 运行要求

- JDK 17+
- Maven 3.8+

在本目录执行：

```bash
mvn test
```

预期 Maven 返回非零退出码，并报告 `greetsNullName` 出错。这个失败是基准项目的初始状态，不要在公共初始化阶段修复它。
