"""Generate figures and the model report for the audited Q4-3 direction-3 run."""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
WIDTH, HEIGHT = 2800, 1500


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def font(size: int, bold: bool = False):
    candidate = Path(r"C:\Windows\Fonts\msyhbd.ttc") if bold else FONT_PATH
    return ImageFont.truetype(str(candidate if candidate.exists() else FONT_PATH), size)


def save_image(image: Image.Image, base: Path) -> None:
    png = base.with_suffix(".png"); pdf = base.with_suffix(".pdf")
    image.save(png, dpi=(320, 320))
    doc = canvas.Canvas(str(pdf), pagesize=(WIDTH, HEIGHT))
    doc.drawImage(ImageReader(image), 0, 0, WIDTH, HEIGHT)
    doc.showPage(); doc.save()


def axes(draw: ImageDraw.ImageDraw, title: str, ylabel: str):
    left, top, right, bottom = 250, 170, 2670, 1250
    draw.text((WIDTH / 2, 45), title, font=font(66, True), fill="#263746", anchor="ma")
    draw.line((left, top, left, bottom), fill="#263746", width=4)
    draw.line((left, bottom, right, bottom), fill="#263746", width=4)
    draw.text((left, top - 25), ylabel, font=font(36), fill="#263746", anchor="ls")
    return left, top, right, bottom


def scale_y(value, maximum, top, bottom):
    return bottom - (value / maximum) * (bottom - top)


def draw_grid(draw, maximum, left, top, right, bottom):
    for k in range(6):
        value = maximum * k / 5; y = scale_y(value, maximum, top, bottom)
        draw.line((left, y, right, y), fill="#D9E0E5", width=2)
        draw.text((left - 25, y), f"{value:.0f}", font=font(32), fill="#52616B", anchor="rm")


def line_chart(base: Path, labels, series, title, ylabel):
    image = Image.new("RGB", (WIDTH, HEIGHT), "white"); draw = ImageDraw.Draw(image)
    left, top, right, bottom = axes(draw, title, ylabel)
    maximum = max(max(values) for _, values, _ in series) * 1.08
    draw_grid(draw, maximum, left, top, right, bottom)
    xs = np.linspace(left + 80, right - 80, len(labels))
    for _, values, color in series:
        points = [(float(x), scale_y(value, maximum, top, bottom)) for x, value in zip(xs, values)]
        draw.line(points, fill=color, width=8)
        for x, y in points: draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=color)
    for x, label in zip(xs, labels):
        draw.text((x, bottom + 35), label, font=font(34), fill="#263746", anchor="ma")
    legend_x = left + 40
    for label, _, color in series:
        draw.line((legend_x, 1360, legend_x + 90, 1360), fill=color, width=9)
        draw.text((legend_x + 110, 1360), label, font=font(34), fill="#263746", anchor="lm")
        legend_x += 720
    save_image(image, base)


def stacked_chart(base: Path, labels, stacks, title, ylabel):
    image = Image.new("RGB", (WIDTH, HEIGHT), "white"); draw = ImageDraw.Draw(image)
    left, top, right, bottom = axes(draw, title, ylabel)
    totals = [sum(values[i] for _, values, _ in stacks) for i in range(len(labels))]
    maximum = max(totals) * 1.08; draw_grid(draw, maximum, left, top, right, bottom)
    band = (right - left) / len(labels); bar_width = band * 0.58
    for i, label in enumerate(labels):
        x0 = left + i * band + (band - bar_width) / 2; x1 = x0 + bar_width; accumulated = 0.0
        for _, values, color in stacks:
            y0 = scale_y(accumulated, maximum, top, bottom); accumulated += values[i]
            y1 = scale_y(accumulated, maximum, top, bottom); draw.rectangle((x0, y1, x1, y0), fill=color)
        draw.text(((x0 + x1) / 2, bottom + 35), label, font=font(34), fill="#263746", anchor="ma")
    legend_x = left + 20
    for label, _, color in stacks:
        draw.rectangle((legend_x, 1338, legend_x + 42, 1380), fill=color)
        draw.text((legend_x + 60, 1360), label, font=font(34), fill="#263746", anchor="lm")
        legend_x += 600
    save_image(image, base)


