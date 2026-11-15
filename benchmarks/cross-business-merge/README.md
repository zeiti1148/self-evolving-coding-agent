# 跨业务经验归纳样例

两个独立、故意含缺陷的 Python 项目，用于实际运行修复及经验提炼。它们是业务演示项目，不是生产事故。

- `invoice`：电商发票。商品单价乘数量求和、整单优惠券减免、金额下限、币种及商品行数，最终金额保留两位小数；11 项原始测试。
- `academic-grades`：教务成绩。按权重计算平均分，最终成绩保留一位小数，再判定及格与奖学金；13 项原始测试。

源码保持故障初态。模型仅在各自隔离工作区修改源码；两个任务关闭经验读取，分别提炼原始经验。共同原则由另一阶段自动归纳和复核，业务精度、阈值、顺序、异常和返回字段分别保留。

入口为 `scripts/demo_cross_business_tasks.py` 和 `scripts/demo_cross_business_merge.py`。真实执行结果及完整复现命令见 [跨业务合并实录](../../docs/reports/knowledge-remediation-2026-11-14/cross-business-merge.md)。
