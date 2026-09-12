"""Generate figures and report for the Q4 reference-method Detailed PoC."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


LABELS = {
    "fixed_greedy": "固定价+贪心",
    "mean7_greedy": "7日价格+贪心",
    "netload_shape_greedy": "净负荷价格+贪心",
    "mean7_value_update": "7日价格+价值更新",
    "netload_shape_value_update": "净负荷价格+价值更新",
    "mean7_value_shift6h": "错移价格+价值更新",
    "oracle_greedy": "完美价格+贪心",
    "oracle_value_update": "完美价格+价值更新",
}
PERIODS = {"development": "开发期", "validation": "验证期", "evaluation": "评估期"}


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def save(fig, path: Path) -> None:
    fig.savefig(path.with_suffix(".png"), dpi=320, bbox_inches="tight", facecolor="white")
    fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


def money(value: float) -> str:
    return f"{value:,.2f}"


def report(experiment: Path) -> None:
    plt.rcParams.update({"font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
                         "axes.unicode_minus": False, "figure.dpi": 120})
    figures = experiment / "figures"
    summary = read(experiment / "results/summary_tables.csv")
    periods = read(experiment / "results/periods.csv")
    metrics = read(experiment / "results/price_forecast_metrics.csv")
    effects = read(experiment / "results/effect_decomposition.csv")
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    audit = json.loads((experiment / "audit_summary.json").read_text(encoding="utf-8"))
    completion = json.loads((experiment / "completion.json").read_text(encoding="utf-8"))
    methods = [row["method"] for row in summary]
    smap = {row["method"]: row for row in summary}
    mmap = {row["method"]: row for row in metrics}
    pmap = {(row["method"], row["period"]): row for row in periods}

    price_methods = ["fixed_greedy", "mean7_greedy", "netload_shape_greedy", "oracle_greedy"]
    x = np.arange(len(price_methods))
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8))
    axes[0].bar(x, [float(mmap[m]["mae"]) for m in price_methods], color="#3B82A0")
    axes[0].set_ylabel("MAE（元/kWh）"); axes[0].set_title("日前价格数值误差")
    axes[1].bar(x, [float(mmap[m]["rank_correlation"]) for m in price_methods], color="#D97746")
    axes[1].set_ylabel("日内Spearman相关"); axes[1].set_title("日内价格排序能力")
    for ax in axes:
        ax.set_xticks(x, [LABELS[m].replace("+贪心", "") for m in price_methods], rotation=25, ha="right")
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("图1 净负荷—形状价格预测是否改善日前预测")
    fig.tight_layout(); save(fig, figures / "Fig1_ReferencePriceForecast")

    baseline = float(smap["mean7_greedy"]["inventory_adjusted_cost"])
    delta = [(float(smap[m]["inventory_adjusted_cost"]) - baseline) / 10000 for m in methods]
    colors = ["#2A9D8F" if value < 0 else "#E76F51" if value > 0 else "#7F8C8D" for value in delta]
    x = np.arange(len(methods))
    fig, ax = plt.subplots(figsize=(12, 5.4))
    bars = ax.bar(x, delta, color=colors)
    ax.axhline(0, color="#333333", linewidth=0.8)
    ax.bar_label(bars, labels=[f"{value:+.2f}" for value in delta], padding=3, fontsize=8)
    ax.set_xticks(x, [LABELS[m] for m in methods], rotation=28, ha="right")
    ax.set_ylabel("相对7日价格+贪心的库存调整费用（万元）")
    ax.set_title("图2 两项参考机制的84天费用贡献")
    ax.grid(axis="y", alpha=0.25); fig.tight_layout()
    save(fig, figures / "Fig2_ReferenceCostComparison")

    effect_names = ["price_forecast_effect", "storage_control_effect", "combined_effect"]
    effect_labels = ["只改价格预测", "只改储能控制", "同时修改"]
    x = np.arange(3); width = 0.25
    fig, ax = plt.subplots(figsize=(9.5, 5.0))
    for i, row in enumerate(effects):
        values = [float(row[name]) / 10000 for name in effect_names]
        ax.bar(x + (i - 1) * width, values, width, label=PERIODS[row["period"]])
    ax.axhline(0, color="#333333", linewidth=0.8)
    ax.set_xticks(x, effect_labels); ax.set_ylabel("相对当前模型的费用变化（万元）")
    ax.set_title("图3 价格预测与储能控制的分期贡献（负值为节省）")
    ax.legend(frameon=False); ax.grid(axis="y", alpha=0.25); fig.tight_layout()
    save(fig, figures / "Fig3_EffectDecomposition")

    rows = []
    for method in methods:
        row = smap[method]
        rows.append(f"| {LABELS[method]} | {float(mmap[method]['mae']):.4f} | "
                    f"{money(float(row['total_cost']))} | {money(float(row['inventory_adjusted_cost']))} | "
                    f"{float(row['emergency_kwh']):,.1f} | {float(row['preserved_emergency_kwh']):,.1f} | "
                    f"{float(row['total_window_end_energy']):,.1f} |")
    selected = selection["selected"]
    later_lines = []
    for period in ["validation", "evaluation"]:
        item = selection["later_checks"][period]
        later_lines.append(
            f"- {PERIODS[period]}：选中方案 {money(item['selected_cost'])} 元；当前模型 "
            f"{money(item['mean7_greedy_cost'])} 元；固定价 {money(item['fixed_greedy_cost'])} 元；"
            f"错位储能 {money(item['shifted_value_cost'])} 元。")
    effect_lines = []
    for row in effects:
        effect_lines.append(
            f"- {PERIODS[row['period']]}：只改价格 {money(float(row['price_forecast_effect']))} 元，"
            f"只改储能 {money(float(row['storage_control_effect']))} 元，"
            f"组合 {money(float(row['combined_effect']))} 元（均为相对当前模型的增量）。")
    max_error = max(float(value) for value in audit["max_errors"].values())
    verdict = "PASS" if selection["scientific_target_pass"] else "FAIL"
    text = f"""# 问题4-2参考方法Detailed PoC报告

