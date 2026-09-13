"""Generate figures and a concise model report for an audited Q4-3 full run."""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def save(fig, base: Path) -> None:
    fig.tight_layout()
    fig.savefig(base.with_suffix(".png"), dpi=220, bbox_inches="tight")
    fig.savefig(base.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def main(run_arg: str) -> None:
    run = (ROOT / run_arg).resolve()
    audit = json.loads((run / "audit_summary.json").read_text(encoding="utf-8"))
    if not audit["pass"]:
        raise AssertionError("Audit must pass before reporting")
    summary = rows(run / "results/summary_tables.csv")[0]
    monthly = rows(run / "results/monthly_summary.csv")
    price = rows(run / "results/price_metrics_daily.csv")
    selection = json.loads((ROOT / "experiments/q4-3-poc-20260913-01/selection.json").read_text(encoding="utf-8"))
    figures = run / "figures"
    figures.mkdir(exist_ok=True)
    plt.rcParams.update({"font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
                         "axes.unicode_minus": False, "font.size": 9})

    labels = [row["period"][5:] for row in monthly]
    x = np.arange(len(labels))
    ordinary = np.array([float(row["ordinary_cost"]) for row in monthly]) / 1e4
    adjustment = np.array([float(row["adjustment_cost"]) for row in monthly]) / 1e4
    emergency = np.array([float(row["emergency_cost"]) for row in monthly]) / 1e4
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.bar(x, ordinary, label="普通购电费", color="#4C78A8")
    ax.bar(x, adjustment, bottom=ordinary, label="调整费", color="#F2CF5B")
    ax.bar(x, emergency, bottom=ordinary + adjustment, label="紧急购电费", color="#E45756")
    ax.set_xticks(x, labels)
    ax.set_xlabel("月份")
    ax.set_ylabel("费用（万元）")
    ax.set_title("问题4-3逐月实际费用分解")
    ax.legend(frameon=False, ncol=3)
    ax.grid(axis="y", alpha=0.25)
    save(fig, figures / "Fig1_Q4_3_MonthlyCost")

    by_month = defaultdict(list)
    for row in price:
        by_month[row["date"][:7]].append(float(row["mae"]))
    mae = [np.mean(by_month[f"2025-{month:02d}"]) for month in range(2, 13)]
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    ax.plot(x, mae, marker="o", linewidth=2, color="#3B7EA1")
    ax.set_xticks(x, labels)
    ax.set_xlabel("月份")
    ax.set_ylabel("MAE（元/kWh）")
    ax.set_title("前14日同刻均价的逐月预测误差")
    ax.grid(alpha=0.25)
    save(fig, figures / "Fig2_Q4_3_PriceForecastMAE")

    emergency_kwh = np.array([float(row["emergency_kwh"]) for row in monthly])
    fig, ax1 = plt.subplots(figsize=(8.6, 4.4))
    ax1.bar(x, emergency_kwh, color="#E45756", alpha=0.8, label="紧急购电量")
    ax1.set_xticks(x, labels)
    ax1.set_xlabel("月份")
    ax1.set_ylabel("紧急购电量（kWh）", color="#B23A3A")
    ax2 = ax1.twinx()
    ax2.plot(x, adjustment, color="#8C6D1F", marker="o", linewidth=2, label="调整费")
    ax2.set_ylabel("调整费（万元）", color="#8C6D1F")
    ax1.set_title("逐月紧急购电量与调整费")
    ax1.grid(axis="y", alpha=0.2)
    handles = ax1.patches[:1] + ax2.lines[:1]
    ax1.legend(handles, ["紧急购电量", "调整费"], frameon=False, loc="upper left")
    save(fig, figures / "Fig3_Q4_3_EmergencyAndAdjustment")

    no_update = next(row for row in rows(
        ROOT / "experiments/q4-3-poc-20260913-01/results/summary_tables.csv")
        if row["strategy"] == "no_update_mean14")
    state_only = next(row for row in rows(
        ROOT / "experiments/q4-3-poc-20260913-01/results/summary_tables.csv")
        if row["strategy"] == "state_only_mean14")
    rolling = next(row for row in rows(
        ROOT / "experiments/q4-3-poc-20260913-01/results/summary_tables.csv")
        if row["strategy"] == "rolling_margin_mean14")
    improve_no = (float(no_update["inventory_adjusted_cost"]) -
                  float(rolling["inventory_adjusted_cost"])) / float(no_update["inventory_adjusted_cost"])
    improve_state = (float(state_only["inventory_adjusted_cost"]) -
                     float(rolling["inventory_adjusted_cost"])) / float(state_only["inventory_adjusted_cost"])
    price_mae = float(np.mean([float(row["mae"]) for row in price]))
    lines = [
        "# 问题4中的4.3：14日同刻均价预测的余量滚动方案", "",
        "## 信息口径", "",
        "每天0:00只读取前14个已结束日的附件4同刻价格，形成当天144时段价格预测。当天未来真实价格不进入计划，只用于普通购电、逐次调整费和5倍紧急购电的事后结算。光伏在0:00、6:00、12:00、18:00按附件3的实际发布时间更新，负荷仍用历史同星期预测。", "",
        "## 模型", "",
        "模型沿用问题3的当日剩余窗口滚动优化。每次更新以最新光伏预报、当前SOC和0.8分位净负荷历史残差余量生成候选计划，并与保留上一版计划比较；只修改尚未执行时段。实际执行遵守光伏先供负载、再充电、再弃光，缺口先放电再紧急购电。", "",
        "对交付时段t，实际费用为：最终普通计划电量乘真实电价，加上各次相邻计划版本变动绝对值乘0.5倍真实电价，再加紧急电量乘5倍真实电价。", "",
        "## 334天结果", "",
        "| 指标 | 数值 |", "| --- | ---: |",
        f"| 普通购电费 | {float(summary['ordinary_cost']):,.2f} 元 |",
        f"| 调整费 | {float(summary['adjustment_cost']):,.2f} 元 |",
        f"| 紧急购电费 | {float(summary['emergency_cost']):,.2f} 元 |",
        f"| 总费用 | **{float(summary['total_cost']):,.2f} 元** |",
        f"| 库存修正费用 | {float(summary['inventory_adjusted_cost']):,.2f} 元 |",
        f"| 普通购电量 | {float(summary['ordinary_kwh']):,.2f} kWh |",
        f"| 紧急购电量 | {float(summary['emergency_kwh']):,.2f} kWh |",
        f"| 期初/期末SOC | {float(summary['e_start']):,.2f} / {float(summary['e_end']):,.2f} kWh |",
        f"| 实际改变计划次数 | {int(float(summary['changed_updates']))} / {int(float(summary['updates']))} |",
        f"| 电价预测日均MAE | {price_mae:.6f} 元/kWh |", "",
        "## 对照与选型结论", "",
        f"预声明84天Model Judge中，本方案相对0:00后不调整方案的库存修正费用降低{improve_no:.2%}，相对仅更新SOC但不使用最新光伏预报和余量的方案降低{improve_state:.2%}。连续残差场景候选在验证期和评估期均未超过本方案，日内价格前缀修正也未达到门槛，因此正式交付选择 rolling_margin_mean14。", "",
        f"Candidate A Scientific Validity：{'PASS' if selection['candidate_a_scientific_target_pass'] else 'FAIL'}；Candidate B Scientific Validity：{'PASS' if selection['candidate_b_scientific_target_pass'] else 'FAIL'}。这说明使用其他时刻光伏预报与SOC滚动调整有经济价值，但没有证据支持把复杂连续场景或日内价格修正升级为主模型。", "",
        "## 审计", "",
        f"独立审计复算{audit['counts']['dispatch_rows']:,}条10分钟执行记录和{audit['counts']['versions']:,}个计划版本。能量守恒、SOC、功率、光伏优先、费用、跨日连续、版本链和价格来源检查均PASS；目标日真实价格扰动不改变预测价格。", "",
        "结果是基于2025年历史轨迹的时间顺序回测，不是外部盲测。14日均价是经既有4.3模型选择保留的操作性基线，不声称为所有市场环境下的最优电价预测器。", ""
    ]
    (run / "model_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"report": str(run / "model_report.md"), "figures": 3}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
