"""Run the predeclared two-week Q2 PoC; model outputs are not full-year deliverables."""
import argparse
import csv
import hashlib
import itertools
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy

from q2_core import execute, forecast, margin, plan, solve_once, step

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False,
                              default=lambda x: x.tolist() if hasattr(x, "tolist") else str(x)) + "\n", encoding="utf-8")


def csv_out(path, rows):
    if not rows:
        raise ValueError("Do not silently omit empty output schemas")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def decide(load, pv, index, initial, price, config, strategy):
    # The only forecast inputs are strict date prefixes.
    lf, vf = forecast(load[:index], pv[:index], config["forecast_days"])
    extra = np.zeros_like(lf)
    if strategy == "q80_margin":
        errors = []
        for past in range(max(1, index-config["residual_days"]), index):
            lp, vp = forecast(load[:past], pv[:past], config["forecast_days"])
            errors.append((load[past]-pv[past])-(lp-vp))
        extra = margin(errors, config)
    flow, info = plan(price, lf+extra, vf, initial, config)
    return flow, info, lf, vf, extra


def unit_checks(config):
    checks = []
    def record(name, condition, **detail):
        assert condition, name
        checks.append({"name": name, "pass": True, **detail})
    r = step(100, 120, 0, config["energy_min"], config)
    record("100_plan_plus_20_emergency_costs_200", abs(r["emergency_kwh"]-20)<1e-9)
    costs, _ = execute([1], [100], [120], [0], config["energy_min"], config)
    record("emergency_price_is_5_not_6", costs[0]["total_cost"] == 200)
    r = step(100, 20, 0, config["energy_max"], config)
    record("unused_plan_is_paid_not_refunded", r["paid_grid_spill_kwh"] == 80 and r["plan_kwh"] == 100)
    r = step(100, 0, 100, 1200, config)
    r2 = step(0, 162, 0, r["e_end"], config)
    record("pv_plus_grid_topup_and_roundtrip", abs(r["pv_charge_kwh"]-100)<1e-9 and
           abs(r["grid_charge_kwh"]-100)<1e-9 and abs(r2["e_end"]-1200)<1e-9 and r2["emergency_kwh"]<1e-9)
    r = step(200, 0, 100, config["energy_max"]-90, config)
    record("pv_fills_headroom_before_paid_grid", abs(r["pv_charge_kwh"]-100)<1e-9 and
           r["grid_charge_kwh"]<1e-9 and abs(r["paid_grid_spill_kwh"]-200)<1e-9)
    r = step(0, 2000, 0, config["energy_max"], config)
    record("power_limited_emergency_and_no_emergency_charge", abs(r["discharge_kwh"]-5000/6)<1e-8 and
           r["emergency_kwh"] > 0 and r["charge_kwh"] == 0)

    small = {**config, "energy_min":0., "energy_max":200., "power_kw":1800., "planned_terminal_energy":0.}
    for name, p, l, v, expected in [
        ("exact_grid_efficiency", [1,2], [0,81], [0,0], 100),
        ("exact_pv_and_grid_topup", [.1,1], [0,162], [100,0], 10),
    ]:
        arrays = [np.array(x, dtype=float) for x in [p,l,v]]
        f, info = plan(*arrays, 0., small)
        replay, end = execute(arrays[0], f["g"], arrays[1], arrays[2], 0., small)
        record(name, abs(info["objective"]-expected)<1e-6 and abs(end)<1e-6,
               expected_cost=expected, actual_cost=info["objective"])
        record(name+"_perfect_forecast_replay", max(r["emergency_kwh"] for r in replay)<1e-6 and
               abs(sum(r["total_cost"] for r in replay)-expected)<1e-6)
    small["energy_max"] = 90.
    p,l,v = [np.array(x, dtype=float) for x in [[.5,2,3],[0,20,70],[120,0,0]]]
    _, integer = solve_once(p,l,v,0,0,small,strict=True)
    objectives = []
    for bits in itertools.product([0,1], repeat=4):
        fixed = {15+i:bits[i] for i in range(3)}; fixed[18] = bits[3]
        try:
            _, result = solve_once(p,l,v,0,0,small,strict=True,fixed=fixed)
            objectives.append(result["objective"])
        except RuntimeError as error:
            if "status 2:" not in str(error): raise
    record("all_16_modes_continuous_exact_lp", abs(min(objectives)-integer["objective"])<1e-6 and
           abs(integer["objective"]-18)<1e-6, actual_cost=integer["objective"], enumeration_best=min(objectives))
    return checks


