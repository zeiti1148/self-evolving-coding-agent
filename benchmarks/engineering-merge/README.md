# 真实工程经验合并实验

这组文件给真实上游工程提供扩展回归与保留测试，不是用简化函数替代真实工程。实际生产源码取自以下缺陷提交，并应用 SWE-bench Verified 的原始 `test_patch`：

| 工程 | 缺陷提交 | 上游修复 |
|---|---|---|
| pytest | `3c1534944cbd34e8a41bc9e76818018fadefc9a1` | https://github.com/pytest-dev/pytest/pull/10356 |
| Sphinx | `dd1615c59dc6fff633e27dbb3861f2d27e1fb976` | https://github.com/sphinx-doc/sphinx/pull/8548 |

`pytest_holdout.py` 和 `sphinx_holdout.py` 在第一轮修复时不可见。第一轮发现缺口后，它们被复制进第二轮工作区并明确变为回归测试。`*_fresh_holdout.py` 是第二轮及续跑未见的新保留测试。不能把回归测试与新保留测试混算，也不能用后续成功覆盖先前失败。

源代码完整副本、原始记录、JUnit 和测试日志在 `.cache/engineering-merge/`。API 只来自用户确认的 `D:\中级实训\self-evolving-coding-agent\.env`；脚本需传入离线核对的配置身份，地址、模型、密钥作为一个完整配置组。不要把密钥写进命令行或实验记录。

流程脚本：

1. `scripts/prepare_engineering_cases.py`：从固定 GitHub 归档恢复源码，应用官方测试补丁，保存哈希。需要事先将官方 Verified parquet 转成 `.cache/engineering-repair-research/verified.json`；它不会给修复模型提供 `patch` 或 `hints_text`。
2. `scripts/demo_engineering_merge.py prepare/repair`：独立工作区、数据库和模型上下文；禁止跨任务检索；通过现有 Agent 与提取器生成原始经验。
3. `scripts/validate_engineering_merge.py`：模型修复结束后才读取参考补丁，在单独副本运行原始、模型和参考版本的同一组测试。
4. `scripts/refine_engineering_merge.py`：根据第一轮发现扩大验收，从原缺陷代码重做两个独立任务。
5. `scripts/continue_engineering_merge.py`：保存预算耗尽的失败，提供公开记录的诊断摘要后继续 Sphinx；沿用已成功的 pytest 结果。
6. `scripts/merge_engineering_evidence.py`：先检查扩展验证和源码快照，再调用现有通用原则归纳与独立复核；失败时不调用合并模型。原始经验不改写。
7. `scripts/observe_engineering_merge.py`：从真实修复副本采集标记列表与 Sphinx 生成的 HTML 成员，供直观展示。

`*-requirements.txt` 冻结本次最终环境依赖。pytest 使用 CPython 3.10.20，Sphinx 最终使用其旧版测试矩阵支持的 CPython 3.9.25。依赖安装在实验自己的环境中，不覆盖主项目环境。

最终验收单列两个上游特殊用例：pytest 已有 xfail `TestKeywordSelection::test_keyword_extra_dash`；Sphinx 缺少可选 Cython 时的 `test_cython`。完整扩展报告保留其状态，既不当作通过，也不删除上游测试代码。

这是 Windows 原生工程实验，采用了源码导入路径和历史依赖适配，不是 SWE-bench 官方 Docker 排名成绩。数据集的“1–4 小时”是人工难度标签。没有向修复模型提供官方补丁，但公开数据可能存在模型训练污染，不能证明问题从未被模型见过。

许可证：上游 pytest 使用 MIT，Sphinx 使用 BSD；完整归档中的 LICENSE 保留。数据来源为 [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)。
