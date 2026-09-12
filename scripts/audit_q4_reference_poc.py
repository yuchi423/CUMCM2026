"""Independently audit the Q4 reference-method Detailed PoC."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TOL = 1e-6
SUMS = [
    "plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
    "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh",
    "preserved_emergency_kwh",
]
SPECS = {
    "fixed_greedy": ("fixed", "greedy", False),
    "mean7_greedy": ("mean7", "greedy", False),
    "netload_shape_greedy": ("netload_shape", "greedy", False),
    "mean7_value_update": ("mean7", "value", False),
    "netload_shape_value_update": ("netload_shape", "value", False),
    "mean7_value_shift6h": ("mean7", "value", True),
    "oracle_greedy": ("oracle", "greedy", False),
    "oracle_value_update": ("oracle", "value", False),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def supply(load: np.ndarray, pv: np.ndarray, dates: list[str], index: int) -> tuple[np.ndarray, np.ndarray]:
    weekday = date.fromisoformat(dates[index]).weekday()
    chosen = [j for j in range(max(0, index - 35), index)
              if date.fromisoformat(dates[j]).weekday() == weekday]
    if len(chosen) < 2:
        chosen = list(range(max(0, index - 7), index))
    return load[chosen].mean(axis=0), pv[max(0, index - 7):index].mean(axis=0)


class IndependentPrices:
    def __init__(self, prices, fixed, load, pv, dates, config):
        self.prices, self.fixed, self.load, self.pv = prices, fixed, load, pv
        self.dates, self.config = dates, config
        self.cache = {}

    def base(self, method: str, index: int) -> np.ndarray:
        key = (method, index)
        if key in self.cache:
            return self.cache[key]
        if method == "fixed":
            result = self.fixed.copy()
        elif method == "oracle":
            result = self.prices[index].copy()
        elif method == "mean7":
            result = self.prices[max(0, index - 7):index].mean(axis=0)
        elif method == "netload_shape":
            history = list(range(max(1, index - self.config["price_history_days"]), index))
            levels = np.asarray([np.mean(self.prices[j]) for j in history])
            net, shapes = [], []
            for j, level in zip(history, levels):
                load_f, pv_f = supply(self.load, self.pv, self.dates, j)
                net.append(np.sum(load_f - pv_f) / 100000.0)
                shapes.append(self.prices[j] / level)
            design = np.c_[np.ones(len(history)), np.asarray(net)]
            beta = np.linalg.lstsq(design, levels, rcond=None)[0]
            load_f, pv_f = supply(self.load, self.pv, self.dates, index)
            level = max(self.config["price_floor"],
                        float(beta[0] + beta[1] * np.sum(load_f - pv_f) / 100000.0))
            result = level * np.mean(np.asarray(shapes), axis=0)
        else:
            raise KeyError(method)
        result = np.maximum(self.config["price_floor"], result)
        self.cache[key] = result
        return result

    def updated(self, method: str, index: int, start: int, shift: bool) -> np.ndarray:
        base = self.base(method, index).copy()
        if shift:
            base = np.roll(base, self.config["negative_control_shift_slots"])
        if method == "oracle" or start == 0:
            return base
        residuals = []
        for j in range(max(1, index - self.config["price_history_days"]), index):
            old = self.base(method, j)
            if shift:
                old = np.roll(old, self.config["negative_control_shift_slots"])
            residuals.append(self.prices[j] - old)
        residuals = np.asarray(residuals)
        previous, following = residuals[:, :-1].ravel(), residuals[:, 1:].ravel()
        denominator = float(previous @ previous)
        raw = 0.0 if denominator <= 1e-15 else float(previous @ following / denominator)
        rho = float(np.clip(raw, self.config["price_error_rho_min"],
                            self.config["price_error_rho_max"]))
        error = self.prices[index, start - 1] - base[start - 1]
        base[start:] = np.maximum(
            self.config["price_floor"],
            base[start:] + rho ** np.arange(1, 145 - start) * error)
        return base


def audit(experiment: Path) -> dict:
    config = json.loads((ROOT / "configs/q4_reference_poc.json").read_text(encoding="utf-8"))
    q2 = json.loads((ROOT / "experiments/q2-full-20260911-01/inputs.json").read_text(encoding="utf-8"))
    snapshot = json.loads((experiment / "input_prices.json").read_text(encoding="utf-8"))
    if hashlib.sha256((ROOT / snapshot["source"]).read_bytes()).hexdigest() != snapshot["source_sha256"]:
        raise AssertionError("Price snapshot hash mismatch")
    dates = q2["dates"]
    if dates != snapshot["dates"]:
        raise AssertionError("Date alignment failed")
    load, pv = np.asarray(q2["load"]), np.asarray(q2["pv"])
    fixed, prices = np.asarray(q2["prices"]), np.asarray(snapshot["prices"])
    price_model = IndependentPrices(prices, fixed, load, pv, dates, config)
    daily = read_csv(experiment / "results/daily_summary.csv")
    summaries = {row["method"]: row for row in read_csv(experiment / "results/summary_tables.csv")}
    windows = {(row["method"], row["window"]): row
               for row in read_csv(experiment / "results/window_summary.csv")}
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    information = json.loads((experiment / "information_checks.json").read_text(encoding="utf-8"))
    expected_days = sum((date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1
                        for w in config["windows"])
    expected_rows = len(config["methods"]) * expected_days * 144
    groups = defaultdict(list)
    totals = {method: defaultdict(float) for method in config["methods"]}
    maximum = defaultdict(float)
    count = 0
    with gzip.open(experiment / "dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        for raw in csv.DictReader(stream):
            count += 1
            numeric = {}
            for field in ["slot", "price", "plan_kwh", "load_kwh", "pv_kwh", "e_start", "e_end",
                          "charge_kwh", "discharge_kwh", "pv_spill_kwh", "paid_grid_spill_kwh",
                          "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
                          "preserved_emergency_kwh", "price_forecast_0"]:
                numeric[field] = float(raw[field])
            numeric["issue_slot"] = int(raw["issue_slot"])
            numeric["controller_price"] = None if raw["controller_price"] == "" else float(raw["controller_price"])
            key = (raw["method"], raw["window"], raw["date"])
            groups[key].append(numeric)
            balance = (numeric["plan_kwh"] + numeric["pv_kwh"] + numeric["discharge_kwh"]
                       + numeric["emergency_kwh"] - numeric["load_kwh"] - numeric["charge_kwh"]
                       - numeric["pv_spill_kwh"] - numeric["paid_grid_spill_kwh"])
            state = (numeric["e_end"] - numeric["e_start"]
                     - config["charge_efficiency"] * numeric["charge_kwh"]
                     + numeric["discharge_kwh"] / config["discharge_efficiency"])
            plan_cost = numeric["price"] * numeric["plan_kwh"]
            emergency_cost = config["emergency_price_multiple"] * numeric["price"] * numeric["emergency_kwh"]
            maximum["balance"] = max(maximum["balance"], abs(balance))
            maximum["state"] = max(maximum["state"], abs(state))
            maximum["plan_cost"] = max(maximum["plan_cost"], abs(numeric["plan_cost"] - plan_cost))
            maximum["emergency_cost"] = max(maximum["emergency_cost"],
                                             abs(numeric["emergency_cost"] - emergency_cost))
            maximum["total_cost"] = max(maximum["total_cost"],
                abs(numeric["total_cost"] - plan_cost - emergency_cost))
            maximum["soc_bounds"] = max(maximum["soc_bounds"],
                config["energy_min"] - numeric["e_end"], numeric["e_end"] - config["energy_max"], 0.0)
            cap = config["power_kw"] * config["step_hours"]
            maximum["power"] = max(maximum["power"], numeric["charge_kwh"] - cap,
                                    numeric["discharge_kwh"] - cap, 0.0)
            if numeric["charge_kwh"] > TOL and numeric["discharge_kwh"] > TOL:
                maximum["mutual_count"] += 1
            available = min(max(0.0, numeric["load_kwh"] - numeric["pv_kwh"]
                                - min(numeric["plan_kwh"], max(0.0, numeric["load_kwh"] - numeric["pv_kwh"]))),
                            cap, max(0.0, numeric["e_start"] - config["energy_min"])
                            * config["discharge_efficiency"])
            expected_preserved = max(0.0, available - numeric["discharge_kwh"])
            maximum["preserved_reconstruction"] = max(
                maximum["preserved_reconstruction"],
                abs(expected_preserved - numeric["preserved_emergency_kwh"]))
            for field in SUMS:
                totals[raw["method"]][field] += numeric[field]
    maximum["row_count_error"] = abs(count - expected_rows)
    maximum["group_count_error"] = abs(len(groups) - len(config["methods"]) * expected_days)

    daily_map = {(row["method"], row["window"], row["date"]): row for row in daily}
    inventory_totals = defaultdict(float)
    window_groups = defaultdict(list)
    for key, rows in groups.items():
        rows.sort(key=lambda row: row["slot"])
        maximum["slots_per_day"] = max(maximum["slots_per_day"], abs(len(rows) - 144))
        maximum["slot_axis"] = max(maximum["slot_axis"],
            max(abs(row["slot"] - slot) for slot, row in enumerate(rows)))
        for left, right in zip(rows, rows[1:]):
            maximum["within_day_continuity"] = max(
                maximum["within_day_continuity"], abs(left["e_end"] - right["e_start"]))
        source = daily_map[key]
        for field in SUMS:
            maximum[f"daily_{field}"] = max(maximum[f"daily_{field}"],
                abs(math.fsum(row[field] for row in rows) - float(source[field])))
        method, window, label = key
        price_method, controller, shift = SPECS[method]
        index = dates.index(label)
        base = price_model.base(price_method, index)
        maximum["base_price_reconstruction"] = max(maximum["base_price_reconstruction"],
            float(np.max(np.abs(base - np.asarray([row["price_forecast_0"] for row in rows])))))
        maximum["actual_price_alignment"] = max(maximum["actual_price_alignment"],
            float(np.max(np.abs(prices[index] - np.asarray([row["price"] for row in rows])))))
        if controller == "value":
            for issue in config["issue_slots"]:
                expected = price_model.updated(price_method, index, issue, shift)
                segment = [row for row in rows if row["issue_slot"] == issue]
                recorded = np.asarray([row["controller_price"] for row in segment])
                maximum["controller_price_reconstruction"] = max(
                    maximum["controller_price_reconstruction"],
                    float(np.max(np.abs(expected[issue:issue + len(segment)] - recorded))))
        else:
            maximum["greedy_controller_price_nonempty"] += sum(
                row["controller_price"] is not None for row in rows)
        window_groups[(method, window)].append((label, rows))

    for key, days in window_groups.items():
        days.sort(key=lambda item: item[0])
        for (_, left), (_, right) in zip(days, days[1:]):
            maximum["between_day_continuity"] = max(
                maximum["between_day_continuity"], abs(left[-1]["e_end"] - right[0]["e_start"]))
        first, last = days[0][1][0]["e_start"], days[-1][1][-1]["e_end"]
        cash = math.fsum(row["total_cost"] for _, rows in days for row in rows)
        mean_price = float(np.mean([row["price"] for _, rows in days for row in rows]))
        adjusted = cash + config["discharge_efficiency"] * mean_price * (first - last)
        source = windows[key]
        maximum["window_inventory"] = max(maximum["window_inventory"],
            abs(adjusted - float(source["inventory_adjusted_cost"])))
        inventory_totals[key[0]] += adjusted
    for method in config["methods"]:
        for field in SUMS:
            maximum[f"summary_{field}"] = max(maximum[f"summary_{field}"],
                abs(totals[method][field] - float(summaries[method][field])))
        maximum["summary_inventory"] = max(maximum["summary_inventory"],
            abs(inventory_totals[method] - float(summaries[method]["inventory_adjusted_cost"])))

    expected_information = expected_days * 3 * 4
    info_pass = len(information) == expected_information and all(bool(row["pass"]) for row in information)
    plan_audit = max(float(row["max_audit"]) for row in daily)
    reserve_failures = sum(int(row["reserve_failures"]) for row in daily)
    development = selection["development_costs"]
    raw_best = min(development, key=development.get)
    selection_pass = raw_best == selection["raw_best"] and selection["selected"] in config["candidate_order"]
    numeric_pass = all(value <= TOL for key, value in maximum.items() if key != "mutual_count")
    passed = numeric_pass and maximum["mutual_count"] == 0 and info_pass and plan_audit <= TOL \
        and reserve_failures == 0 and selection_pass
    result = {
        "pass": passed, "expected_dispatch_rows": expected_rows, "actual_dispatch_rows": count,
        "expected_daily_groups": len(config["methods"]) * expected_days,
        "actual_daily_groups": len(groups), "max_errors": dict(sorted(maximum.items())),
        "information_boundary_pass": info_pass, "information_checks": len(information),
        "plan_and_reserve_audit_max": plan_audit, "reserve_failures": reserve_failures,
        "selection_logic_pass": selection_pass,
        "note": "Independent reconstruction covers price forecasts, intraday corrections, physics, costs, continuity, summaries and inventory adjustment.",
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