def combined_chart(base: Path, labels, bars, line, title):
    image = Image.new("RGB", (WIDTH, HEIGHT), "white"); draw = ImageDraw.Draw(image)
    left, top, right, bottom = axes(draw, title, "紧急购电量（kWh）")
    bar_max = max(bars) * 1.1 if max(bars) else 1.0; line_max = max(line) * 1.1 if max(line) else 1.0
    draw_grid(draw, bar_max, left, top, right, bottom)
    band = (right - left) / len(labels); width = band * 0.55; xs = []
    for i, (label, value) in enumerate(zip(labels, bars)):
        x0 = left + i * band + (band - width) / 2; x1 = x0 + width; xs.append((x0 + x1) / 2)
        draw.rectangle((x0, scale_y(value, bar_max, top, bottom), x1, bottom), fill="#E45756")
        draw.text(((x0 + x1) / 2, bottom + 35), label, font=font(34), fill="#263746", anchor="ma")
    points = [(x, scale_y(value, line_max, top, bottom)) for x, value in zip(xs, line)]
    draw.line(points, fill="#3B7EA1", width=8)
    for x, y in points: draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill="#3B7EA1")
    for k in range(6):
        value = line_max * k / 5; y = scale_y(value, line_max, top, bottom)
        draw.text((right + 25, y), f"{value:.3f}", font=font(32), fill="#3B7EA1", anchor="lm")
    draw.text((right, top - 25), "电价预测MAE（元/kWh）", font=font(36), fill="#3B7EA1", anchor="rs")
    draw.rectangle((left + 10, 1338, left + 52, 1380), fill="#E45756")
    draw.text((left + 70, 1360), "紧急购电量", font=font(34), fill="#263746", anchor="lm")
    draw.line((left + 500, 1360, left + 590, 1360), fill="#3B7EA1", width=8)
    draw.text((left + 610, 1360), "电价预测MAE", font=font(34), fill="#263746", anchor="lm")
    save_image(image, base)


