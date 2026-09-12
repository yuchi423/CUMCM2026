"""Independently audit a completed Task 4 price-forecast PoC experiment."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import hashlib
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TOLERANCE = 1e-6
SUM_FIELDS = [
    "plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
    "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh",
]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def independent_price_forecast(
    method: str, prices: np.ndarray, index: int, fixed: np.ndarray,
    dates: list[str], config: dict,
) -> np.ndarray:
    """Rebuild forecasts without importing the experiment implementation."""
    if method == "fixed_attachment1":
        return fixed.copy()
    if method == "oracle":
        return prices[index].copy()
    if method == "lag1":
        return prices[index - 1].copy()
    if method.startswith("mean"):
        count = int(method.removeprefix("mean"))
        return prices[max(0, index - count):index].mean(axis=0)
    if method == "weekday35":
        weekday = date.fromisoformat(dates[index]).weekday()
        chosen = [j for j in range(max(0, index - 35), index)
                  if date.fromisoformat(dates[j]).weekday() == weekday]
        if len(chosen) < 2:
            chosen = list(range(max(0, index - 7), index))
        return prices[chosen].mean(axis=0)
    if method == "ewma14":
        history = prices[max(0, index - 14):index]
        alpha = float(config["ewma_alpha"])
        weights = (1.0 - alpha) ** np.arange(len(history) - 1, -1, -1)
        return np.average(history, axis=0, weights=weights)
    if method == "lag1_shift6h":
        return np.roll(prices[index - 1], int(config["negative_control_shift_slots"]))
    raise KeyError(method)


def read_prices(experiment: Path) -> tuple[list[str], np.ndarray]:
    snapshot_path = experiment / "input_prices.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    raw_hash = hashlib.sha256((ROOT / snapshot["source"]).read_bytes()).hexdigest()
    if raw_hash != snapshot["source_sha256"]:
        raise AssertionError("Price snapshot no longer matches raw Attachment 4")
    return snapshot["dates"], np.asarray(snapshot["prices"], dtype=float)


def audit(experiment: Path) -> dict:
    config = json.loads((ROOT / "configs/q4_price_poc.json").read_text(encoding="utf-8"))
    q2 = json.loads((ROOT / "experiments/q2-full-20260911-01/inputs.json").read_text(encoding="utf-8"))
    dates, prices = read_prices(experiment)
    if dates != q2["dates"]:
        raise AssertionError("Independent date alignment failed")
    fixed = np.asarray(q2["prices"], dtype=float)

    daily = load_csv(experiment / "results/daily_summary.csv")
    summary = {row["method"]: row for row in load_csv(experiment / "results/summary_tables.csv")}
    window_summary = {
        (row["method"], row["window"]): row
        for row in load_csv(experiment / "results/window_summary.csv")
    }
    information = json.loads((experiment / "information_checks.json").read_text(encoding="utf-8"))
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))

    expected_days = sum(
        (date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])).days + 1
        for window in config["windows"]
    )
    expected_rows = len(config["methods"]) * expected_days * 144
    groups: dict[tuple[str, str, str], list[dict[str, float]]] = defaultdict(list)
    row_count = 0
    maximum = defaultdict(float)
    totals = {method: defaultdict(float) for method in config["methods"]}
    with gzip.open(experiment / "dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        for raw in csv.DictReader(stream):
            row_count += 1
            row = {key: float(raw[key]) for key in [
                "slot", "price", "price_forecast", "plan_kwh", "load_kwh", "pv_kwh",
                "e_start", "e_end", "charge_kwh", "discharge_kwh", "pv_spill_kwh",
                "paid_grid_spill_kwh", "emergency_kwh", "plan_cost", "emergency_cost",
                "total_cost",
            ]}
            method = raw["method"]
            key = (method, raw["window"], raw["date"])
            groups[key].append(row)
            balance = (row["plan_kwh"] + row["pv_kwh"] + row["discharge_kwh"]
                       + row["emergency_kwh"] - row["load_kwh"] - row["charge_kwh"]
                       - row["pv_spill_kwh"] - row["paid_grid_spill_kwh"])
            state = (row["e_end"] - row["e_start"]
                     - config["charge_efficiency"] * row["charge_kwh"]
                     + row["discharge_kwh"] / config["discharge_efficiency"])
            plan_cost = row["price"] * row["plan_kwh"]
            emergency_cost = config["emergency_price_multiple"] * row["price"] * row["emergency_kwh"]
            maximum["balance"] = max(maximum["balance"], abs(balance))
            maximum["state"] = max(maximum["state"], abs(state))
            maximum["plan_cost"] = max(maximum["plan_cost"], abs(row["plan_cost"] - plan_cost))
            maximum["emergency_cost"] = max(maximum["emergency_cost"], abs(row["emergency_cost"] - emergency_cost))
            maximum["total_cost"] = max(
                maximum["total_cost"], abs(row["total_cost"] - plan_cost - emergency_cost))
            maximum["soc_bounds"] = max(
                maximum["soc_bounds"], config["energy_min"] - row["e_end"],
                row["e_end"] - config["energy_max"], 0.0)
            cap = config["power_kw"] * config["step_hours"]
            maximum["power"] = max(
                maximum["power"], row["charge_kwh"] - cap, row["discharge_kwh"] - cap, 0.0)
            maximum["negative"] = max(
                maximum["negative"],
                max(0.0, -min(row[name] for name in [
                    "plan_kwh", "load_kwh", "pv_kwh", "charge_kwh", "discharge_kwh",
                    "pv_spill_kwh", "paid_grid_spill_kwh", "emergency_kwh",
                ])))
            if row["charge_kwh"] > TOLERANCE and row["discharge_kwh"] > TOLERANCE:
                maximum["mutual_count"] += 1
            for field in SUM_FIELDS:
                totals[method][field] += row[field]

    maximum["row_count_error"] = abs(row_count - expected_rows)
    expected_groups = len(config["methods"]) * expected_days
    maximum["group_count_error"] = abs(len(groups) - expected_groups)
    for (method, window, label), rows in groups.items():
        if len(rows) != 144:
            maximum["slots_per_day_error"] = max(maximum["slots_per_day_error"], abs(len(rows) - 144))
            continue
        rows.sort(key=lambda row: row["slot"])
        maximum["slot_axis"] = max(
            maximum["slot_axis"], max(abs(row["slot"] - index) for index, row in enumerate(rows)))
        for previous, current in zip(rows, rows[1:]):
            maximum["within_day_soc_continuity"] = max(
                maximum["within_day_soc_continuity"], abs(previous["e_end"] - current["e_start"]))
        index = dates.index(label)
        expected = independent_price_forecast(method, prices, index, fixed, dates, config)
        recorded = np.asarray([row["price_forecast"] for row in rows])
        actual = np.asarray([row["price"] for row in rows])
        maximum["forecast_reconstruction"] = max(
            maximum["forecast_reconstruction"], float(np.max(np.abs(expected - recorded))))
        maximum["actual_price_alignment"] = max(
            maximum["actual_price_alignment"], float(np.max(np.abs(prices[index] - actual))))

    daily_groups = {(row["method"], row["window"], row["date"]): row for row in daily}
    maximum["daily_group_count_error"] = abs(len(daily_groups) - expected_groups)
    for key, rows in groups.items():
        if key not in daily_groups or len(rows) != 144:
            maximum["missing_daily_summary"] += 1
            continue
        source = daily_groups[key]
        for field in SUM_FIELDS:
            maximum[f"daily_{field}"] = max(
                maximum[f"daily_{field}"],
                abs(sum(row[field] for row in rows) - float(source[field])))
        maximum["daily_start"] = max(maximum["daily_start"], abs(rows[0]["e_start"] - float(source["e_start"])))
        maximum["daily_end"] = max(maximum["daily_end"], abs(rows[-1]["e_end"] - float(source["e_end"])))

    window_groups: dict[tuple[str, str], list[tuple[str, list[dict[str, float]]]]] = defaultdict(list)
    for (method, window, label), rows in groups.items():
        window_groups[(method, window)].append((label, rows))
    inventory_totals = defaultdict(float)
    maximum["window_group_count_error"] = abs(
        len(window_groups) - len(config["methods"]) * len(config["windows"]))
    for key, days in window_groups.items():
        days.sort(key=lambda item: item[0])
        if key not in window_summary:
            maximum["missing_window_summary"] += 1
            continue
        for (_, previous), (_, current) in zip(days, days[1:]):
            maximum["between_day_soc_continuity"] = max(
                maximum["between_day_soc_continuity"],
                abs(previous[-1]["e_end"] - current[0]["e_start"]))
        first_energy = days[0][1][0]["e_start"]
        last_energy = days[-1][1][-1]["e_end"]
        cash_cost = sum(row["total_cost"] for _, rows in days for row in rows)
        mean_price = float(np.mean([row["price"] for _, rows in days for row in rows]))
        adjusted = cash_cost + config["discharge_efficiency"] * mean_price * (first_energy - last_energy)
        source = window_summary[key]
        maximum["window_start"] = max(maximum["window_start"], abs(first_energy - float(source["e_start"])))
        maximum["window_end"] = max(maximum["window_end"], abs(last_energy - float(source["e_end"])))
        maximum["window_inventory_adjusted_cost"] = max(
            maximum["window_inventory_adjusted_cost"],
            abs(adjusted - float(source["inventory_adjusted_cost"])))
        inventory_totals[key[0]] += adjusted

    for method in config["methods"]:
        if method not in summary:
            maximum["missing_method_summary"] += 1
            continue
        for field in SUM_FIELDS:
            maximum[f"summary_{field}"] = max(
                maximum[f"summary_{field}"], abs(totals[method][field] - float(summary[method][field])))
        maximum["summary_inventory_adjusted_cost"] = max(
            maximum["summary_inventory_adjusted_cost"],
            abs(inventory_totals[method] - float(summary[method]["inventory_adjusted_cost"])))

    causal = set(config["methods"]) - {"oracle"}
    information_pass = (
        len(information) == len(config["methods"]) * expected_days
        and all(bool(row["pass"]) for row in information)
        and all((row["method"] in causal and float(row["max_change"]) == 0.0)
                or (row["method"] == "oracle" and float(row["max_change"]) > 0.0)
                for row in information)
    )
    plan_audit_max = max(float(row["max_plan_audit"]) for row in daily)
    plan_mutual_count = sum(int(row["plan_mutual_count"]) for row in daily)
    solver_failures = sum(row["solver"] not in {"LP", "MILP"} for row in daily)
    expected_scientific_pass = all(
        selection["later_checks"][period]["beats_fixed_control"]
        and selection["later_checks"][period]["beats_negative_control"]
        for period in ["validation", "evaluation"]
    )
    expected_recommendation = selection["selected"] if expected_scientific_pass else config["fallback_method"]
    selection_logic_pass = (
        bool(selection["scientific_target_pass"]) == expected_scientific_pass
        and selection["recommended_delivery_method"] == expected_recommendation
        and bool(selection["fallback_triggered"]) == (not expected_scientific_pass)
    )
    numerical_fields = {key: value for key, value in maximum.items() if key != "mutual_count"}
    passed = (
        all(value <= TOLERANCE for value in numerical_fields.values())
        and maximum["mutual_count"] == 0
        and information_pass
        and plan_audit_max <= TOLERANCE
        and plan_mutual_count == 0
        and solver_failures == 0
        and selection["selected"] in config["causal_candidates"]
        and selection_logic_pass
    )
    result = {
        "pass": passed,
        "expected_dispatch_rows": expected_rows,
        "actual_dispatch_rows": row_count,
        "expected_daily_groups": expected_groups,
        "actual_daily_groups": len(groups),
        "max_errors": dict(sorted(maximum.items())),
        "information_boundary_pass": information_pass,
        "plan_audit_max": plan_audit_max,
        "plan_mutual_count": plan_mutual_count,
        "solver_failures": solver_failures,
        "selected_is_causal_candidate": selection["selected"] in config["causal_candidates"],
        "selection_logic_pass": selection_logic_pass,
        "note": "Independent audit reconstructs prices from raw Attachment 4 and recomputes dispatch physics and costs.",
    }
    (experiment / "audit_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if not passed:
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    args = parser.parse_args()
    audit(args.experiment.resolve())


if __name__ == "__main__":
    main()
