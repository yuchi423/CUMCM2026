"""Run the frozen 334-day Task 4 SAA model and required baselines."""
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
from datetime import datetime
from pathlib import Path

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from q2_core import execute, margin, plan
from run_q4_price_poc import read_price_matrix, supply_forecast
from run_q4_saa_poc import solve_saa, dump, table

SUMS = ["plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
        "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh"]


def period_row(method: str, label: str, rows: list[dict], prices: np.ndarray) -> dict:
    totals = {key: float(math.fsum(row[key] for row in rows)) for key in SUMS}
    mean_price = float(np.mean(prices))
    adjusted = totals["total_cost"] + 0.9 * mean_price * (rows[0]["e_start"] - rows[-1]["e_end"])
    return {"method": method, "period": label, "days": len(rows), **totals,
            "e_start": rows[0]["e_start"], "e_end": rows[-1]["e_end"],
            "mean_actual_price": mean_price, "inventory_adjusted_cost": float(adjusted),
            "emergency_slots": int(sum(row["emergency_slots"] for row in rows)),
            "milp_days": int(sum("MILP" in row["solver"] for row in rows)),
            "max_plan_audit": float(max(row["max_plan_audit"] for row in rows))}


def run(output: Path, price_input: Path) -> None:
    started = time.perf_counter(); output.mkdir(parents=False, exist_ok=False)
    results, figures = output / "results", output / "figures"; results.mkdir(); figures.mkdir()
    cfg = json.loads((ROOT / "configs/q4_saa_full.json").read_text(encoding="utf-8"))
    q2_path = ROOT / "experiments/q2-full-20260911-01/inputs.json"
    q2 = json.loads(q2_path.read_text(encoding="utf-8"))
    dates = q2["dates"]; load = np.asarray(q2["load"], float); pv = np.asarray(q2["pv"], float)
    fixed_price = np.asarray(q2["prices"], float)
    price_dates, actual_price, headers, price_audit = read_price_matrix(price_input)
    if dates != price_dates: raise ValueError("Attachment 4 and Q2 dates do not align")
    shutil.copyfile(price_input, output / "input_prices.json")
    shared_states = {}
    with (ROOT / "experiments/q2-improve-20260911-01/daily.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["strategy"] == "combined": shared_states[row["date"]] = float(row["e_start"])
    first, last = dates.index(cfg["start_date"]), dates.index(cfg["end_date"])
    target_indices = list(range(first, last + 1))
    if len(target_indices) != 334: raise AssertionError(len(target_indices))
    dump(output / "run.json", {
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now().astimezone().isoformat(), "config": cfg,
        "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
        "q2_snapshot_sha256": hashlib.sha256(q2_path.read_bytes()).hexdigest(),
        "attachment4": price_audit, "experiment_type": "frozen 334-day SAA full run"})

    supply_cache, margin_cache, price_cache = {}, {}, {}
    def supply(index):
        if index not in supply_cache: supply_cache[index] = supply_forecast(load, pv, dates, index)
        return supply_cache[index]
    def forecasts(index):
        lf, pf = supply(index)
        if index not in margin_cache:
            residuals = []
            for old in range(max(1, index - cfg["residual_days"]), index):
                old_lf, old_pf = supply(old); residuals.append(load[old] - pv[old] - old_lf + old_pf)
            margin_cache[index] = margin(residuals, cfg)
        return lf, pf, margin_cache[index]
    def mean_price(index, count):
        key = (index, count)
        if key not in price_cache: price_cache[key] = actual_price[index-count:index].mean(axis=0)
        return price_cache[key]
    def replay(ordinary, index, energy):
        rows, ending = execute(actual_price[index], ordinary, load[index], pv[index], energy, cfg)
        totals = {key: float(math.fsum(row[key] for row in rows)) for key in SUMS}
        adjusted = totals["total_cost"] + 0.9 * float(np.mean(actual_price[index])) * (energy-ending)
        return rows, float(ending), totals, float(adjusted)

    daily, plan_records, saa_records = [], [], []
    ledger = gzip.open(output / "dispatch.csv.gz", "wt", encoding="utf-8", newline=""); writer = None
    for method in cfg["methods"]:
        energy = shared_states[cfg["start_date"]]
        for ordinal, index in enumerate(target_indices, 1):
            lf, pf, extra = forecasts(index)
            if method in {"fixed", "mean7", "mean14"}:
                curve = fixed_price.copy() if method == "fixed" else mean_price(index, 7 if method == "mean7" else 14)
                flow, solver = plan(curve, lf + extra, pf, energy, cfg)
                item = {"price": curve, "flow": flow, "solver": solver}
            elif method == "oracle_actual":
                flow, solver = plan(actual_price[index], load[index], pv[index], energy, cfg)
                item = {"price": actual_price[index], "flow": flow, "solver": solver}
            else:
                item = solve_saa(index, energy, lf, pf, extra, load, pv, actual_price, dates, cfg, "saa_load")
                saa_records.append({"method": method, "date": dates[index], "e_start": energy,
                    "first_stage_g": item["flow"]["g"].tolist(), "nominal": item["nominal"],
                    "scenarios": item["scenarios"], "solver": item["solver"]})
            day_start = energy
            actual_rows, energy, totals, adjusted = replay(item["flow"]["g"], index, energy)
            audit_values = item["solver"].get("audit", {})
            max_audit = (max(value for key, value in audit_values.items() if key not in {"pass", "mutual_count"})
                         if audit_values else item["solver"].get("max_scenario_audit", 0.0))
            record = {"method": method, "date": dates[index], "e_start": float(day_start),
                "e_end": float(energy), **totals, "inventory_adjusted_day_cost": adjusted,
                "emergency_slots": int(sum(row["emergency_kwh"] > 1e-6 for row in actual_rows)),
                "solver": item["solver"]["formulation"], "solve_seconds": float(item["solver"]["seconds"]),
                "max_plan_audit": float(max_audit),
                "plan_mutual_count": int(audit_values.get("mutual_count", 0))}
            daily.append(record)
            plan_records.append({"method": method, "date": dates[index],
                "history_end": None if method == "oracle_actual" else dates[index-1],
                "causal": method != "oracle_actual", "e_start": day_start,
                "forecast_price": item["price"].tolist(), "ordinary_plan": item["flow"]["g"].tolist(),
                "solver": item["solver"]})
            for row in actual_rows:
                out = dict(row); out.update({"method": method, "date": dates[index],
                    "price_forecast": float(item["price"][int(row["slot"])])})
                if writer is None: writer = csv.DictWriter(ledger, fieldnames=list(out)); writer.writeheader()
                writer.writerow(out)
            if ordinal % 25 == 0 or ordinal == len(target_indices):
                print(method, ordinal, dates[index], round(sum(r["total_cost"] for r in daily if r["method"] == method), 2), flush=True)
    ledger.close()
    table(results / "daily_summary.csv", daily)
    with gzip.open(output / "plans.jsonl.gz", "wt", encoding="utf-8") as stream:
        for row in plan_records: stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    with gzip.open(output / "saa_plans.jsonl.gz", "wt", encoding="utf-8") as stream:
        for row in saa_records: stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    annual, quarterly, monthly = [], [], []
    for method in cfg["methods"]:
        method_rows = [row for row in daily if row["method"] == method]
        annual.append(period_row(method, "2025-02-01_to_2025-12-31", method_rows,
                                 actual_price[first:last+1]))
        quarter_labels = sorted({f"2025-Q{(int(row['date'][5:7])-1)//3+1}" for row in method_rows})
        for label in quarter_labels:
            chosen = [row for row in method_rows if f"2025-Q{(int(row['date'][5:7])-1)//3+1}" == label]
            idx = [dates.index(row["date"]) for row in chosen]
            quarterly.append(period_row(method, label, chosen, actual_price[idx]))
        month_labels = sorted({row["date"][:7] for row in method_rows})
        for label in month_labels:
            chosen = [row for row in method_rows if row["date"].startswith(label)]
            idx = [dates.index(row["date"]) for row in chosen]
            monthly.append(period_row(method, label, chosen, actual_price[idx]))
    table(results / "summary_tables.csv", annual); table(results / "quarterly_summary.csv", quarterly)
    table(results / "monthly_summary.csv", monthly)

    amap = {row["method"]: row for row in annual}; qmap = {(row["method"], row["period"]): row for row in quarterly}
    primary = cfg["primary_method"]; improvement = cfg["practical_improvement_fraction"]
    annual_adjusted_checks = {base: amap[primary]["inventory_adjusted_cost"] <= amap[base]["inventory_adjusted_cost"] * (1-improvement)
                              for base in ["mean7", "fixed"]}
    cash_check = amap[primary]["total_cost"] <= min(amap["mean7"]["total_cost"], amap["fixed"]["total_cost"]) + 1e-6
    quarter_checks = {}
    for label in sorted({row["period"] for row in quarterly}):
        better = min(qmap[("mean7", label)]["inventory_adjusted_cost"], qmap[("fixed", label)]["inventory_adjusted_cost"])
        value = qmap[(primary, label)]["inventory_adjusted_cost"]
        quarter_checks[label] = {"primary_cost": value, "better_baseline_cost": better,
            "within_worsening_tolerance": value <= better * (1 + cfg["quarter_worsening_tolerance_fraction"])}
    risk_check = not (amap[primary]["emergency_kwh"] > amap["mean7"]["emergency_kwh"] and
                      amap[primary]["paid_grid_spill_kwh"] > amap["mean7"]["paid_grid_spill_kwh"])
    model_pass = all(annual_adjusted_checks.values()) and cash_check and all(
        item["within_worsening_tolerance"] for item in quarter_checks.values()) and risk_check
    dump(output / "selection.json", {"frozen_primary": primary, "fallback": "mean7",
        "annual_adjusted_checks": annual_adjusted_checks, "annual_cash_check": cash_check,
        "quarter_checks": quarter_checks, "risk_energy_check": risk_check,
        "model_selection_pass": model_pass, "selected_delivery": primary if model_pass else "mean7",
        "oracle_excluded": True})
    information = []
    for row in plan_records:
        if row["causal"]:
            index = dates.index(row["date"]); changed = actual_price.copy(); changed[index:] = changed[index:] * 1.37 + 0.123
            count = 7 if row["method"] == "mean7" else 14
            delta = 0.0 if row["method"] == "fixed" else float(np.max(np.abs(
                actual_price[index-count:index].mean(axis=0) - changed[index-count:index].mean(axis=0))))
            information.append({"method": row["method"], "date": row["date"],
                "history_end": row["history_end"], "max_source_change": delta,
                "pass": row["history_end"] < row["date"] and delta <= 1e-12})
    dump(output / "information_checks.json", information)
    dump(output / "input_audit.json", {"dates_aligned": True, "days": len(target_indices),
        "slots": len(headers), "start": dates[first], "end": dates[last],
        "initial_energy": shared_states[cfg["start_date"]], "methods": cfg["methods"]})
    dump(output / "completion.json", {"methods": len(cfg["methods"]), "days_per_method": 334,
        "dispatch_rows": len(cfg["methods"])*334*144, "saa_records": len(saa_records),
        "information_checks": len(information), "model_selection_pass": model_pass,
        "selected_delivery": primary if model_pass else "mean7", "runtime_seconds": time.perf_counter()-started})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("output", type=Path)
    parser.add_argument("--price-input", required=True, type=Path); args = parser.parse_args()
    target = args.output.resolve()
    if not target.is_relative_to((ROOT / "experiments").resolve()): raise ValueError("Output must be under experiments/")
    run(target, args.price_input.resolve())


if __name__ == "__main__": main()
