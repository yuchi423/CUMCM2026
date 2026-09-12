"""Generate paper-ready figures and report for the Task 4 SAA PoC."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

LABELS = {"fixed":"固定价", "mean7":"7日均价", "mean14":"14日均价", "saa_load":"SAA供需场景", "saa_joint":"SAA联合场景", "saa_shuffled":"SAA错配对照", "oracle_actual":"实际价格上界"}
PERIODS = {"development":"开发期", "validation":"验证期", "evaluation":"评估期"}


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream: return list(csv.DictReader(stream))


def configure():
    plt.rcParams.update({"font.sans-serif":["Microsoft YaHei","SimHei","Arial Unicode MS","DejaVu Sans"],"axes.unicode_minus":False,"figure.dpi":140,"savefig.dpi":320,"axes.grid":True,"grid.alpha":.25})


def save(fig, directory, name):
    fig.savefig(directory/f"{name}.png",bbox_inches="tight"); fig.savefig(directory/f"{name}.pdf",bbox_inches="tight"); plt.close(fig)


def report(experiment: Path):
    configure(); figdir=experiment/"figures"; periods=rows(experiment/"results/periods.csv"); summaries=rows(experiment/"results/summary_tables.csv"); daily=rows(experiment/"results/daily_summary.csv"); selection=json.loads((experiment/"selection.json").read_text(encoding="utf-8")); audit=json.loads((experiment/"audit_summary.json").read_text(encoding="utf-8")); completion=json.loads((experiment/"completion.json").read_text(encoding="utf-8"))
    methods=list(LABELS); pm={(r["method"],r["period"]):r for r in periods}; sm={r["method"]:r for r in summaries}
    x=np.arange(len(methods)); width=.24; fig,ax=plt.subplots(figsize=(13.5,5.8))
    for off,p in enumerate(["development","validation","evaluation"]):
        base=float(pm[("mean7",p)]["inventory_adjusted_cost"]); vals=[(float(pm[(m,p)]["inventory_adjusted_cost"])-base)/10000 for m in methods]; ax.bar(x+(off-1)*width,vals,width,label=PERIODS[p])
    ax.axhline(0,color="#333",lw=.8); ax.set_xticks(x,[LABELS[m] for m in methods],rotation=22,ha="right"); ax.set_ylabel("相对7日均价的费用变化（万元）"); ax.set_title("图1 两阶段样本平均近似的分期费用"); ax.legend(); save(fig,figdir,"Fig1_SAACostByPeriod")
    fig,ax=plt.subplots(figsize=(9.6,5.8)); chosen=["fixed","mean7","mean14","saa_load","saa_joint","saa_shuffled","oracle_actual"]; em=[float(sm[m]["emergency_kwh"]) for m in chosen]; spill=[float(sm[m]["paid_grid_spill_kwh"]) for m in chosen]; ax.scatter(spill,em,s=70,c=np.arange(len(chosen)),cmap="viridis")
    for xx,yy,m in zip(spill,em,chosen): ax.annotate(LABELS[m],(xx,yy),xytext=(4,4),textcoords="offset points",fontsize=9)
    ax.set_xlabel("已付未用电量（kWh）"); ax.set_ylabel("紧急购电量（kWh）"); ax.set_title("图2 费用风险与储能保守程度"); save(fig,figdir,"Fig2_SAAEmergencyVsSpill")
    fig,ax=plt.subplots(figsize=(9.8,5.8)); dev=[float(pm[(m,"development")]["inventory_adjusted_cost"])/10000 for m in ["saa_load","saa_joint","saa_shuffled"]]; val=[float(pm[(m,"validation")]["inventory_adjusted_cost"])/10000 for m in ["saa_load","saa_joint","saa_shuffled"]]; ev=[float(pm[(m,"evaluation")]["inventory_adjusted_cost"])/10000 for m in ["saa_load","saa_joint","saa_shuffled"]]; xx=np.arange(3); ax.plot(xx,dev,"o-",label="开发期"); ax.plot(xx,val,"s-",label="验证期"); ax.plot(xx,ev,"^-",label="评估期"); ax.set_xticks(xx,[LABELS[m] for m in ["saa_load","saa_joint","saa_shuffled"]]); ax.set_ylabel("库存调整费用（万元）"); ax.set_title("图3 SAA场景关联性的跨期检验"); ax.legend(); save(fig,figdir,"Fig3_SAAScenarioRobustness")
    lines=[]
    for m in methods: lines.append(f"| {LABELS[m]} | {float(sm[m]['inventory_adjusted_cost']):,.2f} | {float(sm[m]['emergency_kwh']):,.1f} | {float(sm[m]['paid_grid_spill_kwh']):,.1f} |")
    later=[]
    for p in ["validation","evaluation"]:
        q=selection["later_checks"][p]; later.append(f"- {PERIODS[p]}：选中 `{selection['selected']}` {q['selected_cost']:,.2f} 元；7日均价 {q['mean7_cost']:,.2f} 元；固定价 {q['fixed_cost']:,.2f} 元；错配对照 {q['shuffled_cost']:,.2f} 元。")
    verdict="PASS" if selection["scientific_target_pass"] else "FAIL"; maxerr=max(a for k,a in audit["maximum_errors"].items() if k not in {"mutual_count","information_failures","information_checks","dispatch_rows"})
    text=f"""# 问题4实时电价下两阶段样本平均近似 Cheap PoC 报告

