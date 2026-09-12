"""Generate figures and final report for the frozen 334-day Task 4 SAA run."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

LABELS={"fixed":"固定价","mean7":"7日均价","mean14":"14日均价","saa_load":"SAA供需场景","oracle_actual":"实际价格下界"}


def rows(path):
    with path.open(encoding="utf-8-sig",newline="") as stream: return list(csv.DictReader(stream))


def configure():
    plt.rcParams.update({"font.sans-serif":["Microsoft YaHei","SimHei","Arial Unicode MS","DejaVu Sans"],"axes.unicode_minus":False,"figure.dpi":140,"savefig.dpi":320,"axes.grid":True,"grid.alpha":.25})


def save(fig,directory,name):
    fig.savefig(directory/f"{name}.png",bbox_inches="tight"); fig.savefig(directory/f"{name}.pdf",bbox_inches="tight"); plt.close(fig)


def report(experiment: Path) -> None:
    configure(); figdir=experiment/"figures"; annual=rows(experiment/"results/summary_tables.csv"); quarterly=rows(experiment/"results/quarterly_summary.csv"); monthly=rows(experiment/"results/monthly_summary.csv")
    selection=json.loads((experiment/"selection.json").read_text(encoding="utf-8")); audit=json.loads((experiment/"audit_summary.json").read_text(encoding="utf-8")); completion=json.loads((experiment/"completion.json").read_text(encoding="utf-8"))
    amap={r["method"]:r for r in annual}; qmap={(r["method"],r["period"]):r for r in quarterly}; mmap={(r["method"],r["period"]):r for r in monthly}
    months=sorted({r["period"] for r in monthly}); fig,ax=plt.subplots(figsize=(12,5.8))
    for method,marker in [("saa_load","o"),("mean7","s"),("mean14","^"),("fixed","D")]:
        vals=[float(mmap[(method,m)]["inventory_adjusted_cost"])/10000 for m in months]; ax.plot(months,vals,marker=marker,label=LABELS[method])
    ax.set_ylabel("月度库存调整费用（万元）"); ax.set_title("图1 334天各方法月度费用"); ax.tick_params(axis="x",rotation=35); ax.legend(); save(fig,figdir,"Fig1_FullMonthlyCost")
    quarters=sorted({r["period"] for r in quarterly}); fig,ax=plt.subplots(figsize=(9.5,5.8)); x=np.arange(len(quarters)); width=.34
    for off,base in [(-.5,"mean7"),(.5,"fixed")]:
        gains=[100*(float(qmap[(base,q)]["inventory_adjusted_cost"])-float(qmap[("saa_load",q)]["inventory_adjusted_cost"]))/float(qmap[(base,q)]["inventory_adjusted_cost"]) for q in quarters]
        ax.bar(x+off*width,gains,width,label=f"相对{LABELS[base]}")
    ax.axhline(0,color="#333",lw=.8); ax.axhline(.1,color="#777",lw=.8,ls="--"); ax.set_xticks(x,quarters); ax.set_ylabel("SAA费用改善（%）"); ax.set_title("图2 SAA季度稳定性"); ax.legend(); save(fig,figdir,"Fig2_FullQuarterlyRobustness")
    methods=["fixed","mean7","mean14","saa_load"]; fig,ax=plt.subplots(figsize=(9.5,5.8)); spill=[float(amap[m]["paid_grid_spill_kwh"]) for m in methods]; emergency=[float(amap[m]["emergency_kwh"]) for m in methods]
    ax.scatter(spill,emergency,s=85,c=np.arange(len(methods)),cmap="viridis")
    for xx,yy,m in zip(spill,emergency,methods): ax.annotate(LABELS[m],(xx,yy),xytext=(5,5),textcoords="offset points")
    ax.set_xlabel("全年已付未用电量（kWh）"); ax.set_ylabel("全年紧急购电量（kWh）"); ax.set_title("图3 全年风险电量对比"); save(fig,figdir,"Fig3_FullRiskEnergy")
    lines=[]
    for m in LABELS:
        r=amap[m]; lines.append(f"| {LABELS[m]} | {float(r['total_cost']):,.2f} | {float(r['inventory_adjusted_cost']):,.2f} | {float(r['emergency_kwh']):,.1f} | {float(r['paid_grid_spill_kwh']):,.1f} |")
    qlines=[]
    for q in quarters:
        p=float(qmap[("saa_load",q)]["inventory_adjusted_cost"]); m7=float(qmap[("mean7",q)]["inventory_adjusted_cost"]); fx=float(qmap[("fixed",q)]["inventory_adjusted_cost"]); qlines.append(f"| {q} | {p:,.2f} | {(m7-p)/m7*100:.3f}% | {(fx-p)/fx*100:.3f}% | {'通过' if selection['quarter_checks'][q]['within_worsening_tolerance'] else '未通过'} |")
    maxerr=max(v for k,v in audit["maximum_errors"].items() if k not in {"dispatch_count","information_count","information_failures"}); verdict="PASS" if selection["model_selection_pass"] else "FAIL"
    text=f"""# 问题4两阶段SAA 334天正式运行报告

