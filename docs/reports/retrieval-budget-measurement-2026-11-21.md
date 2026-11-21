# 自适应检索对照：前置依赖核查与注入侧测量（2026-11-21）

对应阶段五 3.4。任务侧真实对照**未执行**，原因是硬前置依赖在本机不存在；本轮完成前置核查，并做了一次零成本的注入侧测量，得到一个与预期不同的结论。

## 一、任务侧对照被前置依赖阻断

`scripts/run_real_retrieval_comparison.py --live` 的第一步（`run_live()`，第 170–187 行）：

1. 读取 `.cache/real-update-comparison/records.json`，并要求 primary 序列全部 `observed_complete`（第 172–174 行）。
2. 读取 `after1-retrieval-reference.db`、`after2-retrieval-reference.db` 两个冻结快照（第 185–187 行）。

本机核查：

```
.cache/real-update-comparison/        不存在
after*-retrieval-reference.db          未找到
```

`.cache` 由 Git 排除，这两个产物是 2026-11-15 那次主序列实验在本机留下的中间结果，没有随仓库迁移。因此 `--live` 会在第一个文件读取处失败，不会产生任何模型调用；真实对照既没有跑成，也没有"悄悄降级成模拟"的可能。

不带 `--live` 的计划模式可用（本机实测）：

```
4 planned real reuse trials: 2 independent snapshots, paired contextual/adaptive; no calls made.
```

要恢复任务侧对照，前置条件是：先在本机重跑主序列并生成两个冻结快照，或从原机器拷回 `.cache/real-update-comparison/`（含 `records.json`、`registry.json`、`cases/manifest.json` 与两个快照库）。这需要真实模型调用，不能在本轮零成本完成。

## 二、注入侧确定性测量（无模型调用）

新增 `scripts/measure_injection_budget.py`：用生产检索函数 `retrieval.retrieve`，按两种预算（contextual 5 条/16000 字符、adaptive 3 条/8000 字符）对同一经验库取参考，统计实际注入的规则数与字符数。

| 场景 | 策略 | 上限（条/字符） | 实际注入规则 | 实际字符 |
| --- | --- | --- | ---: | ---: |
| greeting-null-guard | contextual | 5 / 16000 | 1 | 850 |
| greeting-null-guard | adaptive | 3 / 8000 | 1 | 850 |
| rounding-wording-gap | contextual | 5 / 16000 | 1 | 777 |
| rounding-wording-gap | adaptive | 3 / 8000 | 1 | 777 |
| dense-8-rules | contextual | 5 / 16000 | 0 | 0 |
| dense-8-rules | adaptive | 3 / 8000 | 0 | 0 |
| long-6-rules | contextual | 5 / 16000 | 0 | 0 |
| long-6-rules | adaptive | 3 / 8000 | 0 | 0 |

### 结论（与预期不同，需要纠正一个假设）

- **预算上限在这类库里根本不是限制因素**：同仓库 8 条同错误类型规则时，检索返回 0 条，因为未知重叠组合按既有安全规则被整体省略；简单场景下也只有 1 条通过资格过滤。
- 因此把 adaptive 从 5 条/16000 字符降到 3 条/8000 字符，**在这些场景下不改变注入量**。不能把"降预算"当作省钱手段来宣称。
- 2026-11-15 报告中"自适应在固定库对照里更慢更贵"的差异，主要来自**重试与检索时机**（每轮重新检索），而不是单次注入的规则条数。本轮测量与该解释一致。

## 三、本轮实际交付

- `scripts/measure_injection_budget.py`：零成本、可重复的注入侧测量工具。
- 本报告的实测表与上述结论。

## 四、限制

- 注入侧测量只反映"多少经验进入提示"，不反映模型如何使用它们，也不能代替任务侧对照。
- 场景规模小：2 个文档化案例 + 2 个合成库。合成库的"0 条"是歧义屏蔽的结果，不是 bug；但它说明想靠加密经验条数来压测预算上限是无效的。
- 任务侧对照仍缺前置快照，3.4 的真实执行保持未完成状态。

## 五、后续

1. 若要恢复任务侧对照：在本机重跑主序列生成冻结快照，或拷回 `.cache/real-update-comparison/`，再执行 `--live`。
2. 若要继续压缩成本，方向应放在**检索时机**（何时重新检索、失败后是否重查），而不是继续下调条数上限。