## A 运行范围

本轮比较 {completion['methods']} 种方法，每种 {completion['days_per_method']} 天，共 {completion['dispatch_rows']:,} 条实际逐时记录；SAA 方法使用最近14个已完成日期作为场景，并让普通购电计划 `g_t` 在场景间共享，充放电、弃电和紧急购电作为场景补救变量。名义场景沿用问题二 `combined` 的预测和0.8分位余量，首末储电量和功率边界均保留。

## B 费用结果

开发期候选原始最优为 `{selection['raw_best']}`，在0.5%容差内选中 **`{selection['selected']}`**。

| 方法 | 84天库存调整费用/元 | 紧急购电/kWh | 已付未用/kWh |
|---|---:|---:|---:|
{chr(10).join(lines)}

{chr(10).join(later)}

预声明的费用目标判定为 **{verdict}**：验证期和评估期都要求相对7日均价、固定价至少改善0.1%。

## C 结果解释

图1给出跨期费用；图2把紧急购电和已付未用电放在同一坐标中，显示共享计划是否通过增加预购电降低短缺；图3检验供需与价格残差的联合关联是否在未见数据上兑现。`saa_shuffled` 是价格残差错位的负对照，不参与候选选择。

SAA 的统计含义是：在目标日开始时，只用历史14天构造经验场景，先决定跨场景一致的普通购电量，再允许每个历史场景单独补救。它比“先选一个价格曲线再求确定性计划”更接近不确定性决策，但仍是有限历史场景的近似，不能声称得到真实分布下的全局最优。

## D 独立审计

独立审计为 **{'PASS' if audit['pass'] else 'FAIL'}**；重新检查实际账本、名义场景、{completion['saa_records']} 个SAA记录及未来价格扰动。最大数值误差为 {maxerr:.3e}，信息检查失败 {audit['maximum_errors'].get('information_failures',0)} 项，实际记录数偏差 {audit['maximum_errors'].get('dispatch_rows',0)}。

## E 可交付边界

算法与数据审计通过即可作为方法复核材料；费用目标为 **{verdict}**，因此本轮不能把SAA宣称为最终费用优选模型。若论文需要主方案，应保留已确认的 `combined` 物理口径，并把SAA作为随机决策复核或失败证据，除非后续有新的、预先登记的模型路线通过同一验证门槛。

## F 一键复现

```powershell
python final_run/q4_saa_poc/main.py experiments/{experiment.name} --io-python C:\\Users\\14592\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe
```
"""
    (experiment/"model_report.md").write_text(text,encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("experiment",type=Path); args=parser.parse_args(); report(args.experiment.resolve())


if __name__ == "__main__": main()
