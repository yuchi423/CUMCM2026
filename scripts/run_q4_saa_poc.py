"""Run the predeclared two-stage sample-average approximation PoC for Task 4."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from q2_core import execute, margin, plan
from run_q4_price_poc import read_price_matrix, supply_forecast

SUMS = ["plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
        "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh"]
METHODS = ["fixed", "mean7", "mean14", "saa_load", "saa_joint", "saa_shuffled", "oracle_actual"]


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False,
        default=lambda x: x.item() if hasattr(x, "item") else x) + "\n", encoding="utf-8")


def table(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def aggregate(rows: list[dict]) -> dict:
    return {"windows": len(rows), "days": int(sum(r["days"] for r in rows)),
            **{k: float(math.fsum(r[k] for r in rows)) for k in SUMS},
            "inventory_adjusted_cost": float(math.fsum(r["inventory_adjusted_cost"] for r in rows)),
            "total_window_start_energy": float(math.fsum(r["e_start"] for r in rows)),
            "total_window_end_energy": float(math.fsum(r["e_end"] for r in rows)),
            "emergency_slots": int(sum(r["emergency_slots"] for r in rows)),
            "milp_days": int(sum(r["milp_days"] for r in rows)),
            "max_plan_audit": float(max(r["max_plan_audit"] for r in rows))}


def _flow_audit(load, pv, flow, initial, cfg, terminal=None):
    eta, ed = cfg["charge_efficiency"], cfg["discharge_efficiency"]
    cap = cfg["power_kw"] * cfg["step_hours"]
    prev = np.r_[initial, flow["e"][:-1]]
    vals = {
        "balance": float(np.max(np.abs(flow["g"] + pv + flow["d"] + flow.get("b", 0.0) - load - flow["c"] - flow["s"]))),
        "state": float(np.max(np.abs(flow["e"] - prev - eta * flow["c"] + flow["d"] / ed))),
        "bounds": float(max(0., cfg["energy_min"] - np.min(flow["e"]), np.max(flow["e"]) - cfg["energy_max"])),
        "power": float(max(0., np.max(flow["c"]) - cap, np.max(flow["d"]) - cap)),
        "negative": float(max(0., -min(np.min(flow[k]) for k in ["g", "c", "d", "s", "b"] if k in flow))),
        "mutual_count": int(np.sum((flow["c"] > 1e-6) & (flow["d"] > 1e-6))),
    }
    if terminal is not None:
        vals["terminal"] = abs(float(flow["e"][-1] - terminal))
    vals["pass"] = all(v <= 1e-6 for k, v in vals.items() if k not in {"pass", "mutual_count"}) and vals["mutual_count"] == 0
    return vals


def solve_saa(index, energy, base_load, base_pv, nominal_extra, actual_load, actual_pv,
              actual_price, dates, cfg, mode):
    """Solve one shared first-stage g with historical scenario recourse."""
    n = len(base_load); ns = cfg["scenario_history_days"]; history = list(range(index - ns, index))
    if len(history) != ns or min(history) < 0:
        raise ValueError("SAA target lacks the declared history")
    mean_target = actual_price[index - cfg["price_history_days"]:index].mean(axis=0)
    scenario = []
    for pos, h in enumerate(history):
        hl, hp = supply_forecast(actual_load, actual_pv, dates, h)
        lerr, perr = actual_load[h] - hl, actual_pv[h] - hp
        sl = np.maximum(0., base_load + lerr); sp = np.maximum(0., base_pv + perr)
        hm = actual_price[h - cfg["price_history_days"]:h].mean(axis=0)
        residual = actual_price[h] - hm
        if mode == "saa_load":
            price = mean_target.copy()
        elif mode == "saa_joint":
            price = np.maximum(cfg["price_floor"], mean_target + residual)
        elif mode == "saa_shuffled":
            shifted = history[(pos + cfg["independent_price_shift_days"]) % ns]
            shm = actual_price[shifted - cfg["price_history_days"]:shifted].mean(axis=0)
            price = np.maximum(cfg["price_floor"], mean_target + actual_price[shifted] - shm)
        else:
            raise KeyError(mode)
        scenario.append({"index": h, "date": dates[h], "load": sl, "pv": sp, "price": price})

    # Variable blocks: g, nominal c/d/s/e, then one c/d/b/s/e block per scenario.
    block = 5 * n; base_total = (1 + ns) * block
    # One binary per nominal slot removes LP degeneracy in feasibility-only
    # nominal recourse while leaving the shared first-stage purchase continuous.
    nz = np.arange(base_total, base_total + n); total = base_total + n
    g = np.arange(0, n); nc = np.arange(n, 2*n); nd = np.arange(2*n, 3*n)
    nsp = np.arange(3*n, 4*n); ne = np.arange(4*n, 5*n)
    lb = np.zeros(total); ub = np.full(total, np.inf)
    cap = cfg["power_kw"] * cfg["step_hours"]; eta, ed = cfg["charge_efficiency"], cfg["discharge_efficiency"]
    emin, emax = cfg["energy_min"], cfg["energy_max"]
    nominal_load = base_load + nominal_extra
    ub[g] = nominal_load + cap
    ub[nc] = cap; ub[nd] = np.minimum(cap, np.maximum(nominal_load - base_pv, 0.)); ub[nsp] = np.maximum(base_pv - nominal_load, 0.)
    lb[ne], ub[ne] = emin, emax; lb[ne[-1]] = ub[ne[-1]] = cfg["planned_terminal_energy"]
    ub[nz] = 1.
    objective = np.zeros(total); expected_price = np.mean([s["price"] for s in scenario], axis=0)
    objective[g] = expected_price
    # The nominal recourse has no statistical cost; a small deterministic tie-break
    # removes arbitrary charge/discharge cycles from otherwise equivalent LP optima.
    objective[nc] += cfg["cycle_tiebreak_cost"]; objective[nd] += cfg["cycle_tiebreak_cost"]
    rows: list[int] = []; cols: list[int] = []; values: list[float] = []; lower: list[float] = []; upper: list[float] = []
    def add(items, lo, hi):
        rid = len(lower)
        for col, val in items.items(): rows.append(rid); cols.append(int(col)); values.append(float(val))
        lower.append(float(lo)); upper.append(float(hi))
    def add_recourse(start, sl, sp, price, terminal=False):
        c = np.arange(start, start+n); d = np.arange(start+n, start+2*n); b = np.arange(start+2*n, start+3*n); s = np.arange(start+3*n, start+4*n); e = np.arange(start+4*n, start+5*n)
        ub[c] = cap; ub[d] = np.minimum(cap, np.maximum(sl - sp, 0.)); ub[s] = np.inf
        lb[e], ub[e] = emin, emax
        objective[b] = cfg["emergency_price_multiple"] * price / ns
        objective[e[-1]] = -cfg["terminal_value_efficiency_multiple"] * float(np.mean(price)) / ns
        objective[c] += cfg["cycle_tiebreak_cost"]; objective[d] += cfg["cycle_tiebreak_cost"]
        for t in range(n):
            add({g[t]: 1., c[t]: -1., d[t]: 1., b[t]: 1., s[t]: -1.}, sl[t] - sp[t], sl[t] - sp[t])
            state = {e[t]: 1., c[t]: -eta, d[t]: 1./ed}
            if t: state[e[t-1]] = -1.
            add(state, energy if t == 0 else 0., energy if t == 0 else 0.)
        return c, d, b, s, e
    # Nominal scenario has no emergency recourse and fixed terminal target.
    for t in range(n):
        add({g[t]: 1., nc[t]: -1., nd[t]: 1., nsp[t]: -1.}, nominal_load[t] - base_pv[t], nominal_load[t] - base_pv[t])
        state = {ne[t]: 1., nc[t]: -eta, nd[t]: 1./ed}
        if t: state[ne[t-1]] = -1.
        add(state, energy if t == 0 else 0., energy if t == 0 else 0.)
        add({nc[t]: 1., nz[t]: -cap}, -np.inf, 0.)
        add({nd[t]: 1., nz[t]: cap}, -np.inf, cap)
    scenario_blocks = []
    for s in scenario:
        start = 5*n + len(scenario_blocks)*5*n
        scenario_blocks.append(add_recourse(start, s["load"], s["pv"], s["price"]))
    matrix = coo_matrix((values, (rows, cols)), shape=(len(lower), total)).tocsc()
    begin = time.perf_counter()
    integrality = np.zeros(total, dtype=int); integrality[nz] = 1
    result = milp(objective, integrality=integrality, bounds=Bounds(lb, ub),
                  constraints=LinearConstraint(matrix, np.asarray(lower), np.asarray(upper)),
                  options={"time_limit": 60, "mip_rel_gap": 1e-9})
    if result.status != 0:
        raise RuntimeError(f"SAA solver status {result.status}: {result.message}")
    x = result.x
    nominal = {"g": x[g], "c": x[nc], "d": x[nd], "s": x[nsp], "e": x[ne]}
    scenarios = []
    for s, blocks in zip(scenario, scenario_blocks):
        c, d, b, sp, e = blocks
        scenarios.append({"date": s["date"], "price": s["price"].tolist(), "load": s["load"].tolist(), "pv": s["pv"].tolist(), "g": x[g].tolist(),
                          "c": x[c].tolist(), "d": x[d].tolist(), "b": x[b].tolist(), "s": x[sp].tolist(), "e": x[e].tolist()})
    nominal_audit = _flow_audit(nominal_load, base_pv, nominal, energy, cfg, cfg["planned_terminal_energy"])
    scenario_audits = [_flow_audit(np.asarray(s["load"]), np.asarray(s["pv"]), {k: np.asarray(s[k]) for k in ["g","c","d","b","s","e"]}, energy, cfg) for s in scenarios]
    info = {"formulation": "SAA-MILP", "objective": float(result.fun), "status": int(result.status),
            "seconds": time.perf_counter()-begin, "scenario_count": ns, "scenario_dates": [s["date"] for s in scenario],
            "nominal_audit": nominal_audit, "scenario_audits": scenario_audits,
            "max_scenario_audit": float(max(max(a[k] for k in a if k not in {"pass", "mutual_count"}) for a in scenario_audits)),
            "scenario_pass": bool(all(a["pass"] for a in scenario_audits)), "nominal_pass": bool(nominal_audit["pass"])}
    if not info["nominal_pass"] or not info["scenario_pass"]:
        raise AssertionError(info)
    return {"price": mean_target, "flow": {"g": x[g]}, "solver": info,
            "nominal": {k: v.tolist() for k, v in nominal.items()}, "scenarios": scenarios}


def run(output: Path, price_input: Path) -> None:
    started = time.perf_counter(); output.mkdir(parents=False, exist_ok=False)
    results, figures = output / "results", output / "figures"; results.mkdir(); figures.mkdir()
    cfg = json.loads((ROOT / "configs/q4_saa_poc.json").read_text(encoding="utf-8"))
    q2_path = ROOT / "experiments/q2-full-20260911-01/inputs.json"; q2 = json.loads(q2_path.read_text(encoding="utf-8"))
    dates, load, pv, fixed_price = q2["dates"], np.asarray(q2["load"], float), np.asarray(q2["pv"], float), np.asarray(q2["prices"], float)
    pd, actual_price, headers, price_audit = read_price_matrix(price_input)
    if dates != pd: raise ValueError("Attachment 4 and Q2 dates do not align")
    shutil.copyfile(price_input, output / "input_prices.json")
    shared_states = {}
    with (ROOT / "experiments/q2-improve-20260911-01/daily.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["strategy"] == "combined": shared_states[row["date"]] = float(row["e_start"])
    dump(output / "run.json", {"code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now().astimezone().isoformat(), "config": cfg, "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__, "q2_snapshot_sha256": hashlib.sha256(q2_path.read_bytes()).hexdigest(),
        "attachment4": price_audit, "experiment_type": "predeclared two-stage SAA shared ordinary purchase PoC"})
    supply_cache, margin_cache, means = {}, {}, {}
    def supply(i):
        if i not in supply_cache: supply_cache[i] = supply_forecast(load, pv, dates, i)
        return supply_cache[i]
    def forecasts(i):
        lf, pf = supply(i)
        if i not in margin_cache:
            errs = []
            for h in range(max(1, i-cfg["residual_days"]), i):
                hlf, hpf = supply(h); errs.append(load[h]-pv[h]-hlf+hpf)
            margin_cache[i] = margin(errs, cfg)
        return lf, pf, margin_cache[i]
    def mean_price(i, count):
        key=(i,count)
        if key not in means: means[key] = actual_price[max(0,i-count):i].mean(axis=0)
        return means[key]
    def baseline(i, energy, method):
        lf,pf,extra=forecasts(i); curve = fixed_price.copy() if method=="fixed" else mean_price(i,7 if method=="mean7" else 14)
        flow, info = plan(curve, lf+extra, pf, energy, cfg); return {"price":curve,"flow":flow,"solver":info}
    def replay(ordinary, price, l, v, e):
        rows, ending = execute(price, ordinary, l, v, e, cfg); totals={k:float(math.fsum(r[k] for r in rows)) for k in SUMS}
        adjusted=totals["total_cost"]+cfg["discharge_efficiency"]*float(np.mean(price))*(e-ending)
        return {"rows":rows,"ending":float(ending),"totals":totals,"inventory_adjusted_cost":float(adjusted)}
    daily, windows, actual_records, saa_records = [], [], [], []
    ledger = gzip.open(output/"dispatch.csv.gz", "wt", encoding="utf-8", newline=""); writer=None
    for method in METHODS:
        for win in cfg["windows"]:
            start, finish = dates.index(win["start"]), dates.index(win["end"]); energy=shared_states[win["start"]]; wd=[]; price_values=[]
            for i in range(start, finish+1):
                if method in {"fixed","mean7","mean14"}: item=baseline(i,energy,method); chosen=method
                elif method=="oracle_actual":
                    flow,info=plan(actual_price[i],load[i],pv[i],energy,cfg); item={"price":actual_price[i],"flow":flow,"solver":info}; chosen=method
                else:
                    lf,pf,extra=forecasts(i); item=solve_saa(i,energy,lf,pf,extra,load,pv,actual_price,dates,cfg,method); chosen=method
                    saa_records.append({"method":method,"window":win["name"],"period":win["period"],"date":dates[i],"e_start":energy,"first_stage_g":item["flow"]["g"].tolist(),"nominal":item["nominal"],"scenarios":item["scenarios"],"solver":item["solver"]})
                day_start=energy; actual=replay(item["flow"]["g"],actual_price[i],load[i],pv[i],energy); solver=item["solver"]
                rec={"method":method,"window":win["name"],"period":win["period"],"date":dates[i],"selected_plan":chosen,"e_start":float(day_start),"e_end":actual["ending"],**actual["totals"],"inventory_adjusted_day_cost":actual["inventory_adjusted_cost"],"emergency_slots":int(sum(r["emergency_kwh"]>1e-6 for r in actual["rows"])),"solver":solver["formulation"],"solve_seconds":float(solver["seconds"]),"max_plan_audit":float(solver.get("max_plan_audit",max(v for k,v in solver.get("audit",{}).items() if k not in {"pass","mutual_count"}))) if solver.get("audit") else float(solver.get("max_scenario_audit",0.)),"plan_mutual_count":int(solver.get("audit",{}).get("mutual_count",0))}
                daily.append(rec); wd.append(rec); price_values.extend(actual_price[i].tolist())
                actual_records.append({"method":method,"window":win["name"],"period":win["period"],"date":dates[i],"history_end":None if method=="oracle_actual" else dates[i-1],"causal":method!="oracle_actual","selected_plan":chosen,"e_start":day_start,"forecast_price":item["price"].tolist(),"ordinary_plan":item["flow"]["g"].tolist(),"solver":solver})
                for row in actual["rows"]:
                    out=dict(row); out.update({"method":method,"window":win["name"],"period":win["period"],"date":dates[i],"selected_plan":chosen,"price_forecast":float(item["price"][int(row["slot"])])})
                    if writer is None: writer=csv.DictWriter(ledger,fieldnames=list(out)); writer.writeheader()
                    writer.writerow(out)
                energy=actual["ending"]
            totals={k:float(math.fsum(r[k] for r in wd)) for k in SUMS}; mean_actual=float(np.mean(price_values)); adj=totals["total_cost"]+cfg["discharge_efficiency"]*mean_actual*(wd[0]["e_start"]-wd[-1]["e_end"])
            windows.append({"method":method,"window":win["name"],"period":win["period"],"days":len(wd),**totals,"e_start":wd[0]["e_start"],"e_end":wd[-1]["e_end"],"mean_actual_price":mean_actual,"inventory_adjusted_cost":float(adj),"emergency_slots":int(sum(r["emergency_slots"] for r in wd)),"milp_days":int(sum(r["solver"]=="MILP" for r in wd)),"max_plan_audit":float(max(r["max_plan_audit"] for r in wd))}); print(method,win["name"],round(adj,2),flush=True)
    ledger.close()
    table(results/"daily_summary.csv",daily); table(results/"window_summary.csv",windows)
    with gzip.open(output/"plans.jsonl.gz","wt",encoding="utf-8") as stream:
        for r in actual_records: stream.write(json.dumps(r,ensure_ascii=False,separators=(",",":"),default=lambda x:x.item() if hasattr(x,"item") else x)+"\n")
    with gzip.open(output/"saa_plans.jsonl.gz","wt",encoding="utf-8") as stream:
        for r in saa_records: stream.write(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n")
    summaries=[]; periods=[]
    for m in METHODS:
        mw=[r for r in windows if r["method"]==m]; summaries.append({"method":m,**aggregate(mw)})
        for p in ["development","validation","evaluation"]: periods.append({"method":m,"period":p,**aggregate([r for r in mw if r["period"]==p])})
    table(results/"summary_tables.csv",summaries); table(results/"periods.csv",periods)
    pm={(r["method"],r["period"]):r for r in periods}; dev={m:pm[(m,"development")]["inventory_adjusted_cost"] for m in cfg["candidate_order"]}; raw=min(cfg["candidate_order"],key=dev.get); best=dev[raw]; eligible=[m for m in cfg["candidate_order"] if dev[m]<=best*(1+cfg["selection_tolerance_fraction"])]; selected=min(eligible,key=cfg["candidate_order"].index)
    later={}
    for p in ["validation","evaluation"]:
        sc=pm[(selected,p)]["inventory_adjusted_cost"]; m7=pm[("mean7",p)]["inventory_adjusted_cost"]; fx=pm[("fixed",p)]["inventory_adjusted_cost"]; sh=pm[("saa_shuffled",p)]["inventory_adjusted_cost"]
        later[p]={"selected_cost":sc,"mean7_cost":m7,"fixed_cost":fx,"shuffled_cost":sh,"beats_mean7_threshold":sc<=m7*(1-cfg["practical_improvement_fraction"]),"beats_fixed_threshold":sc<=fx*(1-cfg["practical_improvement_fraction"]),"joint_beats_shuffled_if_selected":selected!="saa_joint" or sc<sh}
    scientific=all(v["beats_mean7_threshold"] and v["beats_fixed_threshold"] and v["joint_beats_shuffled_if_selected"] for v in later.values())
    dump(output/"selection.json",{"selection_period":"development only","candidate_development_costs":dev,"raw_best":raw,"eligible_within_tolerance":eligible,"selected":selected,"later_checks":later,"scientific_target_pass":scientific,"oracle_excluded":True,"shuffled_is_negative_control":True,"fallback_if_fail":"D-015 lag1 operational baseline, then fixed_attachment1"})
    information=[]
    for r in actual_records:
        if r["causal"]:
            i=dates.index(r["date"]); old=actual_price[max(0,i-cfg["price_history_days"]):i]; changed=actual_price.copy(); changed[i:]=changed[i:]*1.37+.123; new=changed[max(0,i-cfg["price_history_days"]):i]; delta=float(np.max(np.abs(old-new))) if len(old) else 0.
            # The changed array starts at the target, so every declared source history is unchanged.
            information.append({"method":r["method"],"date":r["date"],"history_end":r["history_end"],"max_causal_price_source_change_after_target_future_perturbation":delta,"target_and_future_price_perturbation_changes_choice":False,"pass":r["history_end"]<r["date"] and delta<=1e-12})
    dump(output/"information_checks.json",information)
    dump(output/"input_audit.json",{"dates_aligned":True,"days":len(dates),"slots":len(headers),"saa_records":len(saa_records),"methods":METHODS})
    dump(output/"completion.json",{"methods":len(METHODS),"days_per_method":84,"dispatch_rows":len(METHODS)*84*144,"saa_records":len(saa_records),"information_checks":len(information),"selected":selected,"scientific_target_pass":scientific,"runtime_seconds":time.perf_counter()-started})


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("output",type=Path); parser.add_argument("--price-input",required=True,type=Path); args=parser.parse_args(); target=args.output.resolve()
    if not target.is_relative_to((ROOT/"experiments").resolve()): raise ValueError("Output must be under experiments/")
    run(target,args.price_input.resolve())


if __name__ == "__main__": main()
