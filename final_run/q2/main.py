"""Q2 full-year reproducible runner. See README.md for optional runtime paths."""
import argparse
import csv
import gzip
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
import tempfile
from pathlib import Path

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from q2_core import forecast, margin, plan, execute
from run_q2_poc import unit_checks


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False,
        default=lambda x: x.tolist() if hasattr(x, "tolist") else str(x))+"\n", encoding="utf-8")


def table(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(rows[0])); w.writeheader(); w.writerows(rows)


def forecasts(load, pv, i, cfg, q):
    lf, vf = forecast(load[:i], pv[:i], cfg["forecast_days"])
    extra = np.zeros(144)
    if q is not None:
        residuals = []
        for h in range(max(1, i-cfg["residual_days"]), i):
            lp, vp = forecast(load[:h], pv[:h], cfg["forecast_days"])
            residuals.append(load[h]-pv[h]-lp+vp)
        extra = margin(residuals, {**cfg, "candidate_quantile": q})
    return lf, vf, extra


SUMS = ["plan_kwh", "emergency_kwh", "load_kwh", "plan_cost", "emergency_cost", "total_cost",
        "pv_spill_kwh", "paid_grid_spill_kwh", "charge_kwh", "discharge_kwh"]


def aggregate(days, cfg, price):
    out = {k: sum(r[k] for r in days) for k in SUMS}
    out.update(days=len(days), e_start=days[0]["e_start"], e_end=days[-1]["e_end"],
               emergency_slots=sum(r["emergency_slots"] for r in days),
               milp_days=sum(r["solver"]=="MILP" for r in days))
    for label, value in [("min", min(price)), ("mean", np.mean(price)), ("max", max(price))]:
        out["inventory_adjusted_"+label] = out["total_cost"]+cfg["discharge_efficiency"]*value*(out["e_start"]-out["e_end"])
    return out


