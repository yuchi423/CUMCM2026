"""Independently audit a 334-day Question 4-3 delivery run."""
from __future__ import annotations

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
from run_q4_3_poc import decision_price
from run_q4_price_poc import read_price_matrix

STRATEGY = "rolling_margin_mean14"


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main(run_arg: str) -> None:
    run = (ROOT / run_arg).resolve()
    meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    cfg = meta["config"]
    price_dates, actual_price, _, _ = read_price_matrix(run / "input_prices.json")
    date_index = {d: i for i, d in enumerate(price_dates)}
    summary = rows(run / "results/summary_tables.csv")[0]
    daily = rows(run / "results/daily_summary.csv")
    errors, counts = defaultdict(float), defaultdict(int)

    def err(name: str, value: float) -> None:
        errors[name] = max(errors[name], abs(float(value)))

    dispatch = []
    dispatch_path = run / "details" / f"{STRATEGY}_dispatch.csv.gz"
    with gzip.open(dispatch_path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            dispatch.append(row)
            counts["dispatch_rows"] += 1
            e0, e1 = float(row["e_start"]), float(row["e_end"])
            charge, discharge = float(row["charge_kwh"]), float(row["discharge_kwh"])
            err("state", e1 - e0 - cfg["charge_efficiency"] * charge +
                discharge / cfg["discharge_efficiency"])
            err("energy_bounds", max(0.0, cfg["energy_min"] - e1, e1 - cfg["energy_max"]))
            err("power_bounds", max(0.0, charge - cfg["power_kw"] * cfg["step_hours"],
                                    discharge - cfg["power_kw"] * cfg["step_hours"]))
            err("ordinary_cost", float(row["ordinary_cost"]) -
                float(row["price"]) * float(row["ordinary_kwh"]))
            err("emergency_cost", float(row["emergency_cost"]) -
                cfg["emergency_price_multiple"] * float(row["price"]) * float(row["emergency_kwh"]))
            err("row_total", float(row["total_cost"]) -
                float(row["ordinary_cost"]) - float(row["emergency_cost"]))
            balance = (float(row["ordinary_kwh"]) + float(row["pv_kwh"]) +
                float(row["discharge_kwh"]) + float(row["emergency_kwh"]) -
                float(row["load_kwh"]) - float(row["charge_kwh"]) -
                float(row["pv_spill_kwh"]) - float(row["paid_grid_spill_kwh"]))
            err("physical_balance", balance)
            ordinary, load, pv = float(row["ordinary_kwh"]), float(row["load_kwh"]), float(row["pv_kwh"])
            cap = cfg["power_kw"] * cfg["step_hours"]
            expected_pv_load = min(load, pv)
            expected_grid_load = min(ordinary, load - expected_pv_load)
            shortage = max(0.0, load - expected_pv_load - expected_grid_load)
            if shortage > 0:
                expected_discharge = min(shortage, cap,
                    max(0.0, e0 - cfg["energy_min"]) * cfg["discharge_efficiency"])
                expected_emergency = shortage - expected_discharge
                expected_pv_charge = expected_grid_charge = 0.0
            else:
                available = min(cap, max(0.0, cfg["energy_max"] - e0) / cfg["charge_efficiency"])
                expected_pv_charge = min(pv - expected_pv_load, available)
                expected_grid_charge = min(ordinary - expected_grid_load,
                    max(0.0, available - expected_pv_charge))
                expected_discharge = expected_emergency = 0.0
            err("pv_load_priority", float(row["pv_load_kwh"]) - expected_pv_load)
            err("grid_load_priority", float(row["grid_load_kwh"]) - expected_grid_load)
            err("discharge_rule", discharge - expected_discharge)
            err("emergency_rule", float(row["emergency_kwh"]) - expected_emergency)
            err("pv_charge_priority", float(row["pv_charge_kwh"]) - expected_pv_charge)
            err("grid_charge_rule", float(row["grid_charge_kwh"]) - expected_grid_charge)
            if charge > 1e-8 and discharge > 1e-8:
                errors["simultaneous_charge_discharge"] = 1.0
            if int(row["slot"]) < int(row["issue_hour"]) * 6:
                errors["past_slot_rewritten"] = 1.0

    err("dispatch_count", len(dispatch) - 334 * 144)
    for left, right in zip(dispatch, dispatch[1:]):
        err("continuity", float(left["e_end"]) - float(right["e_start"]))
        if left["date"] == right["date"]:
            err("slot_order", int(right["slot"]) - int(left["slot"]) - 1)
        else:
            err("day_boundary", int(left["slot"]) - 143)
            err("new_day_slot", int(right["slot"]))

    update_rows = []
    with gzip.open(run / "details" / f"{STRATEGY}_updates.csv.gz",
                   "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            update_rows.append(row)
            counts["updates"] += 1
            if int(row["issue_hour"]) not in cfg["issue_hours"]:
                errors["invalid_issue"] = 1.0

    versions = []
    with gzip.open(run / "details" / f"{STRATEGY}_plan_versions.jsonl.gz",
                   "rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            versions.append(item)
            counts["versions"] += 1
            index, start = date_index[item["date"]], int(item["start_slot"])
            err("version_start", start - int(item["issue_hour"]) * 6)
            err("version_length", len(item["new"]) - (144 - start))
            expected_price, _ = decision_price(actual_price, index, start, cfg, False)
            err("decision_price", np.max(np.abs(np.asarray(item["decision_price"]) - expected_price)))
            err("settlement_price", np.max(np.abs(
                np.asarray(item["settlement_price"]) - actual_price[index, start:])))
            if item["price_history_end"] >= item["date"] or item["history_end"] >= item["date"]:
                errors["future_history"] = 1.0
            if item["old"] is None:
                fee = decision_fee = 0.0
            else:
                old, new = np.asarray(item["old"]), np.asarray(item["new"])
                fee = float(np.sum(0.5 * actual_price[index, start:] * np.abs(new - old)))
                decision_fee = float(np.sum(0.5 * expected_price * np.abs(new - old)))
            err("version_fee", fee - float(item["fee"]))
            err("version_decision_fee", decision_fee - float(item["decision_fee"]))

    err("version_count", len(versions) - 334 * 4)
    err("update_count", len(update_rows) - 334 * 4)
    err("update_fee_total", math.fsum(float(row["adjustment_cost"]) for row in update_rows) -
        math.fsum(float(item["fee"]) for item in versions))
    by_day = defaultdict(list)
    for item in versions:
        by_day[item["date"]].append(item)
    for group in by_day.values():
        group.sort(key=lambda item: item["start_slot"])
        if group[0]["old"] is not None:
            errors["first_version_has_old"] = 1.0
        for before, after in zip(group, group[1:]):
            offset = after["start_slot"] - before["start_slot"]
            err("version_chain", np.max(np.abs(
                np.asarray(before["new"])[offset:] - np.asarray(after["old"]))))

    total_ordinary = math.fsum(float(row["ordinary_cost"]) for row in dispatch)
    total_emergency = math.fsum(float(row["emergency_cost"]) for row in dispatch)
    total_adjustment = math.fsum(float(row["adjustment_cost"]) for row in update_rows)
    err("summary_ordinary", total_ordinary - float(summary["ordinary_cost"]))
    err("summary_emergency", total_emergency - float(summary["emergency_cost"]))
    err("summary_adjustment", total_adjustment - float(summary["adjustment_cost"]))
    err("summary_total", total_ordinary + total_emergency + total_adjustment -
        float(summary["total_cost"]))
    err("daily_to_summary", math.fsum(float(row["total_cost"]) for row in daily) -
        float(summary["total_cost"]))
    err("daily_count", len(daily) - 334)

    preflight = json.loads((run / "preflight_checks.json").read_text(encoding="utf-8"))
    if not preflight["future_price_boundary_pass"]:
        errors["information_boundary"] = 1.0
    passed = all(value <= 1e-6 for value in errors.values())
    result = {
        "pass": passed, "counts": dict(counts), "max_errors": dict(errors),
        "scope": "Independent physical, price-source, cost, plan-version, continuity and summary reconstruction",
        "future_price_boundary_pass": preflight["future_price_boundary_pass"]
    }
    (run / "audit_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise AssertionError(result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
