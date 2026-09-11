"""Independent source, forecast, plan and executed-ledger audit; no dispatch imports."""
import csv
import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
from prepare_q2_inputs import read_sources


def main(run):
    meta=json.loads((run/"run.json").read_text(encoding="utf-8"));c=meta["config"]
    raw=read_sources(c);snapshot=json.loads((run/"inputs.json").read_text(encoding="utf-8"))
    assert raw==snapshot
    assert hashlib.sha256((run/"inputs.json").read_bytes()).hexdigest()==meta["snapshot_sha256"]
    dates=raw["dates"]; index={d:i for i,d in enumerate(dates)}
    l=np.array(raw["load"]);v=np.array(raw["pv"]);price=np.array(raw["prices"])
    cap=c["power_kw"]*c["step_hours"];eta=c["charge_efficiency"];ed=c["discharge_efficiency"]
    with gzip.open(run/"plans.json.gz","rt",encoding="utf-8") as f: plans=json.load(f)
    forecasts={i:(np.mean(l[max(0,i-7):i],axis=0),np.mean(v[max(0,i-7):i],axis=0)) for i in range(1,365)}
    predmax=planmax=0.; lp=milp=0; solve_max=0.
    pmap={}
    for p in plans:
        i=index[p["date"]];key=(p["strategy"],p["date"]);assert key not in pmap;pmap[key]=p
        assert p["history_end"]==(dates[i-1] if i else None)
        if not i: continue
        lf,vf=forecasts[i];m=np.zeros(144)
        if p["quantile"] is not None:
            errs=np.array([l[j]-v[j]-forecasts[j][0]+forecasts[j][1] for j in range(max(1,i-28),i)])
            for t in range(0,144,6):m[t:t+6]=max(0.,np.quantile(errs[:,t:t+6].ravel(),p["quantile"]))
        predmax=max(predmax,np.max(np.abs(lf-p["lf"])),np.max(np.abs(vf-p["vf"])),np.max(np.abs(m-p["margin"])))
        g,pc,pd,pe,ps=[np.array(p[k]) for k in ["g","planned_c","planned_d","planned_e","planned_s"]]
        prev=np.r_[p["e_start"],pe[:-1]];surplus=np.maximum(vf-lf-m,0)
        priority=np.minimum(surplus,np.minimum(cap,np.maximum((10800-prev)/eta,0)))
        planmax=max(planmax,np.max(np.abs(g+vf+pd-lf-m-pc-ps)),np.max(np.abs(pe-prev-eta*pc+pd/ed)),
            abs(pe[-1]-6000),max(0,1200-min(pe),max(pe)-10800),max(0,max(pc)-cap,max(pd)-cap),
            np.max(np.abs(surplus-ps-priority)),max(0,-min(np.min(a) for a in [g,pc,pd,ps])))
        assert not np.any((pc>1e-6)&(pd>1e-6))
        assert abs(float(price@g)-p["solver"]["objective"])<1e-6
        assert p["solver"]["status"]==0 and p["solver"]["audit"]["pass"]
        lp+=p["solver"]["formulation"]=="LP";milp+=p["solver"]["formulation"]=="MILP"
        solve_max=max(solve_max,p["solver"]["seconds"])
    errors=defaultdict(float);last={};groups=defaultdict(lambda:defaultdict(float));slotcounts=defaultdict(int);rows=0
    with gzip.open(run/"dispatch.csv.gz","rt",encoding="utf-8",newline="") as f:
        for x in csv.DictReader(f):
            s,d=x.pop("strategy"),x.pop("date");r={k:float(z) for k,z in x.items()};t=int(r["slot"]);i=index[d];key=(s,d)
            assert t==slotcounts[key];slotcounts[key]+=1;rows+=1
            def err(name,val):errors[name]=max(errors[name],abs(float(val)))
            g,b,pv,ds,ll,ch,sp,sg=[r[k] for k in ["plan_kwh","emergency_kwh","pv_kwh","discharge_kwh","load_kwh","charge_kwh","pv_spill_kwh","paid_grid_spill_kwh"]]
            err("raw_load",ll-l[i,t]);err("raw_pv",pv-v[i,t]);err("plan_fixed",g-pmap[key]["g"][t])
            prev=last.get(s,6000 if s=="warmup" else meta["shared_initial_energy"])
            err("continuity",r["e_start"]-prev);last[s]=r["e_end"]
            err("state",r["e_end"]-r["e_start"]-eta*ch+ds/ed)
            err("balance",g+b+pv+ds-ll-ch-sp-sg)
            err("bounds",max(0,1200-r["e_end"],r["e_end"]-10800))
            err("power_kwh",max(0,ch-cap,ds-cap));err("negative",max(0,-min(g,b,pv,ds,ll,ch,sp,sg)))
            assert not(ch>1e-6 and ds>1e-6)
            err("pv_load",r["pv_load_kwh"]-min(pv,ll))
            err("pv_priority",r["pv_charge_kwh"]-min(max(0,pv-ll),cap,max(0,(10800-r["e_start"])/eta)))
            err("sources",ch-r["pv_charge_kwh"]-r["grid_charge_kwh"])
            err("pv_allocation",pv-r["pv_load_kwh"]-r["pv_charge_kwh"]-sp)
            err("grid_allocation",g-r["grid_load_kwh"]-r["grid_charge_kwh"]-sg)
            err("emergency_load_only",b-max(0,ll-pv-g-ds));err("emergency_no_charge",ch if b>1e-6 else 0)
            err("price",r["price"]-price[t]);err("plan_cost",r["plan_cost"]-price[t]*g)
            err("emergency_cost",r["emergency_cost"]-5*price[t]*b);err("total_cost",r["total_cost"]-price[t]*(g+5*b))
            for k in ["plan_kwh","emergency_kwh","load_kwh","plan_cost","emergency_cost","total_cost","pv_spill_kwh","paid_grid_spill_kwh","charge_kwh","discharge_kwh"]:groups[key][k]+=r[k]
            groups[key]["emergency_slots"]+=b>1e-6
            if t==0:groups[key]["e_start"]=r["e_start"]
            groups[key]["e_end"]=r["e_end"]
    assert rows==meta["dispatch_rows"] and all(n==144 for n in slotcounts.values())
    assert sum(s=="warmup" for s,d in slotcounts)==31
    for s in ["baseline","operational"]+[f"q{q:g}" for q in c["quantiles"]]:
        assert [d for ss,d in slotcounts if ss==s]==dates[31:]
    assert abs(last["warmup"]-meta["shared_initial_energy"])<1e-6
    with (run/"daily_summary.csv").open(encoding="utf-8-sig") as f:daily=list(csv.DictReader(f))
    for r in daily:
        g=groups[(r["strategy"],r["date"])]
        assert all(abs(float(r[k])-val)<1e-6 for k,val in g.items())
    # Independent selection score only from the permitted dates.
    selection=json.loads((run/"selection.json").read_text(encoding="utf-8"));scores=[]
    for q in c["quantiles"]:
        subset=[r for r in daily if r["strategy"]==f"q{q:g}" and r["date"]<"2025-05-01"]
        score=math.fsum(float(r["total_cost"]) for r in subset)+.9*float(np.mean(price))*(float(subset[0]["e_start"])-float(subset[-1]["e_end"]))
        scores.append((q,score))
    best=min(x[1] for x in scores);chosen=min([q for q,score in scores if score<=best+.01],key=lambda q:(abs(q-.8),q))
    assert chosen==selection["selected_quantile"]==meta["selected_quantile"]
    for p in plans:
        if p["strategy"]=="operational":assert p["quantile"]==(.8 if p["date"]<"2025-05-01" else chosen)
    for name in ["small_checks.json","information_checks.json"]:
        assert all(r["pass"] for r in json.loads((run/name).read_text(encoding="utf-8")))
    totals=json.loads((run/"totals.json").read_text(encoding="utf-8"))
    for total in totals:
        subset=[r for r in daily if r["strategy"]==total["strategy"]]
        assert abs(total["total_cost"]-math.fsum(float(r["total_cost"]) for r in subset))<.01
    result={"pass":max(errors.values())<1e-6 and predmax<1e-6 and planmax<1e-6,"rows":rows,
        "days_per_strategy":334,"all_365_source_days_checked":True,"max_errors":dict(errors),
        "independent_forecast_max_error":float(predmax),"independent_planned_flow_max_error":float(planmax),
        "lp_days":int(lp),"milp_days":int(milp),"max_solver_seconds":solve_max,"selected_quantile":chosen,
        "selection_independently_recomputed":True,"independence":"No optimizer/executor import; source re-read; forecasts, selection, plans and ledgers independently recomputed"}
    (run/"independent_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False));assert result["pass"]

if __name__=="__main__":main(Path(sys.argv[1]))
