"""Write the concise model-judge report for an audited Question 4-3 PoC."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "no_update_mean14": "0时后不调整",
    "state_only_mean14": "仅状态反馈",
    "rolling_margin_mean14": "方向2余量基线",
    "rolling_scenario_mean14": "方向3连续场景",
    "rolling_scenario_shuffled": "时序打乱负对照",
    "rolling_scenario_price_update": "方向3加日内价格修正"
}


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main(run_arg: str) -> None:
    run = (ROOT / run_arg).resolve()
    audit = json.loads((run / "audit_summary.json").read_text(encoding="utf-8"))
    if not audit["pass"]:
        raise AssertionError("Audit must pass before reporting")
    selection = json.loads((run / "selection.json").read_text(encoding="utf-8"))
    summaries = rows(run / "results/summary_tables.csv")
    periods = rows(run / "results/periods.csv")
    table = ["| 方案 | 84天现金费用/元 | 库存修正费用/元 | 日费用CVaR90/元 | 紧急购电/kWh | 调整费/元 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in summaries:
        table.append(f"| {LABELS[row['strategy']]} | {float(row['total_cost']):,.2f} | "
            f"{float(row['inventory_adjusted_cost']):,.2f} | {float(row['daily_cvar90']):,.2f} | "
            f"{float(row['emergency_kwh']):,.2f} | {float(row['adjustment_cost']):,.2f} |")
    period_table = ["| 方案 | 验证期库存修正费用/元 | 评估期库存修正费用/元 |",
                    "| --- | ---: | ---: |"]
    for name in ["rolling_margin_mean14", "rolling_scenario_mean14", "rolling_scenario_shuffled",
                 "rolling_scenario_price_update"]:
        values = {row["period"]: row for row in periods if row["strategy"] == name}
        period_table.append(f"| {LABELS[name]} | {float(values['validation']['inventory_adjusted_cost']):,.2f} | "
            f"{float(values['evaluation']['inventory_adjusted_cost']):,.2f} |")
    a_verdict = "PASS" if selection["candidate_a_scientific_target_pass"] else "FAIL"
    b_verdict = "PASS" if selection["candidate_b_scientific_target_pass"] else "FAIL"
    chosen = selection["selected_for_human_model_gate"]
    lines = ["# 问题4-3方向3 Model Judge Cheap PoC 结果", "",
        "本轮按人工批准的双层目标和回退规则运行六个冻结策略、六个14日窗口，共84天。结果属于时间顺序回测；没有生成正式 `result4-3.xlsx`。", "",
        *table, "", *period_table, "",
        "## 裁决", "",
        f"- Algorithmic Verdict：**PASS**。独立审计复算{audit['counts']['dispatch_rows']:,}条执行记录和{audit['counts']['versions']:,}个计划版本。",
        f"- Candidate A `rolling_scenario_mean14` Scientific Validity：**{a_verdict}**。",
        f"- Candidate B `rolling_scenario_price_update` Scientific Validity：**{b_verdict}**。",
        f"- 按预声明规则进入HUMAN MODEL GATE的方案：`{chosen}`（{LABELS[chosen]}）。", "",
        "Candidate A必须在验证期和评估期均达到0.1%库存修正费用改善，或达到1% CVaR90改善且现金费用不恶化0.1%以上，并在两期优于时序打乱负对照。Candidate B还必须相对A达到同一门槛并通过未来价格扰动测试。", "",
        "## 诊断范围", "",
        "- 真实结算使用附件4逐时价格；计划只使用此前14日价格，价格增强只读取当日已实现前缀。",
        "- 时序打乱负对照逐时保留8个残差样本的边际分布，只破坏跨时段路径关联。",
        "- 各窗口从同一问题3方向3状态开始，每个策略在窗口内连续推进SOC；库存差异单独修正。",
        "- 联合价格—供需情景未重跑，因为问题4-2已有负对照将其结构性淘汰。", "",
        "最终模型仍需HUMAN MODEL GATE确认；PoC数值不能直接写入官方全年工作簿。", ""]
    (run / "model_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(chosen)


if __name__ == "__main__":
    main(sys.argv[1])
