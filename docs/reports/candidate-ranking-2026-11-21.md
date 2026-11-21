# 自动比较的覆盖与成本：分批补查与候选排序（2026-11-21）

对应阶段五设计第一节。本轮只解决"候选受数量限制、超出的不再比较"和"数字口径不透明"两个问题，不宣称漏合并率下降；样本量与结论边界见文末。

## 起点核查

- `backend/experience/automation.py` 的 `MAX_CANDIDATES = 5`：每个来源最多入队 5 对比较，其余候选不补查。
- `candidate_total` 用的是 SQL 粗计数（同语言、同范围、更早创建），没有走后续资格过滤（派生、已合并、证据不完整都会被排除），所以页面上的"其余 N 条未比较"会偏大。
- 计划只在 `candidates is None` 时执行一次，重启后不会重新挑选。
- 本机 `data/app.db` 有 31 条经验但没有 `experience_auto_jobs` 表，说明该库从未运行过自动比较；本轮的对照数据由测试夹具构造，不是历史运行结果。

## 改动

1. 抽出 `candidate_ranking(connection, source)`：候选筛选、词法排序、资格谓词集中在一处，规划与案例重放共用，避免两套顺序漂移。
2. 盘点口径统一：`candidate_total` 改为资格过滤后的数量；新增 `compared_candidates`（已比较对数，含失败尝试）；`selected_candidates` 改为累计入队对数。页面显示「候选 N 条 · 已比较 M · 其余 K 条未比较」。
3. 分批补查：`_plan()` 跳过已入队的候选（`experience_auto_pairs` 两侧任一为本条），每批最多 5 对；一批结束后 job 回到 `pending` 继续下一批。
4. 预算上限：新增 `MAX_PAIRS_PER_JOB = 20`，批次大小按"已比较对数"扣减，达到上限时结束并在提示里写明剩余对数。
5. 漏合并案例集：`benchmarks/missed-merge/cases.json`，每条记录来源对、期望名次、发现途径与是否已修复；`backend/tests/test_missed_merge_cases.py` 用生产排序函数重放，不调用模型。
6. 排序评测：`scripts/evaluate_candidate_ranking.py`，用同一生产函数对比"含词法信号"与"关闭词法信号"两种排序，输出名次与耗时；消融只 patch `lexical_candidates`，不改排序重写。

## 实测

后端相关回归（本机，2026-11-21）：

| 测试集 | 结果 |
| --- | --- |
| `test_automation_batch.py` | 3 项通过 |
| `test_missed_merge_cases.py` | 2 项通过 |
| 自动合并相关 5 个文件 | 35 项通过 |
| 前端 `npm.cmd test` | 202 项通过 / 22 文件 |
| 前端 `npm.cmd run build` | 通过 |
| 全量后端（排除缺 hypothesis 的文件） | 998 通过 / 1 失败 / 1 跳过，328 秒 |

全量后端唯一失败项是 `test_real_native_runner_failure_then_fix[cpp]`，原因是本机没有 `cmake`/`ctest`，与本轮改动无关；跳过项仍是本机不能创建符号链接。与改动前的 993 通过相比，多出的 5 项为本轮新增测试。

行为验证：

- 12 条同范围候选：分 3 批全部比较，模型调用 12 次，`deferred_candidates=0`。
- 预算设为 3 时：只比较 3 对，结束提示写明"已达到单次比较上限 3 对；剩余 4 对候选暂未自动比较"。
- 比较失败的对计入"已比较"，不计入"未比较"。

排序评测（`benchmarks/missed-merge/cases.json` 的 2 条案例）：

| 案例 | 策略 | 候选数 | 真实伙伴名次 | 期望上限 | 排序耗时(ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| greeting-null-guard | production | 7 | 1 | 5 | 6.4 |
| greeting-null-guard | no-lexical | 7 | 1 | 5 | 1.8 |
| rounding-wording-gap | production | 4 | 4 | 不限 | 1.8 |
| rounding-wording-gap | no-lexical | 4 | 4 | 不限 | 1.5 |

汇总：两种策略都是 recall@1 = 1/2、recall@5 = 2/2、平均名次 2.5。

## 结论与限制

- 可以确认：超出首批的候选不会再被丢弃；数量上限现在有明确提示，且不会超发模型调用。
- 不能确认：漏合并率是否下降。案例集只有 2 条，其中 1 条本来就无法用词法区分（`rounding-wording-gap` 期望名次不限）。
- 在当前 2 条案例上，词法信号没有改变名次（错误类型交集已足够区分），关闭词法反而更快（1.8ms vs 6.4ms）。样本太小，不能据此判断是否应移除词法排序；需要扩充案例后重测。
- `candidate_total` 只统计最近 200 条窗口内、且通过资格谓词的候选；超过窗口的历史经验不会出现在计数里。这是既有设计的延续，不是本轮引入。
- 本轮没有运行真实模型比较，没有新增付费调用。

## 后续

1. 扩充漏合并案例集（目标 ≥20 对，覆盖同义改写、条件差异、版本差异），再重跑 `scripts/evaluate_candidate_ranking.py`。
2. 依据评测结果决定是否调整排序信号（错误类型 > 词法 > 新鲜度 的权重）。
3. 观察实际使用中 `MAX_PAIRS_PER_JOB = 20` 是否合适；如需调整，改动只在该常量。
