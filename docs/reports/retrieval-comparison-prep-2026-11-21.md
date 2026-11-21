# 自适应检索的小样本对照：准备与待执行（2026-11-21）

对应阶段五设计第三节的 3.4。本轮完成对照的前置核查与 dry-run，**尚未执行真实调用**，因此没有任何成本或质量结论。

## 现状

- 对照工具已存在：`scripts/run_real_retrieval_comparison.py`。
  - 不带 `--live`：只打印计划，不产生任何模型调用。
  - `--live`：执行真实配对复用试验（同一快照下 contextual 与 adaptive 各跑一次）。
- 2026-11-21 本机 dry-run 输出：

  ```
  4 planned real reuse trials: 2 independent snapshots, paired contextual/adaptive; no calls made.
  ```

  即 2 个独立快照 × 2 种检索策略 = 4 次真实任务。

- 本轮 3.2 已改变注入预算：adaptive 为 3 条 / 8000 字符，contextual 为 5 条 / 16000 字符。因此这次对照测的是**新预算**，与 2026-11-15 的历史结果不可直接比较。

## 未执行的原因

1. `--live` 需要现成的快照与可用的本地服务，并会真实消耗模型额度；本轮只做零成本准备。
2. 只有 2 个快照、4 次试验，即使跑完也只能给出方向性信号，不能当作"自适应更省/更好"的证据。
3. 若 `--live` 中途失败，留下的记录不足以支撑配对比较，等于既花额度又没有结论；执行前应先确认快照与 `.env` 的来源和额度。

## 执行方式（待确认后运行）

```powershell
cd D:\中级实训\self-evolving-coding-agent
# 先确认计划仍为 4 次且无调用
.\.venv\Scripts\python.exe scripts\run_real_retrieval_comparison.py
# 真实执行（消耗模型额度，需保持服务与快照可用）
.\.venv\Scripts\python.exe scripts\run_real_retrieval_comparison.py --live
```

产物将写入脚本约定的报告路径，记录每次试验的策略、注入规则数、耗时、token 与最终测试结果；这些字段与 3.2 新增的审计字段一致。

## 执行后需要报告的内容

- 每个快照内 contextual 与 adaptive 的配对差值（耗时、token、最终测试是否通过）。
- 注入规则数与字符合计（来自 `injection_events`），确认 adaptive 确实只注入了 3 条 / 8000 字符以内。
- 样本量（4 次）与外推边界：不把结果写成成功率提升的证明。

## 限制

- 2 个快照不足以覆盖不同语言、不同缺陷类型。
- 真实任务受网络与模型时延影响，单次耗时波动会掩盖策略差异；必须用配对差值而不是总体均值。
