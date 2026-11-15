"""Render a Chinese experiment overview from saved artifacts; never call a model."""
import json
from pathlib import Path
import sqlite3

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/reports/knowledge-remediation-2026-11-14"


def main():
    metrics = json.loads((OUT / "metrics.json").read_text(encoding="utf-8"))
    old_audit = json.loads((ROOT / ".cache/consolidation-audit-55632903/results.json").read_text(encoding="utf-8"))
    database = ROOT / ".cache/knowledge-release-validation/group-0-duplicates.db"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        new_counts = {table: connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                      for table in ["experiences", "knowledge_rules", "knowledge_evidence"]}
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.is_file():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font)).get_name()
    else:
        plt.rcParams["font.family"] = ["Noto Sans CJK SC", "Microsoft YaHei", "DejaVu Sans"]
    plt.rcParams.update({"font.size": 12, "axes.unicode_minus": False, "figure.dpi": 160,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False, "axes.spines.bottom": False,
                         "svg.fonttype": "path"})
    old_color, new_color = "#64748b", "#087f8c"
    text_color, muted = "#172b3a", "#526374"
    fig, axes = plt.subplots(2, 2, figsize=(14, 9.5), facecolor="white")
    fig.subplots_adjust(left=.12, right=.96, bottom=.24, top=.79, wspace=.45, hspace=1.03)
    fig.text(.07, .94, "自动经验整理：修复前后实测", fontsize=24, weight="bold", color=text_color)
    fig.text(.07, .89, "20 次重复学习  ·  294 对受控保留样本  ·  108 次真实模型任务", color=muted, fontsize=13)

    def bars(ax, labels, values, title, subtitle, unit, labeler=None):
        colors = [old_color] * (len(labels) - 1) + [new_color]
        ax.barh(labels, values, color=colors, height=.46)
        ax.invert_yaxis()
        ax.set_title(title, loc="left", pad=35, fontsize=16, color=text_color)
        ax.text(0, 1.06, subtitle, transform=ax.transAxes, fontsize=10.5, color=muted)
        ax.set_xlim(0, max(values) * 1.34 if max(values) else 1)
        ax.set_xlabel(unit, fontsize=10.5, color=muted, loc="right")
        ax.tick_params(axis="both", length=0, labelcolor=muted, labelsize=11)
        ax.set_axisbelow(True)
        ax.grid(axis="x", color="#e8edf2", linewidth=.7)
        for i, value in enumerate(values):
            label = labeler(value) if labeler else f"{value:,.0f}"
            ax.text(value + max(values) * .035, i, label, va="center", color=text_color, fontsize=15, weight="bold")

    old_repeat = old_audit["repeated_learning"]
    assert new_counts == {"experiences": 20, "knowledge_rules": 1, "knowledge_evidence": 20}
    bars(axes[0, 0], ["旧结构", "新结构"], [old_repeat["total_evidence_entries"], new_counts["knowledge_evidence"]],
         "证据不再随合并重复复制", "原始 20 条保留；19 个派生对象 → 1 条规范规则", "证据项数")
    bars(axes[0, 1], ["旧策略", "新策略"],
         [metrics["old_semantic"]["heldout"]["false_conflicts"], metrics["semantic"]["local_nli"]["heldout"]["false_conflicts"]],
         "误报冲突明显减少", "294 对受控保留样本，不能外推生产准确率", "误报数 / 对")
    arms = metrics["live"]
    reduction = 100 * (1 - arms["new"]["mean_reference_characters"] / arms["old"]["mean_reference_characters"])
    bars(axes[1, 0], ["旧策略", "新策略"], [arms[a]["mean_reference_characters"] for a in ["old", "new"]],
         f"实际引用长度减少 {reduction:.1f}%", "按实际决策平均；两种策略均只有 Python 命中经验", "字符 / 决策")
    bars(axes[1, 1], ["无经验", "旧策略", "新策略"], [arms[a]["passed"] for a in ["none", "old", "new"]],
         "尚未测出修复成功率提升", "每组 8 个项目 × 4 次；原始及保留测试均通过", "通过任务数 / 项", lambda v: f"{v} / 32")
    fig.text(.07, .15, "实际反例：x >= 10 与 x > 10，在 x = 10 时行为相反。", fontsize=13, color=text_color, weight="bold")
    fig.text(.07, .105, "NLI 曾以 0.993 蕴含分数错误判为等价；加入比较保护后转入待审阅，不再自动合并。", fontsize=12, color=text_color)
    fig.text(.07, .061, "16 对挑战样本：误合并 2 → 0，待审阅 8 → 10；7 个真实冲突仍漏识别 4 个。", fontsize=11, color=muted)
    fig.text(.07, .022, "仅展示已完成实验，未追加模型 API 调用。小样本、重复模板及 Java 引用缺口见完整报告。", fontsize=10.5, color=muted)
    for suffix in ["png", "svg"]:
        fig.savefig(OUT / ("effect-overview-zh." + suffix), facecolor="white")
    plt.close(fig)
    print(json.dumps({"reference_reduction_percent": reduction, "new_counts": new_counts,
                      "output": str(OUT / "effect-overview-zh.png")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
