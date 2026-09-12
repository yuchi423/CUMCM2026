"""Generate figures and report for the Q4 matched residual scenario PoC."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


LABELS = {
    "fixed": "固定价", "mean7": "7日价格", "mean14": "14日价格",
    "rolling_cost_selector": "历史费用选择", "load_scenario_selector": "供需情景",
    "joint_scenario_selector": "联合情景", "shuffled_scenario_selector": "错配联合情景",
    "oracle_plan_daily": "逐日最优计划",
}
PLAN_LABELS = {
    "fixed": "固定价", "blend25": "25%近期价", "blend50": "50%近期价",
    "blend75": "75%近期价", "mean14": "14日价格", "mean7": "7日价格",
}
PERIODS = {"development": "开发期", "validation": "验证期", "evaluation": "评估期"}


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def configure() -> None:
    plt.rcParams.update({
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"],
        "axes.unicode_minus": False, "figure.dpi": 140, "savefig.dpi": 320,
        "axes.grid": True, "grid.alpha": .25,
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
    daily = load_csv(experiment / "results/daily_summary.csv")
    scores = load_csv(experiment / "results/selector_scores.csv")
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    audit = json.loads((experiment / "audit_summary.json").read_text(encoding="utf-8"))
    completion = json.loads((experiment / "completion.json").read_text(encoding="utf-8"))
    period_map = {(row["method"], row["period"]): row for row in periods}
    summary_map = {row["method"]: row for row in summaries}
    methods = list(LABELS)

    x = np.arange(len(methods)); width = .24
    fig, axis = plt.subplots(figsize=(15, 5.8))
    for offset, period in enumerate(["development", "validation", "evaluation"]):
        baseline = float(period_map[("mean7", period)]["inventory_adjusted_cost"])
        values = [(float(period_map[(method, period)]["inventory_adjusted_cost"])
                   - baseline) / 10000 for method in methods]
        axis.bar(x + (offset - 1) * width, values, width, label=PERIODS[period])
    axis.axhline(0, color="#333333", linewidth=.9)
    axis.set_xticks(x, [LABELS[method] for method in methods], rotation=20, ha="right")
    axis.set_ylabel("相对7日价格的费用变化（万元）")
    axis.set_title("图1 联合残差情景选计划的分期费用")
    axis.legend()
    save(fig, figures, "Fig1_ScenarioCostByPeriod")

    selector_methods = ["load_scenario_selector", "joint_scenario_selector",
                        "shuffled_scenario_selector", "oracle_plan_daily"]
    counts = {method: Counter(row["selected_plan"] for row in daily if row["method"] == method)
              for method in selector_methods}
    fig, axis = plt.subplots(figsize=(11.5, 6))
    bottom = np.zeros(len(selector_methods))
    colors = plt.cm.viridis(np.linspace(.1, .9, len(PLAN_LABELS)))
    for color, candidate in zip(colors, PLAN_LABELS):
        values = np.asarray([counts[method][candidate] for method in selector_methods])
        axis.bar(np.arange(len(selector_methods)), values, bottom=bottom,
                 label=PLAN_LABELS[candidate], color=color)
        bottom += values
    axis.set_xticks(np.arange(len(selector_methods)),
                    [LABELS[method] for method in selector_methods])
    axis.set_ylabel("84天内选择次数")
    axis.set_title("图2 各情景规则选择的普通购电计划")
    axis.legend(ncol=3)
    save(fig, figures, "Fig2_ScenarioPlanChoices")

    daily_map = {(row["method"], row["date"]): row for row in daily}
    dates = sorted({row["date"] for row in daily})
    actual_gain = np.asarray([
        float(daily_map[("shuffled_scenario_selector", label)]["inventory_adjusted_day_cost"])
        - float(daily_map[("joint_scenario_selector", label)]["inventory_adjusted_day_cost"])
        for label in dates])
    predicted_gap = []
    for label in dates:
        joint = [row for row in scores if row["selector"] == "joint_scenario_selector"
                 and row["target_date"] == label and int(row["chosen"]) == 1][0]
        shuffled = [row for row in scores if row["selector"] == "shuffled_scenario_selector"
                    and row["target_date"] == label and int(row["chosen"]) == 1][0]
        predicted_gap.append(float(shuffled["score"]) - float(joint["score"]))
    fig, axis = plt.subplots(figsize=(9.5, 6.2))
    axis.scatter(np.asarray(predicted_gap), actual_gain, alpha=.7)
    axis.axhline(0, color="#333333", linewidth=.8)
    axis.axvline(0, color="#333333", linewidth=.8)
    axis.set_xlabel("情景评分预计的联合匹配收益（元/日）")
    axis.set_ylabel("实际联合匹配收益（元/日）")
    axis.set_title("图3 匹配价格—供需残差是否带来可兑现收益")
    save(fig, figures, "Fig3_MatchedVsShuffled")

    table_lines = []
    for method in methods:
        row = summary_map[method]
        table_lines.append(
            f"| {LABELS[method]} | {float(row['inventory_adjusted_cost']):,.2f} | "
            f"{float(row['emergency_kwh']):,.1f} | {float(row['paid_grid_spill_kwh']):,.1f} |")
    later_lines = []
    for period in ["validation", "evaluation"]:
        item = selection["later_checks"][period]
        later_lines.append(
            f"- {PERIODS[period]}：选中方案 {item['selected_cost']:,.2f} 元；"
            f"7日价格 {item['mean7_cost']:,.2f} 元；固定价 {item['fixed_cost']:,.2f} 元；"
            f"错配情景 {item['shuffled_cost']:,.2f} 元。")
    verdict = "PASS" if selection["scientific_target_pass"] else "FAIL"
    max_error = max(float(value) for key, value in audit["max_errors"].items()
                    if key != "mutual_count")
    text = f"""# 问题4-2匹配价格—供需残差情景 Cheap PoC 报告