def main(run_arg: str) -> None:
    run = (ROOT / run_arg).resolve()
    audit = json.loads((run / "audit_summary.json").read_text(encoding="utf-8"))
    if not audit["pass"]: raise AssertionError("Audit must pass before reporting")
    completion = json.loads((run / "completion.json").read_text(encoding="utf-8"))
    solver = json.loads((run / "solver_audit_summary.json").read_text(encoding="utf-8"))
    summary = rows(run / "results/summary_tables.csv")[0]; monthly = rows(run / "results/monthly_summary.csv")
    price = rows(run / "results/price_metrics_daily.csv")
    old_run = ROOT / "experiments/q4-3-full-20260913-02"
    old_summary = rows(old_run / "results/summary_tables.csv")[0]
    old_monthly = rows(old_run / "results/monthly_summary.csv")
    figures = run / "figures"; figures.mkdir(exist_ok=True)
    labels = [row["period"][5:] for row in monthly]
    main_month = [float(row["total_cost"]) / 1e4 for row in monthly]
    old_month = [float(row["total_cost"]) / 1e4 for row in old_monthly]
    line_chart(figures / "Fig1_Q4_3_MonthlyComparison", labels,
        [("方向3 连续残差情景", main_month, "#6A51A3"), ("余量滚动对照", old_month, "#718A59")],
        "问题4-3逐月实际费用比较", "费用（万元）")
    ordinary = [float(row["ordinary_cost"]) / 1e4 for row in monthly]
    adjustment = [float(row["adjustment_cost"]) / 1e4 for row in monthly]
    emergency = [float(row["emergency_cost"]) / 1e4 for row in monthly]
    stacked_chart(figures / "Fig2_Q4_3_CostBreakdown", labels,
        [("普通购电费", ordinary, "#4C78A8"), ("调整费", adjustment, "#F2CF5B"),
         ("紧急购电费", emergency, "#E45756")], "方向3逐月实际费用分解", "费用（万元）")
    by_month = defaultdict(list)
    for row in price: by_month[row["date"][:7]].append(float(row["mae"]))
    mae = [float(np.mean(by_month[f"2025-{month:02d}"])) for month in range(2, 13)]
    emergency_kwh = [float(row["emergency_kwh"]) for row in monthly]
    combined_chart(figures / "Fig3_Q4_3_EmergencyAndPriceMAE", labels, emergency_kwh, mae,
                   "逐月紧急购电量与因果电价预测误差")

    s = {k: float(v) if k not in {"strategy", "period"} else v for k, v in summary.items()}
    b = {k: float(v) if k not in {"strategy", "period"} else v for k, v in old_summary.items()}
    saving = b["total_cost"] - s["total_cost"]
    adjusted_saving = b["inventory_adjusted_cost"] - s["inventory_adjusted_cost"]
    price_mae = float(np.mean([float(row["mae"]) for row in price]))
    runtime = json.loads((run / "run.json").read_text(encoding="utf-8"))
    report = f"""# 问题4-3：因果电价下的连续残差情景滚动模型

## A 运行产物一览（路径）

本次实验位于`{run.relative_to(ROOT).as_posix()}`。`results/`保存334天汇总、逐日账本、分月分季度统计和指定日期结果，`figures/`保存三张PNG/PDF论文图，`details/`保存52,560条10分钟执行记录及1,460个计划版本，官方工作簿由同一账本生成。

## B 核心结果（表格/指标/关键结论）

| 指标 | 方向3正式模型 | 余量滚动对照 |
| --- | ---: | ---: |
| 普通购电费/元 | {s['ordinary_cost']:,.2f} | {b['ordinary_cost']:,.2f} |
| 调整费/元 | {s['adjustment_cost']:,.2f} | {b['adjustment_cost']:,.2f} |
| 紧急购电费/元 | {s['emergency_cost']:,.2f} | {b['emergency_cost']:,.2f} |
| 实际总费用/元 | **{s['total_cost']:,.2f}** | {b['total_cost']:,.2f} |
| 库存修正费用/元 | {s['inventory_adjusted_cost']:,.2f} | {b['inventory_adjusted_cost']:,.2f} |
| 334天日费用CVaR90/元 | {s['daily_cvar90']:,.2f} | — |
| 紧急购电量/kWh | {s['emergency_kwh']:,.2f} | {b['emergency_kwh']:,.2f} |
| 期初/期末储电量/kWh | {s['e_start']:,.2f}/{s['e_end']:,.2f} | {b['e_start']:,.2f}/{b['e_end']:,.2f} |

方向3相对旧余量对照少支出{saving:,.2f}元（{saving / b['total_cost']:.2%}），库存修正后少支出{adjusted_saving:,.2f}元。这个全年回放结果支持方向3作为当前交付模型，但属于2025年历史轨迹回测，不是未来年份外部验证。

问题4-2当前交付的mean7费用为14,550,695.47元，与本方案相差118,415.66元，但两问的初始SOC、价格预测窗口、供需风险模型和日内调整权限均不同，因此不能把该差额解释为“新增日内预报的收益”。问题4-2修复后SAA全年费用14,523,376.59元，虽低于mean7，但第三季度相对较优基线恶化0.37788%，超过团队预设的0.1%稳定性门槛，因此仍选择mean7；该门槛不是题设约束。

模型在0:00、6:00、12:00、18:00更新。负荷由过去同星期轨迹预测，光伏读取附件3当次发布的预测；过去连续净负荷残差片段形成点预测、0.8分位余量和8条历史残差候选计划。每条候选在同一情景集合下计算库存修正费用，评分为情景均值加0.25倍“CVaR90减均值”，并保留上一版计划作为候选。实际调整费逐次比较上一版尚未执行后缀，避免重复结算。

问题4新增价格信息：1月1日使用附件1价格曲线冷启动，此后每天使用最多14个已结束日的同刻均价预测，日内不读取当天未来真实价格。普通购电、调整和紧急购电均以交付时刻附件4真实价格结算。334天电价预测日均MAE为{price_mae:.6f}元/kWh。

## C 图表解读（每张图一句话）

- `Fig1_Q4_3_MonthlyComparison`：逐月比较方向3与旧余量滚动对照的现金费用，显示全年差额来自多个季节而非单日异常。
- `Fig2_Q4_3_CostBreakdown`：分解普通购电、调整和紧急购电费用，展示风险控制的实际代价结构。
- `Fig3_Q4_3_EmergencyAndPriceMAE`：并列显示紧急购电量与电价预测误差，避免把预测精度直接等同于调度费用。

## D 合理性检查与发现的问题

独立审计复算{audit['counts']['dispatch_rows']:,}条区间记录和{audit['counts']['versions']:,}个计划版本。原始负荷、光伏和电价逐格一致；能量守恒、0.9充放电效率、1200—10800 kWh边界、5000 kW功率限制、光伏优先、跨日连续、版本链、逐次调整费和计划版本到实际执行映射均通过。未来当天价格和负荷扰动不改变当时预测，因果边界通过。最大能量状态误差为{audit['max_errors']['state']:.3e} kWh。

当前规划窗口沿用问题3的当日剩余时段。附件3提供的跨日24小时信息尚未纳入正式计划，故本结果不能证明跨午夜决策最优；这是后续最需要补的结构性敏感性。旧84天Model Judge曾显示方向3相对余量方案成本略高，而本次334天同起点比较转为节省，说明短窗口结论不应外推到全年。

当前账本只验证逐次计费：每轮相对上一版有效计划的未执行后缀收取调整费。尚未运行“初始计划与最终计划只比较一次”的备选结算口径，不能写成两种口径都已验证。全年结果也不能单独证明收益来自残差时间相关性；若要做机制归因，仍需在相同起点和信息集下完成时序打乱对照。

## E 微调记录（改动→原因→效果）

- 将旧正式模型`rolling_margin_mean14`改为`rolling_scenario_mean14`：落实跨问题统一模型；全年总费用减少{saving:,.2f}元，紧急购电量减少{b['emergency_kwh'] - s['emergency_kwh']:,.2f} kWh，调整费增加{s['adjustment_cost'] - b['adjustment_cost']:,.2f}元。
- 从1月1日6000 kWh连续热身：闭合题设初始条件；2月1日自然得到{s['e_start']:,.2f} kWh，避免人为指定正式期初库存。
- 修正季度分组为自然季度：防止2月起始导致季度标签错位；不改变逐日费用。
- 新增原始输入和计划版本到执行的独立复算：扩大审计范围；所有检查通过。
- 修正经验CVaR的边界样本权重：334天最差10%按最贵33天加第34天40%计算，CVaR90为{s['daily_cvar90']:,.2f}元；购电计划与实际总费用不变。

## F 最终版本说明（如何一键运行）

运行`python final_run/q4_3_scenario/main.py experiments/<新的运行ID>`。入口顺序执行模型、独立审计、图表与报告、官方模板填充和工作簿回读核验；旧实验目录不覆盖。实际环境为Python {runtime['python']}，NumPy {runtime['numpy']}，SciPy {runtime['scipy']}。

## G 敏感性分析下一步计划（可执行清单 + 推荐优先级）

1. 高优先级：把6:00、12:00、18:00的预测窗口扩展至未来24小时，仅对当日已申报后缀计调整费，比较现金费用、紧急购电和计算时间。
2. 高优先级：固定其他模块，比较情景数4/8/12与风险权重0/0.25/0.5，按时间分块验证，避免用全年结果反向选参。
3. 中优先级：比较7/14/28日电价同槽均值及简单AR模型，继续使用真实价结算，并报告决策费用而非只报MAE。
4. 中优先级：比较贪心执行与价格感知储能控制；要求同一信息边界、同一起始SOC和同一计划版本链。

## H 评委视角

加分证据是模型从问题3到问题4保持同一情景滚动骨架、价格信息边界可复算、1月初始条件闭合、52,560条物理轨迹和官方模板逐格映射。容易扣分的是把附件4全年数据写成决策时已知、把历史回测称为未来验证、只报总费用而不报期末库存，以及把方向3的复杂度本身当作优越性证明。
"""
    (run / "model_report.md").write_text(report, encoding="utf-8")
    print(json.dumps({"report": str(run / "model_report.md"), "figures": 3,
                      "saving_vs_margin": saving, "runtime_seconds": completion["runtime_seconds"],
                      "solve_count": solver["solve_count"]}, ensure_ascii=False))


if __name__ == "__main__": main(sys.argv[1])
