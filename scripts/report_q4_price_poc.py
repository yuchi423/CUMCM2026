"""Create the figures and paper-facing report for the Task 4 price PoC."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
METHOD_LABELS = {
    "fixed_attachment1": "附件1固定价",
    "lag1": "前一日同刻",
    "mean7": "7日同刻均值",
    "mean14": "14日同刻均值",
    "mean28": "28日同刻均值",
    "weekday35": "同星期均值",
    "ewma14": "14日指数均值",
    "lag1_shift6h": "错移6小时",
    "oracle": "完美预知上界",
}
PERIOD_LABELS = {"development": "开发期", "validation": "验证期", "evaluation": "评估期"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def save(figure: plt.Figure, base: Path) -> None:
    figure.savefig(base.with_suffix(".png"), dpi=320, bbox_inches="tight", facecolor="white")
    figure.savefig(base.with_suffix(".pdf"), bbox_inches="tight", facecolor="white")
    plt.close(figure)


def money(value: float) -> str:
    return f"{value:,.2f}"


def report(experiment: Path) -> None:
    plt.rcParams.update({
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "figure.dpi": 120,
    })
    figures = experiment / "figures"
    metrics = read_csv(experiment / "results/price_forecast_metrics.csv")
    summaries = read_csv(experiment / "results/summary_tables.csv")
    periods = read_csv(experiment / "results/periods.csv")
    windows = read_csv(experiment / "results/window_summary.csv")
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    audit = json.loads((experiment / "audit_summary.json").read_text(encoding="utf-8"))
    completion = json.loads((experiment / "completion.json").read_text(encoding="utf-8"))
    methods = [row["method"] for row in summaries]
    causal_display = [m for m in methods if m != "oracle"]

    all_metrics = {row["method"]: row for row in metrics if row["period"] == "all"}
    labels = [METHOD_LABELS[m] for m in causal_display]
    x = np.arange(len(labels))
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    axes[0].bar(x, [float(all_metrics[m]["mae"]) for m in causal_display], color="#3B82A0")
    axes[0].set_ylabel("MAE（元/kWh）")
    axes[0].set_title("价格数值预测误差（84天）")
    axes[1].bar(x, [float(all_metrics[m]["rank_correlation"]) for m in causal_display], color="#D97746")
    axes[1].set_ylabel("日内 Spearman 相关系数")
    axes[1].set_title("日内高低价排序能力（84天）")
    for axis in axes:
        axis.set_xticks(x, labels, rotation=35, ha="right")
        axis.grid(axis="y", alpha=0.25)
    fig.suptitle("图1 价格预测方法的数值精度与日内排序能力")
    fig.tight_layout()
    save(fig, figures / "Fig1_PriceForecastMetrics")

    summary_map = {row["method"]: row for row in summaries}
    cash = np.asarray([float(summary_map[m]["total_cost"]) for m in methods]) / 10000
    adjusted = np.asarray([float(summary_map[m]["inventory_adjusted_cost"]) for m in methods]) / 10000
    x = np.arange(len(methods))
    width = 0.38
    fig, axis = plt.subplots(figsize=(13, 5.2))
    axis.bar(x - width / 2, cash, width, label="现金费用", color="#4C78A8")
    axis.bar(x + width / 2, adjusted, width, label="库存调整费用", color="#F28E2B")
    axis.set_xticks(x, [METHOD_LABELS[m] for m in methods], rotation=35, ha="right")
    axis.set_ylabel("费用（万元）")
    axis.set_title("图2 同一84天实际轨迹下的调度费用比较")
    axis.grid(axis="y", alpha=0.25)
    axis.legend(frameon=False)
    fig.tight_layout()
    save(fig, figures / "Fig2_PoCCostComparison")

    selected = selection["selected"]
    focus = ["fixed_attachment1", selected, "lag1_shift6h", "oracle"]
    period_map = {(row["method"], row["period"]): row for row in periods}
    period_names = ["development", "validation", "evaluation"]
    x = np.arange(len(period_names))
    width = 0.19
    fig, axis = plt.subplots(figsize=(10.8, 5.1))
    colors = ["#7F8C8D", "#2A9D8F", "#E76F51", "#6C5CE7"]
    for index, method in enumerate(focus):
        values = [float(period_map[(method, p)]["inventory_adjusted_cost"]) / 10000 for p in period_names]
        axis.bar(x + (index - 1.5) * width, values, width, label=METHOD_LABELS[method], color=colors[index])
    axis.set_xticks(x, [PERIOD_LABELS[p] for p in period_names])
    axis.set_ylabel("库存调整费用（万元）")
    axis.set_title("图3 开发、验证与评估阶段的费用外推表现")
    axis.grid(axis="y", alpha=0.25)
    axis.legend(frameon=False, ncol=2)
    fig.tight_layout()
    save(fig, figures / "Fig3_PeriodCost")

    later = selection["later_checks"]
    stable = all(
        later[period]["beats_fixed_control"] and later[period]["beats_negative_control"]
        for period in ["validation", "evaluation"]
    )
    selected_summary = summary_map[selected]
    fixed_summary = summary_map["fixed_attachment1"]
    oracle_summary = summary_map["oracle"]
    improvement = float(fixed_summary["inventory_adjusted_cost"]) - float(selected_summary["inventory_adjusted_cost"])
    improvement_pct = improvement / float(fixed_summary["inventory_adjusted_cost"]) * 100
    solver_counts = {
        "LP": sum(int(float(row["days"])) - int(float(row["milp_days"])) for row in windows),
        "MILP": sum(int(float(row["milp_days"])) for row in windows),
    }
    scientific = "PASS（预声明窗口内）" if stable else "FAIL（未稳定优于控制组）"
    extrapolation = "RISK（84天PoC尚不能替代334天正式回测）"

    rows_md = []
    for method in methods:
        row = summary_map[method]
        metric = all_metrics[method]
        rows_md.append(
            f"| {METHOD_LABELS[method]} | {float(metric['mae']):.4f} | "
            f"{float(metric['rank_correlation']):.3f} | {money(float(row['total_cost']))} | "
            f"{money(float(row['inventory_adjusted_cost']))} | {float(row['emergency_kwh']):,.1f} | "
            f"{float(row['paid_grid_spill_kwh']):,.1f} |"
        )
    period_lines = []
    for period in ["validation", "evaluation"]:
        item = later[period]
        period_lines.append(
            f"- {PERIOD_LABELS[period]}：选中方法库存调整费用 {money(item['selected_cost'])} 元；"
            f"固定价控制组 {money(item['fixed_control_cost'])} 元；错位负对照 "
            f"{money(item['negative_control_cost'])} 元。"
        )

    text = f"""# 问题4-2因果电价预测 Cheap PoC 报告