def main(run):
    begin = time.perf_counter()
    if (run / "run.json").exists(): raise FileExistsError("Use a new output directory")
    config = json.loads((ROOT / "configs/q2_poc.json").read_text(encoding="utf-8"))
    source = json.loads((run / "inputs.json").read_text(encoding="utf-8"))
    load, pv, price = [np.array(source[k], dtype=float) for k in ["load", "pv", "prices"]]
    dates = source["dates"]
    start = dates.index(config["evaluation_start"])
    assert dates[-1] == config["evaluation_end"] and start > 0
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    checks = unit_checks(config)
    dump(run / "small_checks.json", checks)
    print(f"Analytical / exhaustive checks: PASS ({len(checks)})", flush=True)
    rows, summaries, schedules, solver_records = [], [], [], []

    def one_day(index, strategy, energy):
        if index == 0:
            f = {"g": np.zeros(144)}
            info = {"formulation":"COLD_START", "objective":0., "status":0, "seconds":0.}
            lf = vf = extra = np.zeros(144)
        else:
            f, info, lf, vf, extra = decide(load, pv, index, energy, price, config, strategy)
        actual, end = execute(price, f["g"], load[index], pv[index], energy, config)
        for t, r in enumerate(actual):
            r.update({"strategy":strategy, "date":dates[index],
                      "forecast_load_kwh":float(lf[t]) if index else None,
                      "forecast_pv_kwh":float(vf[t]) if index else None,
                      "margin_kwh":float(extra[t]), "history_last_date":dates[index-1] if index else "NONE"})
            rows.append(r)
        planned = {"strategy":strategy, "date":dates[index], "e_start":energy,
                   "e_end_actual":end, "g":f["g"].tolist(), "forecast_load":lf.tolist(),
                   "forecast_pv":vf.tolist(), "margin":extra.tolist(),
                   "history_last_date":dates[index-1] if index else "NONE"}
        if index:
            planned.update({"planned_charge":f["c"].tolist(), "planned_discharge":f["d"].tolist(),
                            "planned_energy":f["e"].tolist(), "planned_spill":f["s"].tolist()})
        schedules.append(planned)
        summary = {"strategy":strategy, "date":dates[index], "e_start":energy, "e_end":end,
                   "plan_kwh":sum(r["plan_kwh"] for r in actual),
                   "emergency_kwh":sum(r["emergency_kwh"] for r in actual),
                   "load_kwh":sum(r["load_kwh"] for r in actual),
                   "plan_cost":sum(r["plan_cost"] for r in actual),
                   "emergency_cost":sum(r["emergency_cost"] for r in actual),
                   "total_cost":sum(r["total_cost"] for r in actual),
                   "pv_spill_kwh":sum(r["pv_spill_kwh"] for r in actual),
                   "paid_grid_spill_kwh":sum(r["paid_grid_spill_kwh"] for r in actual),
                   "charge_kwh":sum(r["charge_kwh"] for r in actual),
                   "discharge_kwh":sum(r["discharge_kwh"] for r in actual),
                   "emergency_slots":sum(r["emergency_kwh"]>1e-6 for r in actual),
                   "solver":info["formulation"], "solve_seconds":info["seconds"]}
        summaries.append(summary)
        solver_records.append({"date":dates[index], "strategy":strategy, **info})
        return end

    energy = config["initial_energy"]
    for index in range(start):
        energy = one_day(index, "warmup", energy)
        if (index+1) % 7 == 0: print(f"Warmup completed: {dates[index]}", flush=True)
    shared_initial = energy
    for strategy in ["baseline", "q80_margin"]:
        energy = shared_initial
        for index in range(start, len(dates)):
            energy = one_day(index, strategy, energy)
        print(f"{strategy}: {len(dates)-start} evaluation days completed", flush=True)

    # Deliberately change current and future days, preserving the legitimate past.
    index = start
    la, pa = load.copy(), pv.copy()
    la[index:] = la[index:]*3+300; pa[index:] = pa[index:]*.1
    leakage = []
    for strategy in ["baseline", "q80_margin"]:
        original = decide(load,pv,index,shared_initial,price,config,strategy)
        poisoned = decide(la,pa,index,shared_initial,price,config,strategy)
        gap = float(np.max(np.abs(original[0]["g"]-poisoned[0]["g"])))
        assert gap < 1e-7
        leakage.append({"test":"current_and_future_days_do_not_change_plan", "strategy":strategy,
                        "max_plan_difference_kwh":gap, "pass":True})
        correct, eend = execute(price, original[0]["g"], original[2]+original[4], original[3], shared_initial, config)
        assert max(r["emergency_kwh"] for r in correct)<1e-6
        assert abs(eend-config["planned_terminal_energy"])<1e-6
        leakage.append({"test":"perfect_planning_scenario_replays_without_emergency", "strategy":strategy, "pass":True})
        actual, _ = execute(price, original[0]["g"], load[index], pv[index], shared_initial, config)
        modified_l, modified_v = load[index].copy(), pv[index].copy()
        modified_l[72:] += 500; modified_v[72:] *= .1
        poisoned_actual, _ = execute(price, original[0]["g"], modified_l, modified_v, shared_initial, config)
        difference = max(abs(a[k]-b[k]) for a,b in zip(actual[:72], poisoned_actual[:72]) for k in a)
        assert difference < 1e-8
        leakage.append({"test":"future_slots_do_not_change_past_actions", "strategy":strategy,
                        "max_difference":difference, "pass":True})
    dump(run / "information_checks.json", leakage)
    csv_out(run / "dispatch.csv", rows); csv_out(run / "daily_summary.csv", summaries)
    dump(run / "plans.json", schedules); dump(run / "solver_records.json", solver_records)
    totals = []
    for strategy in ["baseline", "q80_margin"]:
        days = [r for r in summaries if r["strategy"] == strategy]
        total = {"strategy":strategy, "days":len(days), "e_start":days[0]["e_start"], "e_end":days[-1]["e_end"]}
        for key in ["plan_cost","emergency_cost","total_cost","plan_kwh","emergency_kwh","load_kwh",
                    "pv_spill_kwh","paid_grid_spill_kwh","charge_kwh","discharge_kwh","emergency_slots"]:
            total[key] = sum(r[key] for r in days)
        for label, value in [("min",min(price)),("mean",float(np.mean(price))),("max",max(price))]:
            rate = config["discharge_efficiency"]*value
            total[f"inventory_adjusted_{label}_price"] = total["total_cost"] + rate*(total["e_start"]-total["e_end"])
        total["milp_days"] = sum(r["solver"] == "MILP" for r in days)
        total["emergency_fraction_of_load"] = total["emergency_kwh"]/total["load_kwh"]
        totals.append(total)
    dump(run / "totals.json", totals)
    metadata = {"scope":"Two-week PoC plus 31-day warmup; not a full-year or final-model approval",
                "code_commit":commit, "config":config, "python":platform.python_version(),
                "numpy":np.__version__, "scipy":scipy.__version__, "platform":platform.platform(),
                "command":"python scripts/run_q2_poc.py --run " + str(run.relative_to(ROOT)),
                "input_hashes":source["input_hashes"], "snapshot_sha256":hashlib.sha256((run/"inputs.json").read_bytes()).hexdigest(),
                "recorded_at":datetime.now(timezone.utc).isoformat(), "runtime_seconds":time.perf_counter()-begin,
                "scope_rows":len(rows), "evaluation_days":len(dates)-start, "warmup_days":start,
                "analytical_checks":len(checks), "information_checks":len(leakage),
                "shared_evaluation_initial_energy":shared_initial,
                "scientific_validity":"NOT REQUIRED", "independent_audit":"PENDING",
                "full_required_delivery":"PARTIAL", "final_model_approval":"NOT_GRANTED"}
    dump(run / "run.json", metadata)
    print(json.dumps(totals, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args(); run = args.run.resolve()
    assert run.is_relative_to((ROOT / "experiments").resolve())
    assert (run / "inputs.json").is_file()
    try:
        main(run)
    except Exception as error:
        dump(run / "failure.json", {"error":repr(error), "time":datetime.now(timezone.utc).isoformat()})
        raise
