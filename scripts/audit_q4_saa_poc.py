"""Independent integrity and information audit for the Task 4 SAA PoC."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from q2_core import execute, margin
from run_q4_price_poc import read_price_matrix, supply_forecast

TOL = 2e-5
METHODS = ["fixed", "mean7", "mean14", "saa_load", "saa_joint", "saa_shuffled", "oracle_actual"]
SUMS = ["plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost", "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh"]


def load_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_jsonl(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def flow_checks(load, pv, flow, initial, cfg, terminal=None):
    eta, ed = cfg["charge_efficiency"], cfg["discharge_efficiency"]
    cap = cfg["power_kw"] * cfg["step_hours"]
    g = np.asarray(flow["g"], float); c = np.asarray(flow["c"], float); d = np.asarray(flow["d"], float)
    b = np.asarray(flow.get("b", np.zeros_like(g)), float); s = np.asarray(flow["s"], float); e = np.asarray(flow["e"], float)
    prev = np.r_[initial, e[:-1]]
    out = {
        "balance": float(np.max(np.abs(g + pv + d + b - load - c - s))),
        "state": float(np.max(np.abs(e - prev - eta*c + d/ed))),
        "bounds": float(max(0., cfg["energy_min"]-e.min(), e.max()-cfg["energy_max"])),
        "power": float(max(0., c.max()-cap, d.max()-cap)),
        "negative": float(max(0., -min(g.min(), c.min(), d.min(), b.min(), s.min()))),
        "mutual_count": int(np.sum((c > 1e-6) & (d > 1e-6))),
        "pv_priority": float(np.max(np.maximum(np.minimum(np.maximum(pv-load, 0.), np.minimum(cap, np.maximum((cfg["energy_max"]-prev)/eta, 0.)))-c, 0.))),
    }
    if terminal is not None: out["terminal"] = abs(float(e[-1]-terminal))
    out["pass"] = all(v <= TOL for k, v in out.items() if k not in {"pass", "mutual_count"}) and out["mutual_count"] == 0
    return out


def audit(experiment: Path) -> dict:
    cfg = json.loads((ROOT / "configs/q4_saa_poc.json").read_text(encoding="utf-8"))
    q2 = json.loads((ROOT / "experiments/q2-full-20260911-01/inputs.json").read_text(encoding="utf-8"))
    dates, prices, _, price_audit = read_price_matrix(experiment / "input_prices.json")
    if dates != q2["dates"]: raise AssertionError("Attachment 4 and Q2 dates do not align")
    load, pv = np.asarray(q2["load"], float), np.asarray(q2["pv"], float)
    fixed_price = np.asarray(q2["prices"], float)
    daily_rows = load_csv(experiment / "results/daily_summary.csv")
    daily = {(r["method"], r["window"], r["date"]): r for r in daily_rows}
    plans = load_jsonl(experiment / "plans.jsonl.gz")
    saa = load_jsonl(experiment / "saa_plans.jsonl.gz")
    information = json.loads((experiment / "information_checks.json").read_text(encoding="utf-8"))
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    maximum = defaultdict(float); errors = []
    expected_days = sum((date.fromisoformat(w["end"])-date.fromisoformat(w["start"])).days+1 for w in cfg["windows"])
    if len(plans) != len(METHODS)*expected_days: errors.append("plan record count")
    if len(saa) != 3*expected_days: errors.append("SAA record count")
    shared_states = {}
    with (ROOT / "experiments/q2-improve-20260911-01/daily.csv").open(encoding="utf-8-sig", newline="") as stream:
        for r in csv.DictReader(stream):
            if r["strategy"] == "combined": shared_states[r["date"]] = float(r["e_start"])
    def expected_price(i, method):
        if method == "fixed": return fixed_price
        if method == "oracle_actual": return prices[i]
        return prices[max(0, i-(7 if method == "mean7" else 14)):i].mean(axis=0)
    for rec in plans:
        i = dates.index(rec["date"]); method = rec["method"]; ordinary = np.asarray(rec["ordinary_plan"], float); e0 = float(rec["e_start"])
        if len(ordinary) != 144: errors.append((method, rec["date"], "plan length")); continue
        exp = expected_price(i, method); maximum["forecast_reconstruction"] = max(maximum["forecast_reconstruction"], float(np.max(np.abs(exp-np.asarray(rec["forecast_price"], float)))))
        rows, ending = execute(prices[i], ordinary, load[i], pv[i], e0, cfg)
        src = daily[(method, rec["window"], rec["date"])]
        maximum["end_energy"] = max(maximum["end_energy"], abs(ending-float(src["e_end"])))
        for field in SUMS:
            value = math.fsum(row[field] for row in rows)
            maximum[f"daily_{field}"] = max(maximum[f"daily_{field}"], abs(value-float(src[field])))
        if rec["causal"] and rec["history_end"] != dates[i-1]: errors.append((method, rec["date"], "history end"))
        if not rec["causal"] and method != "oracle_actual": errors.append((method, rec["date"], "causal flag"))
    # Independent reconstruction of every nominal and historical SAA recourse block.
    supply_cache = {}; margin_cache = {}
    def supply(i):
        if i not in supply_cache: supply_cache[i] = supply_forecast(load, pv, dates, i)
        return supply_cache[i]
    def target_forecast(i):
        lf, pf = supply(i)
        if i not in margin_cache:
            errs = []
            for h in range(max(1, i-cfg["residual_days"]), i):
                hlf, hpf = supply(h); errs.append(load[h]-pv[h]-hlf+hpf)
            margin_cache[i] = margin(errs, cfg)
        return lf, pf, margin_cache[i]
    for rec in saa:
        i = dates.index(rec["date"]); method=rec["method"]; e0=float(rec["e_start"]); g=np.asarray(rec["first_stage_g"],float)
        lf,pf,extra=target_forecast(i); nominal={k:np.asarray(rec["nominal"][k],float) for k in ["g","c","d","s","e"]}
        maximum["g_match"] = max(maximum["g_match"], float(np.max(np.abs(g-nominal["g"]))))
        maximum["nominal"] = max(maximum["nominal"], max(v for k,v in flow_checks(lf+extra,pf,nominal,e0,cfg,cfg["planned_terminal_energy"]).items() if k not in {"pass","mutual_count"}))
        history=list(range(i-cfg["scenario_history_days"],i)); rows=rec["scenarios"]
        if len(rows) != len(history): errors.append((method,rec["date"],"scenario count")); continue
        for pos,(sr,h) in enumerate(zip(rows,history)):
            hl,hp=supply(h); sl=np.maximum(0.,lf+load[h]-hl); sp=np.maximum(0.,pf+pv[h]-hp); target=prices[i-cfg["price_history_days"]:i].mean(axis=0)
            if method=="saa_load": ex_price=target
            elif method=="saa_joint": ex_price=np.maximum(cfg["price_floor"],target+prices[h]-prices[h-cfg["price_history_days"]:h].mean(axis=0))
            else:
                shifted=history[(pos+cfg["independent_price_shift_days"])%len(history)]
                ex_price=np.maximum(cfg["price_floor"],target+prices[shifted]-prices[shifted-cfg["price_history_days"]:shifted].mean(axis=0))
            maximum["scenario_inputs"] = max(maximum["scenario_inputs"], float(np.max(np.abs(np.asarray(sr["load"])-sl))), float(np.max(np.abs(np.asarray(sr["pv"])-sp))), float(np.max(np.abs(np.asarray(sr["price"])-ex_price))))
            flow={k:np.asarray(sr[k],float) for k in ["g","c","d","b","s","e"]}; chk=flow_checks(sl,sp,flow,e0,cfg)
            maximum["scenario"] = max(maximum["scenario"], max(v for k,v in chk.items() if k not in {"pass","mutual_count"}))
            if not chk["pass"]: errors.append((method,rec["date"],sr["date"],"scenario feasibility"))
            if sr["date"] != dates[h]: errors.append((method,rec["date"],"scenario date"))
    with gzip.open(experiment / "dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        dispatch=list(csv.DictReader(stream))
    maximum["dispatch_rows"] = abs(len(dispatch)-len(METHODS)*expected_days*144)
    maximum["information_checks"] = abs(len(information)-6*expected_days)
    maximum["information_failures"] = sum(not r["pass"] for r in information)
    if selection.get("selected") not in cfg["candidate_order"]: errors.append("selection")
    passed = not errors and all(v <= TOL for k,v in maximum.items() if k not in {"information_failures","information_checks","dispatch_rows"}) and maximum["information_failures"] == 0 and maximum["information_checks"] == 0 and maximum["dispatch_rows"] == 0
    out={"pass":passed,"errors":errors,"maximum_errors":dict(maximum),"expected":{"methods":METHODS,"days":expected_days,"plan_records":len(METHODS)*expected_days,"saa_records":3*expected_days,"dispatch_rows":len(METHODS)*expected_days*144},"price_audit":price_audit,"scientific_target_pass":selection.get("scientific_target_pass"),"selected":selection.get("selected")}
    (experiment/"audit_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if not passed: raise AssertionError(out)
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return out


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("experiment",type=Path); args=parser.parse_args(); audit(args.experiment.resolve())


if __name__ == "__main__": main()