def compute(run, cfg):
    started = time.perf_counter()
    data = json.loads((run/"inputs.json").read_text(encoding="utf-8"))
    dates=data["dates"]; load=np.array(data["load"]); pv=np.array(data["pv"]); price=np.array(data["prices"])
    assert len(dates)==365 and dates[-1]=="2025-12-31"
    start=dates.index(cfg["evaluation_start"]); cut=dates.index(cfg["switch_date"])
    checks=unit_checks(cfg); dump(run/"small_checks.json", checks)
    cache={}; daily=[]; plans=[]; selected_rows=[]
    log = gzip.open(run/"dispatch.csv.gz", "wt", newline="", encoding="utf-8")
    writer=None

    def day(i, strategy, q, energy):
        nonlocal writer
        if i==0:
            lf=vf=extra=np.zeros(144); flow={"g":np.zeros(144)}
            info={"formulation":"COLD_START", "seconds":0., "status":0}
        else:
            key=(i,q)
            if key not in cache: cache[key]=forecasts(load,pv,i,cfg,q)
            lf,vf,extra=cache[key]
            flow,info=plan(price,lf+extra,vf,energy,cfg)
        rows,end=execute(price,flow["g"],load[i],pv[i],energy,cfg)
        for t,r in enumerate(rows):
            r.update(strategy=strategy,date=dates[i])
            if writer is None:
                writer=csv.DictWriter(log,list(r)); writer.writeheader()
            writer.writerow(r)
        if strategy=="operational": selected_rows.extend(rows)
        summary={"strategy":strategy,"date":dates[i],"quantile":q,"e_start":energy,"e_end":end}
        summary.update({k:sum(r[k] for r in rows) for k in SUMS})
        summary.update(emergency_slots=sum(r["emergency_kwh"]>1e-6 for r in rows),
                       solver=info["formulation"],solve_seconds=info["seconds"])
        daily.append(summary)
        plans.append({"strategy":strategy,"date":dates[i],"quantile":q,"e_start":energy,"g":flow["g"],
                      "lf":lf,"vf":vf,"margin":extra,"history_end":dates[i-1] if i else None,"solver":info,
                      "planned_c":flow.get("c"),"planned_d":flow.get("d"),"planned_e":flow.get("e"),"planned_s":flow.get("s")})
        return end

    energy=cfg["initial_energy"]
    for i in range(start): energy=day(i,"warmup",None,energy)
    shared=energy; print("Warmup complete; shared E =",shared,flush=True)
    strategies={"baseline":None,**{f"q{q:g}":q for q in cfg["quantiles"]}}
    # Selection only observes Feb-Apr, before validation/holdout simulation.
    candidates=[]; ending={}
    for name,q in strategies.items():
        energy=shared
        for i in range(start,cut): energy=day(i,name,q,energy)
        ending[name]=energy
        a=aggregate([r for r in daily if r["strategy"]==name],cfg,price)
        candidates.append({"strategy":name,"quantile":q,**a})
    ranked=[r for r in candidates if r["quantile"] is not None]
    best_score=min(r["inventory_adjusted_mean"] for r in ranked)
    winner=min([r for r in ranked if r["inventory_adjusted_mean"]<=best_score+0.01],
               key=lambda r:(abs(r["quantile"]-.8),r["quantile"]))
    chosen=winner["quantile"]
    dump(run/"selection.json",{"used_dates":[cfg["evaluation_start"],cfg["selection_end"]],
        "selected_quantile":chosen,"scores":candidates,"rule":cfg["selection_score"],
        "selection_before_later_simulation":True})
    print("Selected on Feb-Apr only:",chosen,flush=True)
    for name,q in strategies.items():
        energy=ending[name]
        for i in range(cut,len(dates)): energy=day(i,name,q,energy)
        print(name,"334 days complete",flush=True)
    energy=shared
    for i in range(start,len(dates)):
        q=cfg["initial_quantile"] if i<cut else chosen
        energy=day(i,"operational",q,energy)
    log.close()
    # Information perturbations use multiple seasons and every quantile.
    info_checks=[]
    for label in ["2025-02-01","2025-05-01","2025-09-01","2025-12-21"]:
        i=dates.index(label); la=load.copy(); va=pv.copy(); la[i:]+=321;va[i:]*=.3
        for q in [None]+cfg["quantiles"]:
            original=forecasts(load,pv,i,cfg,q); altered=forecasts(la,va,i,cfg,q)
            error=max(float(np.max(np.abs(a-b))) for a,b in zip(original,altered))
            assert error==0
            info_checks.append({"date":label,"quantile":q,"max_forecast_change":error,"pass":True})
    for label in cfg["paper_dates"]:
        i=dates.index(label); schedule=next(p for p in plans if p["strategy"]=="operational" and p["date"]==label)
        original,_=execute(price,schedule["g"],load[i],pv[i],schedule["e_start"],cfg)
        changed=load[i].copy();changed[72:]+=1000
        altered,_=execute(price,schedule["g"],changed,pv[i],schedule["e_start"],cfg)
        error=max(abs(a[k]-b[k]) for a,b in zip(original[:72],altered[:72]) for k in a)
        assert error==0;info_checks.append({"date":label,"future_slots_change":error,"pass":True})
    dump(run/"information_checks.json",info_checks)
    with gzip.open(run/"plans.json.gz","wt",encoding="utf-8") as f:
        json.dump(plans,f,ensure_ascii=False,separators=(",",":"),default=lambda x:x.tolist())
    table(run/"daily_summary.csv",daily); table(run/"operational_dispatch.csv",selected_rows)
    totals=[];monthly=[];periods=[]
    for name in list(strategies)+["operational"]:
        rows=[r for r in daily if r["strategy"]==name]
        totals.append({"strategy":name,**aggregate(rows,cfg,price)})
        for m in range(2,13):
            r=[r for r in rows if int(r["date"][5:7])==m]
            monthly.append({"strategy":name,"month":m,**aggregate(r,cfg,price)})
        for label,lo,hi in [("selection","2025-02-01","2025-04-30"),("validation","2025-05-01","2025-08-31"),("holdout","2025-09-01","2025-12-31")]:
            periods.append({"strategy":name,"period":label,**aggregate([r for r in rows if lo<=r["date"]<=hi],cfg,price)})
    table(run/"summary_tables.csv",totals);table(run/"monthly.csv",monthly);table(run/"periods.csv",periods)
    dump(run/"totals.json",totals)
    dump(run/"run.json",{"config":cfg,"code_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
         "python":platform.python_version(),"numpy":np.__version__,"scipy":scipy.__version__,"input_hashes":data["input_hashes"],
         "snapshot_sha256":hashlib.sha256((run/"inputs.json").read_bytes()).hexdigest(),"selected_quantile":chosen,
         "warmup_days":start,"evaluation_days":len(dates)-start,"shared_initial_energy":shared,
         "runtime_seconds":time.perf_counter()-started,"dispatch_rows":(start+8*(len(dates)-start))*144,
         "note":"fixed-q annual comparisons are descriptive; operational switches only after selection"})
    print(json.dumps(totals,ensure_ascii=False,indent=2),flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--io-python",default=sys.executable)
    p.add_argument("--node",default="node")
    p.add_argument("--node-modules",type=Path,help="Existing bundled Node module directory; creates a task-only temporary junction")
    p.add_argument("--compute-only",action="store_true")
    a=p.parse_args();run=a.output.resolve()
    assert run.is_relative_to(ROOT/"experiments") and not run.exists()
    cfg=json.loads((ROOT/"configs/q2_full.json").read_text(encoding="utf-8"))
    subprocess.run([a.io_python,str(ROOT/"scripts/prepare_q2_inputs.py"),"--config",str(ROOT/"configs/q2_full.json"),"--output",str(run)],cwd=ROOT,check=True)
    try:
        compute(run,cfg)
        subprocess.run([a.io_python,str(ROOT/"final_run/q2/audit.py"),str(run)],cwd=ROOT,check=True)
        subprocess.run([sys.executable,str(ROOT/"final_run/q2/report.py"),str(run)],cwd=ROOT,check=True)
        if not a.compute_only:
            builder=ROOT/"final_run/q2/workbook.mjs"
            if a.node_modules:
                stage=Path(tempfile.mkdtemp(prefix="cumcm-q2-xlsx-"))
                shutil.copyfile(builder,stage/"workbook.mjs");builder=stage/"workbook.mjs"
                quoted=lambda p:"'"+str(p).replace("'","''")+"'"
                subprocess.run(["powershell","-NoProfile","-Command",
                    "New-Item -ItemType Junction -Path "+quoted(stage/"node_modules")+" -Target "+quoted(a.node_modules.resolve())],check=True)
            subprocess.run([a.node,str(builder),str(run)],cwd=ROOT,check=True)
            subprocess.run([a.io_python,str(ROOT/"final_run/q2/check_workbook.py"),str(run)],cwd=ROOT,check=True)
    except Exception as e:
        dump(run/"failure.json",{"error":repr(e)});raise


if __name__=="__main__": main()
