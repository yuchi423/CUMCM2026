"""Independently audit a Question 4-3 Cheap PoC run."""
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
from run_q4_3_poc import VARIANTS, decision_price, gate
from run_q4_price_poc import read_price_matrix


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main(run_arg: str) -> None:
    run = (ROOT / run_arg).resolve()
    meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    cfg = meta["config"]
    price_dates, actual_price, _, _ = read_price_matrix(run / "input_prices.json")
    date_index = {d: i for i, d in enumerate(price_dates)}
    summaries = {row["strategy"]: row for row in rows(run / "results/summary_tables.csv")}
    daily = rows(run / "results/daily_summary.csv")
    periods = rows(run / "results/periods.csv")
    selection = json.loads((run / "selection.json").read_text(encoding="utf-8"))
    errors, counts = defaultdict(float), defaultdict(int)

    def err(name: str, value: float) -> None:
        errors[name] = max(errors[name], abs(float(value)))

    specs = {row["name"]: row for row in VARIANTS}
    for strategy, spec in specs.items():
        dispatch = []
        with gzip.open(run / "details" / f"{strategy}_dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                dispatch.append(row); counts["dispatch_rows"] += 1
                e0, e1 = float(row["e_start"]), float(row["e_end"])
                charge, discharge = float(row["charge_kwh"]), float(row["discharge_kwh"])
                err("state", e1 - e0 - cfg["charge_efficiency"] * charge + discharge / cfg["discharge_efficiency"])
                err("energy_bounds", max(0.0, cfg["energy_min"] - e1, e1 - cfg["energy_max"]))
                err("ordinary_cost", float(row["ordinary_cost"]) - float(row["price"]) * float(row["ordinary_kwh"]))
                err("emergency_cost", float(row["emergency_cost"]) - cfg["emergency_price_multiple"] *
                    float(row["price"]) * float(row["emergency_kwh"]))
                err("row_total", float(row["total_cost"]) - float(row["ordinary_cost"]) - float(row["emergency_cost"]))
                balance = (float(row["ordinary_kwh"]) + float(row["pv_kwh"]) + float(row["discharge_kwh"]) +
                    float(row["emergency_kwh"]) - float(row["load_kwh"]) - float(row["charge_kwh"]) -
                    float(row["pv_spill_kwh"]) - float(row["paid_grid_spill_kwh"]))
                err("physical_balance", balance)
                ordinary, load, pv = float(row["ordinary_kwh"]), float(row["load_kwh"]), float(row["pv_kwh"])
                cap = cfg["power_kw"] * cfg["step_hours"]
                expected_pv_load = min(load, pv)
                expected_grid_load = min(ordinary, load - expected_pv_load)
                shortage = max(0.0, load - expected_pv_load - expected_grid_load)
                if shortage > 0:
                    expected_discharge = min(shortage, cap, max(0.0, e0 - cfg["energy_min"]) * cfg["discharge_efficiency"])
                    expected_emergency = shortage - expected_discharge
                    expected_pv_charge = expected_grid_charge = 0.0
                else:
                    available = min(cap, max(0.0, cfg["energy_max"] - e0) / cfg["charge_efficiency"])
                    expected_pv_charge = min(pv - expected_pv_load, available)
                    expected_grid_charge = min(ordinary - expected_grid_load, max(0.0, available - expected_pv_charge))
                    expected_discharge = expected_emergency = 0.0
                err("pv_load_priority", float(row["pv_load_kwh"]) - expected_pv_load)
                err("grid_load_priority", float(row["grid_load_kwh"]) - expected_grid_load)
                err("discharge_rule", float(row["discharge_kwh"]) - expected_discharge)
                err("emergency_rule", float(row["emergency_kwh"]) - expected_emergency)
                err("pv_charge_priority", float(row["pv_charge_kwh"]) - expected_pv_charge)
                err("grid_charge_rule", float(row["grid_charge_kwh"]) - expected_grid_charge)
                if int(row["slot"]) < int(row["issue_hour"]) * 6:
                    errors["past_slot_rewritten"] = 1.0
        if len(dispatch) != 84 * 144:
            errors["dispatch_count"] = max(errors["dispatch_count"], abs(len(dispatch) - 84 * 144))
        for left, right in zip(dispatch, dispatch[1:]):
            same = left["window"] == right["window"]
            consecutive = int(left["slot"]) + 1 == int(right["slot"]) or (int(left["slot"]) == 143 and int(right["slot"]) == 0)
            if same and consecutive:
                err("continuity", float(left["e_end"]) - float(right["e_start"]))

        update_rows = []
        with gzip.open(run / "details" / f"{strategy}_updates.csv.gz", "rt", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                update_rows.append(row); counts["updates"] += 1
                if int(row["issue_hour"]) not in spec["issues"]:
                    errors["invalid_issue"] = 1.0
        versions = []
        with gzip.open(run / "details" / f"{strategy}_plan_versions.jsonl.gz", "rt", encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line); versions.append(item); counts["versions"] += 1
                index, start = date_index[item["date"]], int(item["start_slot"])
                err("version_start", start - int(item["issue_hour"]) * 6)
                err("version_length", len(item["new"]) - (144 - start))
                err("settlement_price", np.max(np.abs(np.asarray(item["settlement_price"]) - actual_price[index, start:])))
                expected_price, expected_bias = decision_price(actual_price, index, start, cfg, bool(spec.get("price_update")))
                err("decision_price", np.max(np.abs(np.asarray(item["decision_price"]) - expected_price)))
                err("price_bias", float(item["price_bias"]) - expected_bias)
                if item["history_end"] >= item["date"]:
                    errors["future_history"] = 1.0
                if item["old"] is None:
                    fee = decision_fee = 0.0
                else:
                    old, new = np.asarray(item["old"]), np.asarray(item["new"])
                    fee = float(np.sum(0.5 * actual_price[index, start:] * np.abs(new - old)))
                    decision_fee = float(np.sum(0.5 * expected_price * np.abs(new - old)))
                err("version_fee", fee - float(item["fee"]))
                err("version_decision_fee", decision_fee - float(item["decision_fee"]))
        err("update_version_count", len(update_rows) - len(versions))
        err("update_fee_total", math.fsum(float(row["adjustment_cost"]) for row in update_rows) -
            math.fsum(float(item["fee"]) for item in versions))
        by_day = defaultdict(list)
        for item in versions:
            by_day[(item["window"], item["date"])].append(item)
        for group in by_day.values():
            group.sort(key=lambda item: item["start_slot"])
            if group[0]["old"] is not None:
                errors["first_version_has_old"] = 1.0
            for before, after in zip(group, group[1:]):
                offset = after["start_slot"] - before["start_slot"]
                err("version_chain", np.max(np.abs(np.asarray(before["new"])[offset:] - np.asarray(after["old"]))))

        expected = summaries[strategy]
        total_ordinary = math.fsum(float(row["ordinary_cost"]) for row in dispatch)
        total_emergency = math.fsum(float(row["emergency_cost"]) for row in dispatch)
        total_adjustment = math.fsum(float(row["adjustment_cost"]) for row in update_rows)
        err("summary_ordinary", total_ordinary - float(expected["ordinary_cost"]))
        err("summary_emergency", total_emergency - float(expected["emergency_cost"]))
        err("summary_adjustment", total_adjustment - float(expected["adjustment_cost"]))
        err("summary_total", total_ordinary + total_emergency + total_adjustment - float(expected["total_cost"]))

    for summary in summaries.values():
        selected = [row for row in daily if row["strategy"] == summary["strategy"]]
        err("daily_to_summary_total", math.fsum(float(row["total_cost"]) for row in selected) - float(summary["total_cost"]))

    period_map = {(row["strategy"], row["period"]): {k: (float(v) if k not in {"strategy", "period"} else v)
        for k, v in row.items()} for row in periods}
    baseline, candidate, updated = selection["baseline"], selection["candidate_a"], selection["candidate_b"]
    for period in ["validation", "evaluation"]:
        expected_a = gate(period_map[(candidate, period)], period_map[(baseline, period)], cfg)
        expected_b = gate(period_map[(updated, period)], period_map[(candidate, period)], cfg)
        for key in ["inventory_cost_improvement_fraction", "cvar90_improvement_fraction", "cash_worsening_fraction"]:
            err("selection_a_" + key, expected_a[key] - selection["candidate_a_period_checks"][period][key])
            err("selection_b_" + key, expected_b[key] - selection["candidate_b_period_checks"][period][key])
        if expected_a["pass"] != selection["candidate_a_period_checks"][period]["pass"]:
            errors["selection_a_pass"] = 1.0
        if expected_b["pass"] != selection["candidate_b_period_checks"][period]["pass"]:
            errors["selection_b_pass"] = 1.0

    preflight = json.loads((run / "preflight_checks.json").read_text(encoding="utf-8"))
    shuffle = json.loads((run / "shuffle_checks.json").read_text(encoding="utf-8"))
    if not preflight["future_price_boundary_pass"] or not shuffle["pass"]:
        errors["information_or_negative_control"] = 1.0
    passed = all(value <= 1e-6 for value in errors.values())
    result = {"pass": passed, "counts": dict(counts), "max_errors": dict(errors),
        "scope": "Independent physical, price-source, cost, plan-version and selection reconstruction",
        "future_price_suffix_perturbation_pass": preflight["future_price_boundary_pass"],
        "shuffled_marginal_preservation_pass": shuffle["pass"]}
    (run / "audit_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise AssertionError(result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
