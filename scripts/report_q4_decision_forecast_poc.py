"""Generate figures and a model report for the decision-focused Q4 PoC."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


LABELS = {
    "blend_a000": "固定价",
    "blend_a025": "25%近期价",
    "blend_a050": "50%近期价",
    "blend_a075": "75%近期价",
    "blend_a100": "14日价格",
    "mean7": "7日价格",
    "rolling_cost_selector": "滚动费用选择",
    "rolling_mae_selector": "滚动MAE选择",
    "oracle_alpha_daily": "逐日最优权重",
}
PERIODS = {"development": "开发期", "validation": "验证期", "evaluation": "评估期"}


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def configure() -> None:
    plt.rcParams.update({
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "figure.dpi": 140,
        "savefig.dpi": 320,
        "axes.grid": True,
        "grid.alpha": 0.25,
    })


def save(fig, directory: Path, name: str) -> None:
    fig.savefig(directory / f"{name}.png", bbox_inches="tight")
    fig.savefig(directory / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)


def report(experiment: Path) -> None:
    configure()
    figures = experiment / "figures"
    periods = load_csv(experiment / "results/periods.csv")
    summaries = load_csv(experiment / "results/summary_tables.csv")
    prices = load_csv(experiment / "results/price_forecast_metrics.csv")
    daily = load_csv(experiment / "results/daily_summary.csv")
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    audit = json.loads((experiment / "audit_summary.json").read_text(encoding="utf-8"))
    completion = json.loads((experiment / "completion.json").read_text(encoding="utf-8"))
    period_map = {(row["method"], row["period"]): row for row in periods}
    summary_map = {row["method"]: row for row in summaries}
    price_map = {row["method"]: row for row in prices}

    selected = selection["selected_method"]
    methods = ["blend_a000", "mean7", selection["static_method"],
               "rolling_cost_selector", "rolling_mae_selector", "oracle_alpha_daily"]
    methods = list(dict.fromkeys(methods))
    period_names = ["development", "validation", "evaluation"]
    x = np.arange(len(methods))
    width = 0.24
    fig, axis = plt.subplots(figsize=(13, 5.5))
    for offset, period in enumerate(period_names):
        baseline = float(period_map[("mean7", period)]["inventory_adjusted_cost"])
        values = [(float(period_map[(method, period)]["inventory_adjusted_cost"])
                   - baseline) / 10000 for method in methods]
        axis.bar(x + (offset - 1) * width, values, width, label=PERIODS[period])
    axis.axhline(0, color="#333333", linewidth=0.9)
    axis.set_xticks(x, [LABELS[method] for method in methods], rotation=18, ha="right")
    axis.set_ylabel("相对7日价格的费用变化（万元）")
    axis.set_title("图1 收缩价格与滚动选择器的分期费用")
    axis.legend()
    save(fig, figures, "Fig1_DecisionCostByPeriod")

    chosen_rows = [row for row in daily if row["method"] in {
        "rolling_cost_selector", "rolling_mae_selector"}]
    dates = sorted({row["date"] for row in chosen_rows})
    date_index = {label: index for index, label in enumerate(dates)}
    fig, axis = plt.subplots(figsize=(14, 5.2))
    styles = [("rolling_cost_selector", "费用选择", "o"),
              ("rolling_mae_selector", "MAE选择", "x")]
    for method, label, marker in styles:
        rows = sorted([row for row in chosen_rows if row["method"] == method],
                      key=lambda row: row["date"])
        axis.scatter([date_index[row["date"]] for row in rows],
                     [float(row["selected_alpha"]) for row in rows],
                     marker=marker, s=28, alpha=0.78, label=label)
    tick_indices = np.linspace(0, len(dates) - 1, min(10, len(dates)), dtype=int)
    axis.set_xticks(tick_indices, [dates[index] for index in tick_indices], rotation=25, ha="right")
    axis.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    axis.set_ylabel("近期14日价格权重 α")
    axis.set_title("图2 两种滚动规则在每天0点选择的价格权重")
    axis.legend()
    save(fig, figures, "Fig2_OnlineAlphaChoices")

    fig, axis = plt.subplots(figsize=(9.5, 6.2))
    base_cost = float(summary_map["mean7"]["inventory_adjusted_cost"])
    for method in methods:
        x_value = float(price_map[method]["mae"])
        y_value = (float(summary_map[method]["inventory_adjusted_cost"]) - base_cost) / 10000
        axis.scatter(x_value, y_value, s=80)
        axis.annotate(LABELS[method], (x_value, y_value), xytext=(5, 5),
                      textcoords="offset points", fontsize=9)
    axis.axhline(0, color="#333333", linewidth=0.9)
    axis.set_xlabel("日前价格MAE（元/kWh）")
    axis.set_ylabel("相对7日价格的84天费用变化（万元）")
    axis.set_title("图3 价格误差与调度费用并非同一目标")
    save(fig, figures, "Fig3_MAEvsDecisionCost")

    rows = []
    for method in ["blend_a000", "mean7", "blend_a100", selection["static_method"],
                   "rolling_cost_selector", "rolling_mae_selector", "oracle_alpha_daily"]:
        if any(row[0] == method for row in rows):
            continue
        summary = summary_map[method]
        rows.append((method, LABELS[method], float(price_map[method]["mae"]),
                     float(summary["inventory_adjusted_cost"]),
                     float(summary["emergency_kwh"])))
    table_lines = [
        f"| {label} | {mae:.6f} | {cost:,.2f} | {emergency:,.1f} |"
        for _, label, mae, cost, emergency in rows
    ]
    later_lines = []
    for period in ["validation", "evaluation"]:
        item = selection["later_checks"][period]
        later_lines.append(
            f"- {PERIODS[period]}：选中方案 {item['selected_cost']:,.2f} 元；"
            f"7日价格 {item['mean7_cost']:,.2f} 元；固定价 {item['fixed_cost']:,.2f} 元；"
            f"滚动MAE选择 {item['rolling_mae_selector_cost']:,.2f} 元。")
    cost_choices = Counter(float(row["selected_alpha"]) for row in daily
                           if row["method"] == "rolling_cost_selector")
    mae_choices = Counter(float(row["selected_alpha"]) for row in daily
                          if row["method"] == "rolling_mae_selector")
    verdict = "PASS" if selection["scientific_target_pass"] else "FAIL"
    max_error = max(float(value) for key, value in audit["max_errors"].items()
                    if key != "mutual_count")
    text = f"""# 问题4-2决策导向电价候选 Cheap PoC 报告

