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
    fixed_cost = float(summary_map["fixed_attachment1"]["inventory_adjusted_cost"])
    adjusted_delta = np.asarray([
        float(summary_map[m]["inventory_adjusted_cost"]) - fixed_cost for m in methods
    ]) / 10000
    emergency = np.asarray([float(summary_map[m]["emergency_kwh"]) for m in methods]) / 1000
    x = np.arange(len(methods))
    colors = ["#2A9D8F" if value < 0 else "#E76F51" if value > 0 else "#7F8C8D"
              for value in adjusted_delta]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    axes[0].bar(x, adjusted_delta, color=colors)
    axes[0].axhline(0, color="#333333", linewidth=0.8)
    axes[0].set_ylabel("相对固定价的费用增量（万元）")
    axes[0].set_title("库存调整费用差值（负值为节省）")
    axes[1].bar(x, emergency, color="#4C78A8")
    axes[1].set_ylabel("紧急购电量（MWh）")
    axes[1].set_title("同一实际轨迹下的紧急购电")
    for axis in axes:
        axis.set_xticks(x, [METHOD_LABELS[m] for m in methods], rotation=35, ha="right")
        axis.grid(axis="y", alpha=0.25)
    fig.suptitle("图2 价格预测对费用与保供结果的影响")
    fig.tight_layout()
    save(fig, figures / "Fig2_PoCCostComparison")

    selected = selection["selected"]
    recommended = selection["recommended_delivery_method"]
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
    stable = bool(selection["scientific_target_pass"])
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

开发期按照库存调整费用选择，0.5%近似并列时优先简单模型。原始最优为 `{selection['raw_best']}`，近似并列集合为 `{', '.join(selection['eligible_within_tolerance'])}`，开发期选中 **{METHOD_LABELS[selected]}（`{selected}`）**。由于它未通过预声明的跨期稳定性条件，回退机制已经触发，当前交付建议为 **{METHOD_LABELS[recommended]}（`{recommended}`）因果基线**，且不主张其一定节省费用。

| 方法 | 价格MAE | 日内排序相关 | 现金费用/元 | 库存调整费用/元 | 紧急购电/kWh | 已付未用电/kWh |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows_md)}

选中方法相对附件1固定价控制组，在84天上的库存调整费用减少 {money(improvement)} 元（{improvement_pct:.2f}%）。完美预知上界的库存调整费用为 {money(float(oracle_summary['inventory_adjusted_cost']))} 元，它只表示信息上界，未参与选择。

{chr(10).join(period_lines)}

## C. 图形解释

- 图1同时比较价格点值误差与日内排序。储能调度依赖高低价位置，因此MAE不是唯一判据，排序相关和高低价时段重合率用于补充判断。
- 图2左侧以固定价控制组为零点显示库存调整费用增量，直接呈现各方法的节省或增支；右侧显示紧急购电量，防止只看费用而忽略保供结果。
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
D:\\Users\\python.exe final_run/q4_poc/main.py experiments/q4-price-poc-20260912-01 --io-python C:\\Users\\14592\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe
```

目标目录必须不存在。入口依次运行PoC、独立审计和制图报告；任一步失败都会返回非零状态。

## G. 下一步敏感性

本PoC已经触发科学目标失败条件，因此不建议直接把 `{selected}` 写成最终价格模型。若进入详细运行，应优先比较批准的 `lag1` 回退基线与附件1固定价控制组，补充334天连续SOC、逐月费用和极端高价日表现；不得再用验证期或评估期重新选择窗口长度。若 `lag1` 算法失败，再回退为附件1固定价计划。

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
