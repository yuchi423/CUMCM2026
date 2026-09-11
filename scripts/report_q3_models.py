"""Create the concise Q3 model report from audited summary outputs."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = {"no_update": "仅0时预报", "state_only": "状态反馈对照",
          "rolling_point": "方向1：点预报滚动", "rolling_margin": "方向2：提前期余量",
          "rolling_scenario": "方向3：连续误差场景", "rolling_point_step": "点预报阶梯映射"}


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def money(value):
    return f"{float(value) / 1e4:,.2f}"


def main(run_arg):
    run = ROOT / run_arg
    audit = json.loads((run / "audit_summary.json").read_text(encoding="utf-8"))
    if not audit["pass_"]:
        raise AssertionError("Audit must pass before reporting")
    summary = rows(run / "results/summary_tables.csv")
    periods = rows(run / "results/periods.csv")
    lookup = {r["strategy"]: r for r in summary}
    directions = ["rolling_point", "rolling_margin", "rolling_scenario"]
    best = min(directions, key=lambda x: float(lookup[x]["inventory_adjusted_cost"]))
    base = lookup["no_update"]

    table = ["| 方案 | 普通购电/万元 | 调整费/万元 | 紧急购电/万元 | 总费用/万元 | 期末SOC/kWh | 库存调整费用/万元 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in summary:
        table.append(f"| {LABELS[r['strategy']]} | {money(r['ordinary_cost'])} | {money(r['adjustment_cost'])} | "
                     f"{money(r['emergency_cost'])} | {money(r['total_cost'])} | {float(r['e_end']):,.2f} | "
                     f"{money(r['inventory_adjusted_cost'])} |")

    period_table = ["| 路线 | 2—4月 | 5—8月 | 9—12月 |", "| --- | ---: | ---: | ---: |"]
    for name in directions:
        values = [r for r in periods if r["strategy"] == name]
        period_table.append(f"| {LABELS[name]} | " + " | ".join(money(r["total_cost"]) for r in values) + " |")

    def delta(a, b, field="total_cost"):
        return float(lookup[a][field]) - float(lookup[b][field])

    lines = ["# 第三问三路线模型实测报告", "",
        "## A 运行产物一览", "",
        "一键入口为`final_run/q3/main.py`；配置为`configs/q3_models.json`。必要结果在`results/`，"
        "三张论文候选图在`figures/`，独立审计为`audit_summary.json`。完整逐时账本仅留在本地`details/`，可由入口重建。", "",
        "## B 核心结果", "", *table, "",
        f"三条正式方向中，按库存调整费用选择的本轮最佳路线是**{LABELS[best]}**。相对仅0时预报，现金费用变化"
        f"{delta('no_update', best) / 1e4:,.2f}万元，库存调整口径变化{delta('no_update', best, 'inventory_adjusted_cost') / 1e4:,.2f}万元。", "",
        f"状态反馈对照相对不调整变化{delta('no_update', 'state_only') / 1e4:,.2f}万元；"
        f"使用最新PV点预报相对状态反馈对照变化{delta('state_only', 'rolling_point') / 1e4:,.2f}万元。"
        "后一个差值更接近新增PV预报的净价值，但仍包含由预报引起的计划联动。", "", *period_table, "",
        "## C 图表解读", "",
        "- `Fig1_Q3_TotalCost`比较控制组、三条方向和插值敏感性的334天现金费用。",
        "- `Fig2_Q3_CostBreakdown`显示普通购电、逐次调整和5倍紧急购电三类成本，判断降费是否只是转移账目。",
        "- `Fig3_Q3_MonthlyCost`检验优势是否只集中在少数月份。", "",
        "## D 合理性检查与发现的问题", "",
        f"独立审计通过：{audit['counts'].get('dispatch_rows', 0):,}条实际执行记录重算能量、SOC、费用和跨日连续，"
        f"{audit['counts'].get('updates', 0):,}次计划版本逐项用旧/新计划重算调整费。"
        "附件3的1460个发布键唯一且24小时值完整；未来负载扰动不改变当日历史预测。", "",
        "计划生成内部要求预测日末不低于1200kWh，但真实期末由因果执行决定，因此比较同时报告现金和库存调整费用。"
        "整点预报到10分钟的线性插值不是题面唯一解释，阶梯映射作为独立敏感性。夜间PV接近零，未使用MAPE。", "",
        "## E 微调记录", "",
        "- 在正式运行前加入“保留上一版计划”候选：避免硬终端目标把修改计划变成强制行为；该修正属于有效性要求，未用全年费用决定。",
        "- 场景方向由允许场景内提前看见未来的理想调度，改为连续历史残差生成固定购电候选、再用同一因果执行器评分；避免预见性偏差。",
        f"- 对预报映射增加阶梯敏感性：相对线性点预报，现金费用变化{delta('rolling_point', 'rolling_point_step') / 1e4:,.2f}万元。"
        "本轮没有看到结果后重新选择0.8分位、8条场景或风险权重。", "",
        "## F 最终版本说明", "",
        "在仓库根目录使用项目现有Python依赖运行：", "",
        "```text", "python final_run/q3/main.py experiments/<新的运行ID>", "```", "",
        "入口依次运行全部路线、独立审计和报告。输出目录必须不存在，防止覆盖历史证据。", "",
        "## G 敏感性分析下一步计划", "",
        "1. 优先复核线性/阶梯预报映射及指定日期曲线；若结论翻转，先确定口径。",
        "2. 在不改测试期的前提下，用2—4月比较少量余量分位，再锁定参数检查5—8月与9—12月。",
        "3. 仅当场景方向显示稳定潜力时，再扩大场景数并改变风险权重；不直接开展设备参数敏感性。", "",
        "## H 评委视角", "",
        "最有说服力的是调整权限、当前SOC反馈和新PV预报三者被分开对照，且每笔修改费用可回放。"
        "容易扣分的是把整点预报插值写成题面事实、只报现金而隐藏期末SOC，或把时间回测称为外部盲测。", "",
        "本轮属于已经接触该年度供需后的时间顺序回测；三条路线均真实运行，但尚未据此批准论文最终模型。", ""]
    (run / "model_report.md").write_text("\n".join(lines), encoding="utf-8")
    (run / "selection.json").write_text(json.dumps({"selected_by_inventory_adjusted_cost": best,
        "directions": directions, "controls": ["no_update", "state_only"],
        "sensitivity": ["rolling_point_step"],
        "status": "computed candidate; not final paper approval"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(best)


if __name__ == "__main__":
    main(sys.argv[1])