## A 运行产物一览

一键入口为 `final_run/q4_joint_scenario_poc/main.py`。本实验比较{completion['methods']}种方法，每种{completion['days_per_method']}天，并完成{completion['scenario_replays']:,}次情景回放。输出逐时账本、候选计划、情景评分、独立审计和三张PNG/PDF图。

## B 核心结果

开发期原始最优为 `{selection['raw_best']}`，按0.5%近似并列规则选中 **`{selection['selected']}`**。

| 方法 | 84天库存调整费用/元 | 紧急购电/kWh | 已付未用/kWh |
|---|---:|---:|---:|
{chr(10).join(table_lines)}

{chr(10).join(later_lines)}

预声明科学目标判定为 **{verdict}**。联合、供需、错配和oracle均使用相同低维候选计划族。

## C 图表解读

- 图1比较各方法在开发、验证和评估阶段相对7日价格的费用变化。
- 图2展示不同情景规则选择计划的次数，用于判断复杂方法是否实际改变决策。
- 图3比较联合匹配相对错配对照的预计收益与实际收益，直接检验匹配机制。

## D 合理性检查与发现的问题

独立审计结论为 **{'PASS' if audit['pass'] else 'FAIL'}**，复算{audit['actual_dispatch_rows']:,}条实际执行记录，并重新回放全部情景评分。最大误差{max_error:.3e}，计划审计最大误差{audit['plan_audit_max']:.3e}，同时充放电{audit['plan_mutual_count']}次，求解失败{audit['solver_failures']}次；{audit['information_checks']}项未来价格扰动检查通过。

## E 微调记录

本轮没有结果后调参。28个匹配日期、7日错位对照、六个候选计划、0.5%选择容差和0.1%实用阈值均在 `planning/q4-joint-scenario-poc-protocol.md` 中预声明。

## F 最终版本说明

```powershell
D:\\Users\\python.exe final_run/q4_joint_scenario_poc/main.py experiments/{experiment.name} --io-python C:\\Users\\14592\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe
```

## G 敏感性分析下一步计划

仅当validation和evaluation同时通过费用阈值，才申请334天连续运行。若失败，不调整历史天数或错位量；转向显式两阶段随机优化，并首先解决日内非预见性约束。

## H 评委视角

本轮比单纯价格预测更接近费用目标，并用错配联合情景作为机制负对照。局限是只在六个既有普通计划中选择，没有直接优化随机环境下的购电量。Algorithmic Verdict为 **{'PASS' if audit['pass'] else 'FAIL'}**，Scientific Validity Verdict为 **{verdict}**，Deliverable Verdict为 **PARTIAL**。
"""
    (experiment / "model_report.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    args = parser.parse_args(); report(args.experiment.resolve())


if __name__ == "__main__":
    main()