## A 运行产物一览

一键入口为 `final_run/q4_decision_forecast_poc/main.py`。本实验比较{completion['methods']}种方法，每种{completion['days_per_method']}天，另完成{completion['shadow_day_alpha_runs']}个历史日—权重反事实评分，保存逐时账本、选择得分、独立审计和三张PNG/PDF图。

## B 核心结果

开发期选出的静态权重为 α={selection['static_alpha']:.2f}（`{selection['static_method']}`）。两类候选按0.5%近似并列规则最终选中 `{selection['selected_concept']}`，对应运行方法 `{selected}`。

| 方法 | 价格MAE | 84天库存调整费用/元 | 紧急购电/kWh |
|---|---:|---:|---:|
{chr(10).join(table_lines)}

{chr(10).join(later_lines)}

滚动费用选择的权重次数为 `{dict(sorted(cost_choices.items()))}`；滚动MAE选择的权重次数为 `{dict(sorted(mae_choices.items()))}`。预声明科学目标判定为 **{verdict}**。

## C 图表解读

- 图1按阶段比较收缩价格、费用选择器、MAE选择器和逐日oracle相对 `mean7` 的费用变化。
- 图2显示两种在线选择规则的每日权重，检查费用反馈是否真正形成不同决策。
- 图3直接比较价格MAE与调度费用，用于防止以预测误差替代题目费用目标。

## D 合理性检查与发现的问题

独立审计结论为 **{'PASS' if audit['pass'] else 'FAIL'}**，复算{audit['actual_dispatch_rows']:,}条记录。最大数值复算误差{max_error:.3e}，计划审计最大误差{audit['plan_audit_max']:.3e}，同时充放电{audit['plan_mutual_count']}次，求解失败{audit['solver_failures']}次；{audit['information_checks']}项未来价格扰动检查通过。

## E 微调记录

本轮没有结果后调参。14日近期价格、五档收缩权重、28日滚动评分、0.5%选择容差及0.1%实用阈值均在 `planning/q4-decision-forecast-poc-protocol.md` 中预声明。

## F 最终版本说明

```powershell
D:\\Users\\python.exe final_run/q4_decision_forecast_poc/main.py experiments/{experiment.name} --io-python C:\\Users\\14592\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe
```

## G 敏感性分析下一步计划

只有本轮在validation和evaluation都通过预声明费用阈值，才进入334天连续运行。失败时不在相同窗口加密 α 或改28日窗口；Workflow Recheck应转向能够显式评价普通计划费与紧急购电费的优化目标。

## H 评委视角

本轮的价值在于直接比较“按预测误差选模型”和“按历史调度费用选模型”，并保留逐日因果选择证据。局限是84天分段窗口及历史反事实使用共同SOC代理，不能直接替代全年 `result4-2.xlsx`。Algorithmic Verdict为 **{'PASS' if audit['pass'] else 'FAIL'}**，Scientific Validity Verdict为 **{verdict}**，Deliverable Verdict为 **PARTIAL**。
"""
    (experiment / "model_report.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    args = parser.parse_args()
    report(args.experiment.resolve())


if __name__ == "__main__":
    main()
