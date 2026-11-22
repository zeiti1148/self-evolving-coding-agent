# 漏合并案例集

记录已经确认的"真实合并伙伴存在、但自动比较没有充分处理"的案例，用于回归与排序评测。每条案例都只依赖确定性逻辑，不调用模型。

## 文件

`cases.json`：

| 字段 | 含义 |
| --- | --- |
| `id` | 案例标识，与失败点对应 |
| `found` / `channel` | 发现日期与途径（`user_reported` / `review` / `experiment`） |
| `fixed` | 当前是否已由代码修复 |
| `history` | 简述当时的现象，避免把结论当证据 |
| `source` | 本次修复产生的经验（描述字段） |
| `partner` | 应当被合并或比较的真实伙伴 |
| `distractors` | 干扰候选，用于检验排序与分批 |
| `expect.top` | 伙伴可接受的最差名次（1 起）；`null` 表示只要求仍可比较、不承诺名次 |

## 使用

回归（CI 可跑，无外部依赖）：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_missed_merge_cases.py -q
```

排序评测：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_candidate_ranking.py
```

两者都直接调用生产函数 `backend.experience.automation.candidate_ranking`，因此案例结论与线上挑选候选的行为一致。

## 维护约定

- 新增案例必须写明发现途径和当前是否已修复；未修复的案例把 `expect.top` 设为 `null`，只锁定"不被丢弃"。
- 不因为某条案例失败就放宽断言；先确认是排序问题还是案例本身写错。
- 样本量少于 20 条时，评测结论只能作为方向性参考，报告里必须写明。