## A. 交付物清单

- 一键入口：`final_run/q4_poc/main.py`
- 逐时调度账本：`dispatch.csv.gz`
- 每日、窗口、阶段和总汇总：`results/`
- 每日计划与价格预测：`plans.jsonl.gz`
- 独立审计：`audit_summary.json`
- 选择记录：`selection.json`
- 图1—图3：`figures/`，均提供 PNG（320 dpi）与 PDF。

本次是批准后的 Cheap PoC，共比较 {completion['methods']} 种方法、6个14天窗口，每种方法 {completion['days_per_method']} 天；不等同于完整334天问题4-2交付。

## B. 核心结果

开发期按照库存调整费用选择，0.5%近似并列时优先简单模型。原始最优为 `{selection['raw_best']}`，近似并列集合为 `{', '.join(selection['eligible_within_tolerance'])}`，最终选中 **{METHOD_LABELS[selected]}（`{selected}`）**。

| 方法 | 价格MAE | 日内排序相关 | 现金费用/元 | 库存调整费用/元 | 紧急购电/kWh | 已付未用电/kWh |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows_md)}

选中方法相对附件1固定价控制组，在84天上的库存调整费用减少 {money(improvement)} 元（{improvement_pct:.2f}%）。完美预知上界的库存调整费用为 {money(float(oracle_summary['inventory_adjusted_cost']))} 元，它只表示信息上界，未参与选择。

{chr(10).join(period_lines)}

## C. 图形解释

- 图1同时比较价格点值误差与日内排序。储能调度依赖高低价位置，因此MAE不是唯一判据，排序相关和高低价时段重合率用于补充判断。
- 图2并列给出现金费用与库存调整费用。二者接近时说明结论不是靠少留期末电量获得；出现差异时以库存调整费用作选择依据。
- 图3只展示固定价控制组、开发期选中方法、错位负对照和完美预知上界在三个阶段的表现，用于判断开发期优势是否能够外推。

## D. 合理性与审计

- 算法有效性：**{'PASS' if audit['pass'] else 'FAIL'}**。独立脚本复算 {audit['actual_dispatch_rows']:,} 条10分钟记录的能量平衡、SOC递推、功率边界和费用。
- 最大审计误差为 {max(float(v) for v in audit['max_errors'].values()):.3e}，计划审计最大误差为 {audit['plan_audit_max']:.3e}，同时充放电次数为 {audit['plan_mutual_count']}。
- 信息边界：**{'PASS' if audit['information_boundary_pass'] else 'FAIL'}**。扰动当天及未来真实价格后，所有可执行预测不变，只有oracle变化。
- 求解情况：LP日数 {solver_counts['LP']}，MILP复核/回退日数 {solver_counts['MILP']}，求解失败 {audit['solver_failures']}。
- 科学目标：**{scientific}**；外推判断：**{extrapolation}**。

## E. 微调记录

本次没有根据结果调参。方法集合、6个时间窗口、0.5%近似并列规则、库存调整费用、负对照、oracle和失败条件均在运行前写入 `planning/q4-poc-protocol.md`。问题2 `combined` 的0.8分位余量、14天残差窗口和1200kWh计划终端目标保持冻结。

## F. 运行方法

在仓库根目录执行：

```powershell
C:\\Users\\14592\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe final_run/q4_poc/main.py experiments/q4-price-poc-20260912-01
```

目标目录必须不存在。入口依次运行PoC、独立审计和制图报告；任一步失败都会返回非零状态。

## G. 下一步敏感性

如果人工认可本PoC，正式334天运行首先固定 `{selected}`，不得使用验证期或评估期重新选模型。正式报告需补充全年逐月费用、极端高价日表现和相对 `lag1` 回退基线的差值。若评估阶段没有稳定优于两类控制组，按批准口径回退为 `lag1`；若 `lag1` 算法失败，再回退为附件1固定价计划。

## H. 评委视角结论

该PoC的优点是把价格预测价值与供需预测、储能参数和真实结算价格隔离，并设置错位负对照及完美信息上界。主要限制是窗口总计84天，且各窗口从共同的既有轨迹SOC重新起步，不能代替全年连续SOC回测。当前交付状态为 **PARTIAL**：足以判断候选价格模型是否值得进入详细运行，但尚不能作为问题4-2最终结果。
"""
    (experiment / "model_report.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    args = parser.parse_args()
    report(args.experiment.resolve())


if __name__ == "__main__":
    main()