# A 运行产物一览（路径）

正式运行覆盖2025-02-01至12-31，共334天、{completion['methods']}种方法、{completion['dispatch_rows']:,}条10分钟实际执行记录。主要文件包括 `results/summary_tables.csv`、`results/quarterly_summary.csv`、`results/monthly_summary.csv`、`dispatch.csv.gz`、`saa_plans.jsonl.gz`、`audit_summary.json` 和三张PNG/PDF图。

# B 核心结果（表格/指标/关键结论）

| 方法 | 现金支出/元 | 库存调整费用/元 | 紧急购电/kWh | 已付未用/kWh |
|---|---:|---:|---:|---:|
{chr(10).join(lines)}

| 季度 | SAA费用/元 | 相对7日均价 | 相对固定价 | 季度门槛 |
|---|---:|---:|---:|---|
{chr(10).join(qlines)}

冻结规则的最终判定为 **{verdict}**，交付方法为 **`{selection['selected_delivery']}`**。

# C 图表解读（每张图一句话：展示什么、说明什么）

- 图1展示各方法逐月库存调整费用，用于识别季节性失稳月份。
- 图2展示SAA相对两条基线的季度改善率，虚线为0.1%参考门槛。
- 图3同时比较紧急购电和已付未用电，判断费用改善是否以更高风险电量为代价。

# D 合理性检查与发现的问题

独立审计结论为 **{'PASS' if audit['pass'] else 'FAIL'}**。复算{completion['dispatch_rows']:,}条实际记录、{completion['saa_records']}个SAA计划及{completion['information_checks']}项未来信息检查；最大数值误差为{maxerr:.3e}。全年库存调整双基线检查为{selection['annual_adjusted_checks']}，现金支出检查为{selection['annual_cash_check']}，风险电量检查为{selection['risk_energy_check']}。

# E 微调记录（迭代清单：改动→原因→指标变化）

本轮没有根据334天结果微调。14日场景、0.8分位余量、储能参数、5倍紧急购电价格、名义层0--1互斥和全部裁决门槛均在运行前冻结。Cheap PoC阶段曾修正名义层LP退化和审计中紧急购电符号错误；两项修正均发生在全年协议冻结之前。

# F 最终版本说明（如何一键运行）

```powershell
D:\\Users\\python.exe final_run/q4_saa_full/main.py experiments/{experiment.name} --io-python C:\\Users\\14592\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe --model-python D:\\Users\\python.exe
```

# G 敏感性分析下一步计划（可执行清单 + 推荐优先级）

1. 高优先级：只在当前正式结果确认后，分别用7日和28日场景窗口做敏感性分析，不改变主结果。
2. 中优先级：对0.7、0.8、0.9余量分位进行离线敏感性分析，报告费用—紧急购电—弃电曲线。
3. 低优先级：将场景补救改成分时信息树，检验两阶段完全补救近似的乐观程度。

# H 评委视角：哪些图表/检验最加分，哪些最容易被扣分

加分点是全年连续推进储能状态、双基线和季度门槛预声明、未来价格扰动检查与独立物理复算。最容易被质疑的是14日场景代表性和场景内补救决策较理想化；论文应明确其为历史样本驱动的日前鲁棒近似，不把 `saa_load` 写成价格—供需联合机制模型。
"""
    (experiment/"model_report.md").write_text(text,encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("experiment",type=Path); args=parser.parse_args(); report(args.experiment.resolve())


if __name__ == "__main__": main()