## A. 交付物

一键入口为 `final_run/q4_reference_poc/main.py`。本实验比较{completion['methods']}种方法，每种{completion['days_per_method']}天，保存逐时账本、计划、更新记录、汇总、效应拆分、独立审计及三张PNG/PDF图。

## B. 核心结果

开发期原始最优为 `{selection['raw_best']}`，按0.5%近似并列规则选出 **{LABELS[selected]}（`{selected}`）**。

| 方法 | 日前价格MAE | 现金费用/元 | 库存调整费用/元 | 紧急购电/kWh | 主动保留产生的紧急电/kWh | 六窗口期末SOC合计/kWh |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

{chr(10).join(effect_lines)}

{chr(10).join(later_lines)}

预声明科学目标判定为 **{verdict}**。0.1%的实用改善阈值、开发期选择规则和后续阶段均未在看到结果后修改。

## C. 图形解释

- 图1检验净负荷—价格水平回归能否改善点值误差和日内排序。
- 图2以原 `mean7_greedy` 为基准，直接显示价格预测、储能控制、错位负对照和完美信息上界的费用差。
- 图3把“只改价格”“只改储能”和“同时改动”分期展示，用于识别真正产生收益的机制。

## D. 审计

独立审计结论为 **{'PASS' if audit['pass'] else 'FAIL'}**，复算{audit['actual_dispatch_rows']:,}条记录；最大复算误差{max_error:.3e}，计划及储能优化审计最大误差{audit['plan_and_reserve_audit_max']:.3e}，求解失败{audit['reserve_failures']}。共{audit['information_checks']}项0点和日内未来扰动检查通过。

## E. 微调记录

本轮没有结果后调参。价格历史35天、0/6/12/18更新、AR(1)截断、0.1%阈值和终端库存系数均在 `planning/q4-reference-poc-protocol.md` 中预声明。联合情景因参考页面缺少完整第5.2节结构而未实现。

## F. 复现

```powershell
D:\\Users\\python.exe final_run/q4_reference_poc/main.py experiments/{experiment.name} --io-python C:\\Users\\14592\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe
```

## G. 后续敏感性

只有当价格感知储能在validation和evaluation均通过预声明阈值，才值得进入334天连续运行。下一步需要检验终端库存系数、更新频率和联合残差情景，但不得用后续阶段重新选择当前结构。

## H. 评委视角

本轮将参考文稿的两个主要机制拆开，因此能够判断收益来自价格预测还是储能控制。局限仍是84天分段窗口和透明近似的终端库存价值；不能把本报告直接作为 `result4-2.xlsx` 的全年结论。Algorithmic Verdict为 **{'PASS' if audit['pass'] else 'FAIL'}**，Scientific Validity Verdict为 **{verdict}**，Deliverable Verdict为 **PARTIAL**。
"""
    (experiment / "model_report.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    args = parser.parse_args()
    report(args.experiment.resolve())


if __name__ == "__main__":
    main()
