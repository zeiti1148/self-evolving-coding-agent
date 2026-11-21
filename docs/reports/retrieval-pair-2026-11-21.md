# 检索策略最小配对实验（2026-11-21，新协议）

对应阶段五 3.4。**这不是 2026-11-15 主序列的复现**：原序列的 runner 与所用代码版本不兼容、第三个用例不可得（见 `retrieval-budget-measurement-2026-11-21.md` 与后续核查）。本实验是一个自包含的新协议，用真实模型调用取得任务侧配对数据。

## 协议

- 目标项目：仓库已登记项目 `java-null-greeting`、`java-null-profile`（真实 Maven 项目）。
- 经验种子：每个臂在各自独立数据库里注入**同一份合成规则**（java、仓库 `java-benchmarks`、错误类型 `NullPointerException`、`compatibility_key` 取项目实际 `source_revision`、`evidence` 为 maven 2 项通过）。**种子是合成夹具，不是历史真实修复**，用于让检索有可选中目标。
- 每次运行：启动独立后端（`DATABASE_URL` 指向该臂数据库、`EXPERIENCE_RETRIEVAL_STRATEGY=contextual|adaptive`），通过普通 HTTP API 提交同一任务（`max_steps=12`、`execution_mode=single`），轮询到终态，读取 `/api/tasks/{id}/metrics` 与审计中的注入事件。
- 每个项目跑两臂各一次，共 **4 次真实任务**。

## 实测结果

| 项目 | 策略 | 注入请求数 | 注入规则数 | 每轮注入字符 | token | 耗时 | 最终测试 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| java-null-greeting | contextual | 6 / 7 | 1 | 1196 | 28,432 | 42.9 s | 通过 |
| java-null-greeting | adaptive | 0 / 7 | 0 | 2 | 22,830 | 31.1 s | 通过 |
| java-null-profile | contextual | 5 / 6 | 1 | 1196 | 21,991 | 25.6 s | 通过 |
| java-null-profile | adaptive | 0 / 6 | 0 | 2 | 25,133 | 30.4 s | 通过 |

重复注入比例（同一规则被重复注入的比例）：contextual 0.833 / 0.800，adaptive 0（没有注入）。

## 结论（只写测到的）

1. **contextual 每轮都重新注入同一条规则**，两个项目分别重复 5–6 次，每次约 1196 字符；这就是 3.3 里"重复注入比例"指标在真实任务上的取值。
2. **adaptive 两次都注入 0 条**，审计给出的原因是 `no_failure_evidence`（6 次）与 `tests_passed`（1 次）：该任务里模型没有产生一次"失败的测试运行"，自适应门控因此从未打开。这说明自适应策略省下的注入量来自**门控从未开放**，不是更聪明的排序。
3. **成本方向不稳定**：greeting 项目上 adaptive 更省（22.8k vs 28.4k token、31.1s vs 42.9s），profile 项目上 adaptive 反而更贵（25.1k vs 22.0k token、30.4s vs 25.6s）。两对结果方向相反，**不能宣称任一策略更省**。
4. 两个臂的最终测试都通过，说明这次比较没有质量差异可归因于注入。

## 限制

- 只有 2 对（4 次任务），且每对只跑一次，没有重复实验，无法估计波动。
- 种子规则是我构造的合成夹具；真实历史经验的表述与条件会不同。
- adaptive 注入 0 条使这次比较实质是"反复注入一条规则"对"完全不注入"，不是两种有效检索的正面对比。
- 单模型（`deepseek-flash`）、单机器、小项目；不能外推到其他模型或大型工程。
- 本轮没有实现 11-15 的 3 案例 × 4 臂 × 20 次协议，因此这些数字与 `real-update-comparison-2026-11-15.md` 不可比。

## 复现方式

```powershell
.\.venv\Scripts\python.exe scripts\minimal_retrieval_pair.py                # 计划，不调用模型
.\.venv\Scripts\python.exe scripts\minimal_retrieval_pair.py --live         # 真实执行（默认项目）
.\.venv\Scripts\python.exe scripts\minimal_retrieval_pair.py --live --project java-null-profile
```

结果写入 `.cache/minimal-retrieval-pair/`（`pair.json`、每臂的 `{arm}.json` 与 `server.log`）。本轮 `java-null-profile` 的 `pair.json` 另存为 `pair-java-null-profile.json`。

## 后续

1. 若要判断"自适应门控是否合理"，需要构造**必须先跑失败测试**的任务（例如描述里不点名异常、测试必须先暴露失败），否则自适应总是走 `no_failure_evidence`。
2. 每对至少重复 3 次才能估计波动；当前 2 对不足以支撑成本结论。
3. 11-15 主序列的重建仍受 runner/代码版本不匹配限制，未继续。
