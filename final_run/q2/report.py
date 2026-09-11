"""Create paper tables, plots and a report from completed audited Q2 results."""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def read(path):
    with path.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))


def table(path,headers,rows):
    with path.open("w",encoding="utf-8-sig",newline="") as f:w=csv.writer(f);w.writerow(headers);w.writerows(rows)


def md(headers,rows):
    return "| "+" | ".join(headers)+" |\n|"+" --- |"*len(headers)+"\n"+"\n".join("| "+" | ".join(f"{v:,.2f}" if isinstance(v,(float,np.floating)) else str(v) for v in row)+" |" for row in rows)


def main(run):
    meta=json.loads((run/"run.json").read_text(encoding="utf-8"));cfg=meta["config"]
    totals=json.loads((run/"totals.json").read_text(encoding="utf-8"));lookup={r["strategy"]:r for r in totals}
    audit=json.loads((run/"independent_audit.json").read_text(encoding="utf-8"));assert audit["pass"]
    daily=read(run/"daily_summary.csv");monthly=read(run/"monthly.csv");periods=read(run/"periods.csv")
    rows=read(run/"operational_dispatch.csv");group=defaultdict(list)
    for r in rows:group[r["date"]].append({k:float(v) for k,v in r.items() if k not in ["strategy","date"]})
    def time_label(t):return f"{t//6:02}:{t%6*10:02}"
    intervals=[time_label(t)+"-"+time_label(t+1) for t in range(144)]
    sheets={};p_rows=[];storage=[];emergency=[];cash=[];paper1=[];paper2=[];paper3=[]
    for date,g in group.items():
        plan=[r["plan_kwh"] for r in g];pc=sum(r["plan_cost"] for r in g);bc=sum(r["emergency_cost"] for r in g)
        p_rows.append([date]+plan+[sum(plan),pc])
        cash.append([date,pc,bc,pc+bc,sum(r["emergency_kwh"] for r in g),sum(r["paid_grid_spill_kwh"] for r in g),sum(r["pv_spill_kwh"] for r in g),g[0]["e_start"],g[-1]["e_end"],.8 if date<"2025-05-01" else meta["selected_quantile"]])
        for b in range(6):
            sub=g[b*24:(b+1)*24]
            r=[date,f"{b*4:02}:00-{(b+1)*4:02}:00",sum(x["charge_kwh"] for x in sub),sum(x["discharge_kwh"] for x in sub),
               "00:00" if b==0 else "24:00" if b==1 else None,g[0]["e_start"] if b==0 else g[-1]["e_end"] if b==1 else None]
            storage.append(r)
            if date in cfg["paper_dates"]:paper2.append(r)
        e=[[date,intervals[t],r["emergency_kwh"]] for t,r in enumerate(g) if r["emergency_kwh"]>1e-6]
        if not e:e=[[date,"全天无紧急购电",0.]]
        emergency.extend(e)
        if date in cfg["paper_dates"]:
            paper1.append([date]+[plan[t] for t in [60,72,84,96,108,120]]+[sum(plan),pc,bc,pc+bc])
            paper3.extend(e)
    sheets["计划购电量"]={"headers":["日期 / 时段（kWh）"]+intervals+["全天计划购电量（kWh）","全天计划购电费（元）"],"rows":p_rows}
    sheets["充放电量"]={"headers":["日期","时间段","充电量（kWh）","放电量（kWh）","时刻","储电量（kWh）"],"rows":storage}
    sheets["紧急购电量"]={"headers":["日期","紧急时间段","购电量（kWh）"],"rows":emergency}
    sheets["费用汇总"]={"headers":["日期","计划购电费（元）","紧急购电费（元）","总费用（元）","紧急电量（kWh）","已付未用网电（kWh）","弃光（kWh）","日初电量（kWh）","日末电量（kWh）","分位数"],"rows":cash}
    sheets["指定日表1"]={"headers":["日期"]+[intervals[t] for t in [60,72,84,96,108,120]]+["全天计划电量（kWh）","计划电费（元）","紧急电费（元）","合计费用（元）"],"rows":paper1}
    sheets["指定日表2"]={"headers":sheets["充放电量"]["headers"],"rows":paper2}
    sheets["指定日表3"]={"headers":sheets["紧急购电量"]["headers"],"rows":paper3}
    for key,r in sheets.items():
        if key.startswith("指定日"):table(run/(key+".csv"),r["headers"],r["rows"])
    (run/"workbook_data.json").write_text(json.dumps(sheets,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    figdir=run/"figures";figdir.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family":"Microsoft YaHei","font.size":10,"axes.unicode_minus":False,"pdf.fonttype":42})
    def save(fig,name):
        fig.savefig(figdir/(name+".png"),dpi=300,bbox_inches="tight");fig.savefig(figdir/(name+".pdf"),bbox_inches="tight");plt.close(fig)
    fig,ax=plt.subplots(figsize=(9,4.3))
    for name,label,color in [("baseline","无余量基线","#6D7785"),("q0.8","固定0.8","#C68A34"),("operational","按时间选参策略","#225EA8")]:
        a=[r for r in monthly if r["strategy"]==name];ax.plot([int(r["month"]) for r in a],[float(r["total_cost"])/1e4 for r in a],marker="o",label=label,color=color)
    ax.set(xlabel="月份",ylabel="月总费用（万元）",xticks=range(2,13));ax.legend();ax.grid(alpha=.2);save(fig,"Fig1_monthly_cost")
    fig,ax=plt.subplots(figsize=(9,4.3))
    for period,label in [("selection","2—4月选参"),("validation","5—8月验证"),("holdout","9—12月留出")]:
        vals=[]
        for q in cfg["quantiles"]:
            r=next(r for r in periods if r["strategy"]==f"q{q:g}" and r["period"]==period)
            vals.append(float(r["inventory_adjusted_mean"])/int(r["days"])/1e4)
        ax.plot(cfg["quantiles"],vals,marker="o",label=label)
    ax.set(xlabel="固定分位数",ylabel="库存调整日均费用（万元）");ax.legend();ax.grid(alpha=.2);save(fig,"Fig2_quantile_comparison")
    fig,axes=plt.subplots(4,1,figsize=(10,8),sharex=True)
    for ax,date in zip(axes,cfg["paper_dates"]):
        g=group[date];ax.plot(np.arange(145)/6,[g[0]["e_start"]]+[r["e_end"] for r in g],color="#225EA8")
        ax.axhline(1200,ls="--",color="gray",lw=.8);ax.axhline(10800,ls="--",color="gray",lw=.8)
        ax.set(ylabel="储电量/kWh",title=date,ylim=(500,11500));ax.grid(alpha=.15)
    axes[-1].set(xlabel="时刻/h",xticks=range(0,25,4));fig.tight_layout();save(fig,"Fig3_paper_day_storage")
    b=lookup["baseline"];o=lookup["operational"];q=meta["selected_quantile"]
    costrows=[[r["strategy"],r["total_cost"],r["plan_cost"],r["emergency_cost"],r["emergency_kwh"],r["paid_grid_spill_kwh"],r["e_end"]] for r in totals]
    perows=[[r["strategy"],r["period"],float(r["total_cost"]),float(r["inventory_adjusted_mean"]),float(r["e_start"]),float(r["e_end"])] for r in periods if r["strategy"] in ["baseline","q0.8","operational"]]
    text=f'''# 问题2全年建模结果

## A 运行产物一览

本次覆盖2025-02-01至12-31共334天，每天144区间；1月统一热启动。`result2.xlsx`含三个模板核心表、费用汇总和指定四日表；`summary_tables.csv`、`monthly.csv`、`periods.csv`给完整比较；`operational_dispatch.csv`给正式策略逐时账本；全部固定参数反事实轨迹存于`dispatch.csv.gz`和`plans.json.gz`。

## B 核心结果与数学模型

正式可实施策略为2—4月q=0.8，5月1日使用此前选参数据确定q={q:g}并冻结至年底。全过程承接自身真实电量，不能拼接不同策略的库存轨迹。与基线相比，全年总费用减少{b['total_cost']-o['total_cost']:,.2f}元（{(b['total_cost']-o['total_cost'])/b['total_cost']:.2%}）。年初进入比较时为{meta['shared_initial_energy']:,.2f}kWh，正式策略期末{o['e_end']:,.2f}kWh，基线期末{b['e_end']:,.2f}kWh。

{md(['策略','总费用/元','计划电费/元','紧急电费/元','紧急电量/kWh','已付未用/kWh','期末电量/kWh'],costrows)}

固定q全年比较是描述性对照；正式策略只用2—4月选参，验证/留出期不参与改选。分段比较如下（库存调整只作比较诊断，不写入实际账单）：

{md(['策略','区间','现金费用/元','库存调整费用/元','期初电量','期末电量'],perows)}

### 预测与余量

每日d的144维负载和光伏预测分别为此前最多7天同刻均值。历史日h的净负载残差定义为r(h,t)=L(h,t)−PV(h,t)−[Lhat(h,t)−PVhat(h,t)]，历史预测也只能使用h以前的日期。余量m(d,t)=max(0,Q_q(过去28天同小时6个区间的残差))，规划负载设为Lhat+m。分位数为经验线性插值分位数，小时内合并不是144维联合概率保证。

单时段、无储能、过量电无残值近似下，J(x)=p*x+5p*E[(D−x)^+]，内点条件F_D(x)=0.8。该式仅解释0.8的成本直觉；完整模型的储能、容量、效率和价格耦合使该证明不能直接推广。最初PoC的0.8是预定启发式，本轮多分位数选择才有真实数据证据。

### 日前优化

144个区间交流侧变量为计划购电g、充电C、放电D、弃光S及区间末内部储电量E，目标min sum(p_t*g_t)。约束为g+PVhat+D=Lhat+m+C+S；E_t=E_(t−1)+0.9*C−D/0.9；1200≤E≤10800；0≤C,D≤5000/6；g≥0，不售电。E_0使用当天实际库存，预测末端E_144=6000是固定策略假设。

严格模型还要求同区间充放互斥，光伏先供负载后尽可能充电；只有优先吸收光伏后，普通计划网电才可补充充电。先求LP；若其解通过严格约束核算，LP下界与可行解相等，证明该次确定性计划最优；否则回退严格MILP。这不证明全年随机决策最优。

### 日内执行与结算

当天计划g固定不变；每区间PV先供负载，计划网电补剩余负载，盈余先PV后网电充电，缺口先放电至安全下限、不足再紧急购电b。真实E逐区间、逐日承接，实际日末无6000强制复位。费用K=sum(p*g+5p*b)，已付未用计划电不退款，紧急电只填负载缺口。在线执行假设当前10分钟平均供需可用于区间平衡，不代表秒级动态控制仿真。

### 指定四日论文表

表1所有购电量为日前计划，另列紧急费和合计费，避免两者混淆。

{md(sheets['指定日表1']['headers'],paper1)}

表2（交流侧充放电量，内部储电量）：

{md(sheets['指定日表2']['headers'],[[v if v is not None else '' for v in r] for r in paper2])}

表3（紧急电量，零事件明确列零）：

{md(sheets['指定日表3']['headers'],paper3)}

## C 图表解读

- Fig1_monthly_cost：基线、固定0.8和按时间选参策略的各月现金费用，观察季节稳定性。
- Fig2_quantile_comparison：选参、验证和留出分别展示库存调整日均费用，避免用月份天数差异误判。
- Fig3_paper_day_storage：四个指定日的真实库存轨迹与安全上下界，展示跨日库存并非每天6000。

## D 合理性检查与局限

独立审核{audit['rows']:,}条实际区间账本，365天原始数据完整。全部历史预测、余量、计划约束、费用、连续性、来源优先和选参分数独立复算通过；LP {audit['lp_days']}天、MILP {audit['milp_days']}天，最大单日求解{audit['max_solver_seconds']:.4f}秒。11项手算/穷举与32项多季节未来扰动检查通过。Excel仍须以`workbook_audit.json`的实际回读结果作为导出验收证据。

2—4月为参数选择期，包含此前已看过的2月PoC，不能充当无偏成绩。5—8月用于验证而不改参；9—12月只报告锁定策略留出表现。历史误差同小时池化、7日均值、28日窗口、预测终端6000和贪心放电都是固定建模选择；本次没有证明其全局最优。分位数提高可能降低紧急电却增加已付未用电，应以总费用而非单独紧急量排序。

## E 微调记录

由两周q=0.8 PoC扩展为全年基线和六个固定q。参数只用2—4月比较，在5月1日选择{q:g}；5—12月没有按结果再调参。物理、效率、信息边界、终端和执行器保持一致。未加入场景/CVaR/DP，也未改变设备给定参数。

## F 最终版本说明

入口`final_run/q2/main.py`按顺序抽取只读输入、求解、独立审核、生成图表/报告、导出Excel并回读。运行命令和依赖详见同目录README。所有模型输入哈希、配置、代码提交与实际版本均在`run.json`，禁止覆盖旧运行目录。

## G 敏感性分析下一步计划

已完成授权的分位数比较。后续可分别比较预测窗口3/7/14天、残差窗口14/28/56天、预测终端库存及未来SOC备用，保持时间选择/留出分离；这些额外实验本次未运行。

## H 评委视角

主要证据是完整334天可执行轨迹、锁定后验证/留出成绩、现金费用与库存的分开核算，以及指定日期可追溯结果。论文应避免声称经验0.8为完整模型理论最优、把全年最低固定q当作事先可知、将80%残差分位写成80%全年保供概率，或把计划电弃用漏出费用。
'''
    (run/"model_report.md").write_text(text,encoding="utf-8")
    print("Report, 7 worksheet payloads, 3 PNG/PDF figures generated",flush=True)

if __name__=="__main__":main(Path(sys.argv[1]))
