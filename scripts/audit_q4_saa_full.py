"""Independently audit the frozen 334-day Task 4 SAA run."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_q4_saa_poc import flow_checks, load_csv, load_jsonl
from q2_core import execute, margin
from run_q4_price_poc import read_price_matrix, supply_forecast

TOL = 2e-5
SUMS = ["plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
        "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh"]


def audit(experiment: Path) -> dict:
    cfg = json.loads((ROOT / "configs/q4_saa_full.json").read_text(encoding="utf-8"))
    q2 = json.loads((ROOT / "experiments/q2-full-20260911-01/inputs.json").read_text(encoding="utf-8"))
    dates, prices, _, price_audit = read_price_matrix(experiment / "input_prices.json")
    if dates != q2["dates"]: raise AssertionError("Date alignment failed")
    load, pv, fixed = np.asarray(q2["load"], float), np.asarray(q2["pv"], float), np.asarray(q2["prices"], float)
    first, last = dates.index(cfg["start_date"]), dates.index(cfg["end_date"]); target = list(range(first, last+1))
    daily_rows = load_csv(experiment / "results/daily_summary.csv")
    daily = {(r["method"], r["date"]): r for r in daily_rows}
    plans = load_jsonl(experiment / "plans.jsonl.gz"); saa = load_jsonl(experiment / "saa_plans.jsonl.gz")
    annual = {r["method"]: r for r in load_csv(experiment / "results/summary_tables.csv")}
    quarterly = {(r["method"], r["period"]): r for r in load_csv(experiment / "results/quarterly_summary.csv")}
    monthly = {(r["method"], r["period"]): r for r in load_csv(experiment / "results/monthly_summary.csv")}
    information = json.loads((experiment / "information_checks.json").read_text(encoding="utf-8"))
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    maximum = defaultdict(float); errors = []
    if len(plans) != len(cfg["methods"])*334: errors.append("plan record count")
    if len(saa) != 334: errors.append("SAA record count")

    def expected_price(index, method):
        if method == "fixed": return fixed
        if method == "oracle_actual": return prices[index]
        return prices[index-(7 if method == "mean7" else 14):index].mean(axis=0)
    for rec in plans:
        index = dates.index(rec["date"]); method = rec["method"]
        ordinary = np.asarray(rec["ordinary_plan"], float); e0 = float(rec["e_start"])
        maximum["forecast"] = max(maximum["forecast"], float(np.max(np.abs(
            expected_price(index, method) - np.asarray(rec["forecast_price"], float)))))
        rows, ending = execute(prices[index], ordinary, load[index], pv[index], e0, cfg)
        src = daily[(method, rec["date"])]
        maximum["end_energy"] = max(maximum["end_energy"], abs(ending-float(src["e_end"])))
        for key in SUMS:
            maximum[f"daily_{key}"] = max(maximum[f"daily_{key}"], abs(
                math.fsum(row[key] for row in rows)-float(src[key])))
        if rec["causal"] and rec["history_end"] != dates[index-1]: errors.append((method, rec["date"], "history"))

    supply_cache, margin_cache = {}, {}
    def supply(index):
        if index not in supply_cache: supply_cache[index] = supply_forecast(load, pv, dates, index)
        return supply_cache[index]
    def forecasts(index):
        lf, pf = supply(index)
        if index not in margin_cache:
            residuals = []
            for old in range(index-cfg["residual_days"], index):
                ol, op = supply(old); residuals.append(load[old]-pv[old]-ol+op)
            margin_cache[index] = margin(residuals, cfg)
        return lf, pf, margin_cache[index]
    saa_g = {(r["method"], r["date"]): np.asarray(r["ordinary_plan"], float)
             for r in plans if r["method"] == "saa_load"}
    for rec in saa:
        index = dates.index(rec["date"]); e0 = float(rec["e_start"]); g = np.asarray(rec["first_stage_g"], float)
        lf, pf, extra = forecasts(index); nominal = {k: np.asarray(rec["nominal"][k], float) for k in ["g","c","d","s","e"]}
        maximum["g_actual_plan"] = max(maximum["g_actual_plan"], float(np.max(np.abs(g-saa_g[("saa_load", rec["date"])]))))
        maximum["g_nominal"] = max(maximum["g_nominal"], float(np.max(np.abs(g-nominal["g"]))))
        check = flow_checks(lf+extra, pf, nominal, e0, cfg, cfg["planned_terminal_energy"])
        maximum["nominal"] = max(maximum["nominal"], max(v for k,v in check.items() if k not in {"pass","mutual_count"}))
        if not check["pass"]: errors.append((rec["date"], "nominal"))
        history = list(range(index-cfg["scenario_history_days"], index))
        if len(rec["scenarios"]) != len(history): errors.append((rec["date"], "scenario count")); continue
        target_price = prices[index-cfg["price_history_days"]:index].mean(axis=0)
        for scenario, old in zip(rec["scenarios"], history):
            ol, op = supply(old); sl = np.maximum(0., lf+load[old]-ol); sp = np.maximum(0., pf+pv[old]-op)
            maximum["scenario_inputs"] = max(maximum["scenario_inputs"],
                float(np.max(np.abs(np.asarray(scenario["load"])-sl))),
                float(np.max(np.abs(np.asarray(scenario["pv"])-sp))),
                float(np.max(np.abs(np.asarray(scenario["price"])-target_price))))
            flow = {k: np.asarray(scenario[k], float) for k in ["g","c","d","b","s","e"]}
            check = flow_checks(sl, sp, flow, e0, cfg)
            maximum["scenario"] = max(maximum["scenario"], max(v for k,v in check.items() if k not in {"pass","mutual_count"}))
            if not check["pass"]: errors.append((rec["date"], scenario["date"], "scenario"))
            if scenario["date"] != dates[old]: errors.append((rec["date"], "scenario date"))

    # Rebuild annual, quarterly and monthly totals from the independently replayed daily table.
    def check_period(source, method, label, chosen, price_rows):
        for key in SUMS:
            maximum[f"period_{key}"] = max(maximum[f"period_{key}"], abs(
                math.fsum(float(row[key]) for row in chosen)-float(source[key])))
        mean_price = float(np.mean(price_rows)); adjusted = math.fsum(float(row["total_cost"]) for row in chosen) + 0.9*mean_price*(float(chosen[0]["e_start"])-float(chosen[-1]["e_end"]))
        maximum["period_adjusted"] = max(maximum["period_adjusted"], abs(adjusted-float(source["inventory_adjusted_cost"])))
    for method in cfg["methods"]:
        chosen = [daily[(method, dates[i])] for i in target]
        check_period(annual[method], method, "annual", chosen, prices[target])
        for label in sorted({f"2025-Q{(int(row['date'][5:7])-1)//3+1}" for row in chosen}):
            subset = [row for row in chosen if f"2025-Q{(int(row['date'][5:7])-1)//3+1}" == label]
            idx = [dates.index(row["date"]) for row in subset]; check_period(quarterly[(method,label)], method, label, subset, prices[idx])
        for label in sorted({row["date"][:7] for row in chosen}):
            subset = [row for row in chosen if row["date"].startswith(label)]
            idx = [dates.index(row["date"]) for row in subset]; check_period(monthly[(method,label)], method, label, subset, prices[idx])

    with gzip.open(experiment / "dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        dispatch_count = sum(1 for _ in csv.DictReader(stream))
    maximum["dispatch_count"] = abs(dispatch_count-len(cfg["methods"])*334*144)
    maximum["information_count"] = abs(len(information)-4*334)
    maximum["information_failures"] = sum(not row["pass"] for row in information)
    primary = cfg["primary_method"]; imp = cfg["practical_improvement_fraction"]
    annual_checks = {base: float(annual[primary]["inventory_adjusted_cost"]) <= float(annual[base]["inventory_adjusted_cost"])*(1-imp) for base in ["mean7","fixed"]}
    cash = float(annual[primary]["total_cost"]) <= min(float(annual["mean7"]["total_cost"]),float(annual["fixed"]["total_cost"]))+1e-6
    qchecks = {}
    for label in sorted({key[1] for key in quarterly}):
        value=float(quarterly[(primary,label)]["inventory_adjusted_cost"]); base=min(float(quarterly[("mean7",label)]["inventory_adjusted_cost"]),float(quarterly[("fixed",label)]["inventory_adjusted_cost"])); qchecks[label]=value<=base*(1+cfg["quarter_worsening_tolerance_fraction"])
    risk = not (float(annual[primary]["emergency_kwh"])>float(annual["mean7"]["emergency_kwh"]) and float(annual[primary]["paid_grid_spill_kwh"])>float(annual["mean7"]["paid_grid_spill_kwh"]))
    expected_select = all(annual_checks.values()) and cash and all(qchecks.values()) and risk
    if expected_select != selection["model_selection_pass"]: errors.append("selection reconstruction")
    passed = not errors and all(v <= TOL for k,v in maximum.items() if k not in {"information_failures","information_count","dispatch_count"}) and maximum["information_failures"]==0 and maximum["information_count"]==0 and maximum["dispatch_count"]==0
    result = {"pass": passed, "errors": errors, "maximum_errors": dict(maximum),
        "expected": {"days":334,"methods":cfg["methods"],"plans":len(cfg["methods"])*334,"saa_records":334,"dispatch_rows":len(cfg["methods"])*334*144,"information_checks":4*334},
        "price_audit": price_audit, "selection_reconstructed": expected_select,
        "selected_delivery": selection["selected_delivery"]}
    (experiment / "audit_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if not passed: raise AssertionError(result)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return result


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("experiment",type=Path); args=parser.parse_args(); audit(args.experiment.resolve())


if __name__ == "__main__": main()
