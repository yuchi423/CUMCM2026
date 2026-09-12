"""Independently audit the matched price-supply scenario Q4 PoC."""
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
from q2_core import execute

TOLERANCE = 1e-6
SUM_FIELDS = [
    "plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
    "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh",
]
METHODS = [
    "fixed", "mean7", "mean14", "rolling_cost_selector",
    "load_scenario_selector", "joint_scenario_selector",
    "shuffled_scenario_selector", "oracle_plan_daily",
]
PLAN_CANDIDATES = ["fixed", "blend25", "blend50", "blend75", "mean14", "mean7"]
SELECTORS = METHODS[3:]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_prices(experiment: Path) -> tuple[list[str], np.ndarray]:
    snapshot = json.loads((experiment / "input_prices.json").read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256((ROOT / snapshot["source"]).read_bytes()).hexdigest()
    if actual_hash != snapshot["source_sha256"]:
        raise AssertionError("Price snapshot does not match raw Attachment 4")
    return snapshot["dates"], np.asarray(snapshot["prices"], dtype=float)


def load_jsonl_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def audit(experiment: Path) -> dict:
    config = json.loads((ROOT / "configs/q4_joint_scenario_poc.json").read_text(
        encoding="utf-8"))
    q2 = json.loads((ROOT / "experiments/q2-full-20260911-01/inputs.json").read_text(
        encoding="utf-8"))
    dates, prices = read_prices(experiment)
    if dates != q2["dates"]:
        raise AssertionError("Date alignment failed")
    load = np.asarray(q2["load"], dtype=float)
    pv = np.asarray(q2["pv"], dtype=float)
    fixed_price = np.asarray(q2["prices"], dtype=float)
    daily_rows = load_csv(experiment / "results/daily_summary.csv")
    daily = {(row["method"], row["window"], row["date"]): row for row in daily_rows}
    window_rows = {(row["method"], row["window"]): row for row in load_csv(
        experiment / "results/window_summary.csv")}
    summaries = {row["method"]: row for row in load_csv(
        experiment / "results/summary_tables.csv")}
    periods = {(row["method"], row["period"]): row for row in load_csv(
        experiment / "results/periods.csv")}
    score_rows = load_csv(experiment / "results/selector_scores.csv")
    shadow_rows = load_csv(experiment / "results/shadow_policy_costs.csv")
    candidate_records = load_jsonl_gz(experiment / "candidate_plans.jsonl.gz")
    information = json.loads((experiment / "information_checks.json").read_text(
        encoding="utf-8"))
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))

    expected_days = sum(
        (date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])).days + 1
        for window in config["windows"])
    expected_dispatch = len(METHODS) * expected_days * 144
    maximum = defaultdict(float)
    totals = {method: defaultdict(float) for method in METHODS}
    groups = defaultdict(list)
    row_count = 0
    with gzip.open(experiment / "dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        for raw in csv.DictReader(stream):
            row_count += 1
            method = raw["method"]
            row = {key: float(raw[key]) for key in [
                "slot", "price", "price_forecast", "plan_kwh", "load_kwh", "pv_kwh",
                "e_start", "e_end", "charge_kwh", "discharge_kwh", "pv_spill_kwh",
                "paid_grid_spill_kwh", "emergency_kwh", "plan_cost", "emergency_cost",
                "total_cost",
            ]}
            row["selected_plan"] = raw["selected_plan"]
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
            maximum["emergency_cost"] = max(
                maximum["emergency_cost"], abs(row["emergency_cost"] - emergency_cost))
            maximum["total_cost"] = max(
                maximum["total_cost"], abs(row["total_cost"] - plan_cost - emergency_cost))
            maximum["soc_bounds"] = max(
                maximum["soc_bounds"], config["energy_min"] - row["e_end"],
                row["e_end"] - config["energy_max"], 0.0)
            cap = config["power_kw"] * config["step_hours"]
            maximum["power"] = max(
                maximum["power"], row["charge_kwh"] - cap,
                row["discharge_kwh"] - cap, 0.0)
            if row["charge_kwh"] > TOLERANCE and row["discharge_kwh"] > TOLERANCE:
                maximum["mutual_count"] += 1
            for field in SUM_FIELDS:
                totals[method][field] += row[field]
    maximum["row_count"] = abs(row_count - expected_dispatch)
    expected_groups = len(METHODS) * expected_days
    maximum["group_count"] = abs(len(groups) - expected_groups)

    price_mean_cache = {}; supply_cache = {}

    def mean_price(index: int, days_count: int) -> np.ndarray:
        key = (index, days_count)
        if key not in price_mean_cache:
            price_mean_cache[key] = prices[max(0, index - days_count):index].mean(axis=0)
        return price_mean_cache[key]

    def candidate_price(index: int, candidate: str) -> np.ndarray:
        recent14 = mean_price(index, config["mean_history_days"])
        if candidate == "fixed":
            return fixed_price
        if candidate == "mean7":
            return mean_price(index, 7)
        if candidate == "mean14":
            return recent14
        alpha = {"blend25": .25, "blend50": .5, "blend75": .75}[candidate]
        return (1 - alpha) * fixed_price + alpha * recent14

    def supply(index: int) -> tuple[np.ndarray, np.ndarray]:
        if index not in supply_cache:
            weekday = date.fromisoformat(dates[index]).weekday()
            chosen = [old for old in range(max(0, index - 35), index)
                      if date.fromisoformat(dates[old]).weekday() == weekday]
            if len(chosen) < 2:
                chosen = list(range(max(0, index - 7), index))
            supply_cache[index] = (
                load[chosen].mean(axis=0), pv[max(0, index - 7):index].mean(axis=0))
        return supply_cache[index]

    def replay_cost(ordinary: np.ndarray, scenario_price: np.ndarray,
                    scenario_load: np.ndarray, scenario_pv: np.ndarray,
                    energy: float) -> float:
        rows, ending = execute(
            scenario_price, ordinary, scenario_load, scenario_pv, energy, config)
        cash = math.fsum(row["total_cost"] for row in rows)
        return float(cash + config["discharge_efficiency"] * float(
            np.mean(scenario_price)) * (energy - ending))

    inventory_totals = defaultdict(float)
    window_groups = defaultdict(list)
    for key, rows in groups.items():
        method, window, label = key
        rows.sort(key=lambda row: row["slot"])
        maximum["slots_per_day"] = max(maximum["slots_per_day"], abs(len(rows) - 144))
        if len(rows) != 144:
            continue
        maximum["slot_axis"] = max(maximum["slot_axis"], max(
            abs(row["slot"] - slot) for slot, row in enumerate(rows)))
        for previous, current in zip(rows, rows[1:]):
            maximum["within_day_continuity"] = max(
                maximum["within_day_continuity"], abs(previous["e_end"] - current["e_start"]))
        source = daily.get(key)
        if source is None:
            maximum["missing_daily"] += 1
        else:
            maximum["daily_selected_plan"] = max(
                maximum["daily_selected_plan"],
                0.0 if source["selected_plan"] == rows[0]["selected_plan"] else 1.0)
            for field in SUM_FIELDS:
                maximum[f"daily_{field}"] = max(
                    maximum[f"daily_{field}"], abs(math.fsum(row[field] for row in rows)
                                                    - float(source[field])))
        index = dates.index(label)
        expected_price = candidate_price(index, rows[0]["selected_plan"])
        maximum["forecast_reconstruction"] = max(
            maximum["forecast_reconstruction"], float(np.max(np.abs(
                expected_price - np.asarray([row["price_forecast"] for row in rows])))))
        maximum["actual_price_alignment"] = max(
            maximum["actual_price_alignment"], float(np.max(np.abs(
                prices[index] - np.asarray([row["price"] for row in rows])))))
        window_groups[(method, window)].append((label, rows))
    maximum["daily_count"] = abs(len(daily) - expected_groups)
    for key, days_group in window_groups.items():
        days_group.sort(key=lambda item: item[0])
        for (_, previous), (_, current) in zip(days_group, days_group[1:]):
            maximum["between_day_continuity"] = max(
                maximum["between_day_continuity"],
                abs(previous[-1]["e_end"] - current[0]["e_start"]))
        first_energy = days_group[0][1][0]["e_start"]
        last_energy = days_group[-1][1][-1]["e_end"]
        cash = math.fsum(row["total_cost"] for _, rows in days_group for row in rows)
        mean_actual = float(np.mean([row["price"] for _, rows in days_group for row in rows]))
        adjusted = cash + config["discharge_efficiency"] * mean_actual * (
            first_energy - last_energy)
        source = window_rows.get(key)
        if source is None:
            maximum["missing_window"] += 1
        else:
            maximum["window_inventory"] = max(
                maximum["window_inventory"], abs(adjusted - float(
                    source["inventory_adjusted_cost"])))
        inventory_totals[key[0]] += adjusted
    maximum["window_count"] = abs(len(window_groups) - len(METHODS) * len(config["windows"]))
    for method in METHODS:
        source = summaries.get(method)
        if source is None:
            maximum["missing_summary"] += 1
            continue
        for field in SUM_FIELDS:
            maximum[f"summary_{field}"] = max(
                maximum[f"summary_{field}"], abs(
                    totals[method][field] - float(source[field])))
        maximum["summary_inventory"] = max(
            maximum["summary_inventory"], abs(
                inventory_totals[method] - float(source["inventory_adjusted_cost"])))

    candidate_plans = {(row["selector"], row["target_date"], row["candidate"]): row
                       for row in candidate_records}
    scores = {(row["selector"], row["target_date"], row["candidate"]): row
              for row in score_rows}
    maximum["candidate_record_count"] = abs(len(candidate_plans) - len(SELECTORS) * expected_days * 6)
    maximum["score_record_count"] = abs(len(scores) - len(SELECTORS) * expected_days * 6)
    shadow = {(row["date"], row["candidate"]): float(row["inventory_adjusted_cost"])
              for row in shadow_rows}
    selector_logic_pass = True
    target_dates = sorted({row["date"] for row in daily_rows})
    for selector in SELECTORS:
        for label in target_dates:
            index = dates.index(label)
            history = list(range(index - config["scenario_history_days"], index))
            recomputed = {}
            for candidate in PLAN_CANDIDATES:
                record = candidate_plans[(selector, label, candidate)]
                ordinary = np.asarray(record["ordinary_plan"], dtype=float)
                energy = float(record["e_start"])
                maximum["candidate_history_end"] = max(
                    maximum["candidate_history_end"],
                    0.0 if record["history_end"] == dates[index - 1] else 1.0)
                expected_curve = candidate_price(index, candidate)
                maximum["candidate_price"] = max(
                    maximum["candidate_price"], float(np.max(np.abs(
                        np.asarray(record["forecast_price"]) - expected_curve))))
                if selector == "rolling_cost_selector":
                    values = [shadow[(dates[old], candidate)] for old in history]
                elif selector == "oracle_plan_daily":
                    values = [replay_cost(ordinary, prices[index], load[index], pv[index], energy)]
                else:
                    base_load, base_pv = supply(index)
                    values = []
                    for position, old in enumerate(history):
                        old_load_f, old_pv_f = supply(old)
                        scenario_load = np.maximum(0.0, base_load + load[old] - old_load_f)
                        scenario_pv = np.maximum(0.0, base_pv + pv[old] - old_pv_f)
                        if selector == "load_scenario_selector":
                            scenario_price = mean_price(index, config["mean_history_days"])
                        else:
                            price_day = old if selector == "joint_scenario_selector" else history[
                                (position + config["independent_price_shift_days"]) % len(history)]
                            error = prices[price_day] - mean_price(
                                price_day, config["mean_history_days"])
                            scenario_price = np.maximum(
                                config["price_floor"],
                                mean_price(index, config["mean_history_days"]) + error)
                        values.append(replay_cost(
                            ordinary, scenario_price, scenario_load, scenario_pv, energy))
                recomputed[candidate] = float(np.mean(values))
                maximum["selector_score"] = max(
                    maximum["selector_score"], abs(recomputed[candidate]
                    - float(scores[(selector, label, candidate)]["score"])))
            expected_choice = min(
                PLAN_CANDIDATES, key=lambda name: (recomputed[name], PLAN_CANDIDATES.index(name)))
            marked = [candidate for candidate in PLAN_CANDIDATES
                      if int(scores[(selector, label, candidate)]["chosen"]) == 1]
            actual_choices = {row["selected_plan"] for key, row in daily.items()
                              if key[0] == selector and key[2] == label}
            selector_logic_pass = selector_logic_pass and marked == [expected_choice]
            selector_logic_pass = selector_logic_pass and actual_choices == {expected_choice}

    information_pass = (
        len(information) == (len(METHODS) - 1) * expected_days
        and all(bool(row["pass"]) and row["history_end"] < row["date"]
                and abs(float(row[
                    "max_causal_price_source_change_after_target_future_perturbation"])) <= 1e-12
                and not bool(row["target_and_future_price_perturbation_changes_choice"])
                for row in information)
    )
    dev_costs = {name: float(periods[(name, "development")]["inventory_adjusted_cost"])
                 for name in config["candidate_order"]}
    raw_best = min(config["candidate_order"], key=lambda name: dev_costs[name])
    best = dev_costs[raw_best]
    eligible = [name for name in config["candidate_order"]
                if dev_costs[name] <= best * (1 + config["selection_tolerance_fraction"])]
    selected = min(eligible, key=config["candidate_order"].index)
    expected_later = {}
    for period in ["validation", "evaluation"]:
        selected_cost = float(periods[(selected, period)]["inventory_adjusted_cost"])
        mean7_cost = float(periods[("mean7", period)]["inventory_adjusted_cost"])
        fixed_cost = float(periods[("fixed", period)]["inventory_adjusted_cost"])
        shuffled_cost = float(periods[("shuffled_scenario_selector", period)][
            "inventory_adjusted_cost"])
        threshold = config["practical_improvement_fraction"]
        expected_later[period] = {
            "selected_cost": selected_cost, "mean7_cost": mean7_cost,
            "fixed_cost": fixed_cost, "shuffled_cost": shuffled_cost,
            "beats_mean7_threshold": selected_cost <= mean7_cost * (1 - threshold),
            "beats_fixed_threshold": selected_cost <= fixed_cost * (1 - threshold),
            "joint_beats_shuffled_if_selected": (
                selected != "joint_scenario_selector" or selected_cost < shuffled_cost),
        }
    scientific_pass = all(item["beats_mean7_threshold"]
                          and item["beats_fixed_threshold"]
                          and item["joint_beats_shuffled_if_selected"]
                          for item in expected_later.values())
    selection_pass = (
        selection["candidate_development_costs"] == dev_costs
        and selection["raw_best"] == raw_best
        and selection["eligible_within_tolerance"] == eligible
        and selection["selected"] == selected
        and selection["later_checks"] == expected_later
        and bool(selection["scientific_target_pass"]) == scientific_pass)
    plan_audit_max = max(float(row["max_plan_audit"]) for row in daily_rows)
    plan_mutual_count = sum(int(row["plan_mutual_count"]) for row in daily_rows)
    solver_failures = sum(row["solver"] not in {"LP", "MILP"} for row in daily_rows)
    numerical = {key: value for key, value in maximum.items() if key != "mutual_count"}
    passed = (all(value <= TOLERANCE for value in numerical.values())
              and maximum["mutual_count"] == 0 and selector_logic_pass
              and information_pass and selection_pass and plan_audit_max <= TOLERANCE
              and plan_mutual_count == 0 and solver_failures == 0)
    result = {
        "pass": passed, "expected_dispatch_rows": expected_dispatch,
        "actual_dispatch_rows": row_count, "expected_daily_groups": expected_groups,
        "actual_daily_groups": len(groups), "max_errors": dict(sorted(maximum.items())),
        "selector_score_reconstruction_pass": selector_logic_pass,
        "information_boundary_pass": information_pass,
        "information_checks": len(information), "plan_audit_max": plan_audit_max,
        "plan_mutual_count": plan_mutual_count, "solver_failures": solver_failures,
        "selection_logic_pass": selection_pass,
        "note": "Independent replay reconstructs all matched, load-only, shuffled and oracle scenario scores.",
    }
    (experiment / "audit_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    if not passed:
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    args = parser.parse_args(); audit(args.experiment.resolve())


if __name__ == "__main__":
    main()
