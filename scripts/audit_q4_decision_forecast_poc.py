"""Independently audit a completed decision-focused Q4 price PoC."""
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
TOLERANCE = 1e-6
SUM_FIELDS = [
    "plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
    "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh",
]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_prices(experiment: Path) -> tuple[list[str], np.ndarray]:
    snapshot = json.loads((experiment / "input_prices.json").read_text(encoding="utf-8"))
    raw_hash = hashlib.sha256((ROOT / snapshot["source"]).read_bytes()).hexdigest()
    if raw_hash != snapshot["source_sha256"]:
        raise AssertionError("Price snapshot does not match raw Attachment 4")
    return snapshot["dates"], np.asarray(snapshot["prices"], dtype=float)


def alpha_name(alpha: float) -> str:
    return f"blend_a{int(round(alpha * 100)):03d}"


def audit(experiment: Path) -> dict:
    config = json.loads((ROOT / "configs/q4_decision_forecast_poc.json").read_text(
        encoding="utf-8"))
    q2 = json.loads((ROOT / "experiments/q2-full-20260911-01/inputs.json").read_text(
        encoding="utf-8"))
    dates, prices = read_prices(experiment)
    if dates != q2["dates"]:
        raise AssertionError("Date alignment failed")
    fixed = np.asarray(q2["prices"], dtype=float)
    alphas = [float(value) for value in config["blend_alphas"]]
    alpha_methods = [alpha_name(alpha) for alpha in alphas]
    methods = alpha_methods + [
        "mean7", "rolling_cost_selector", "rolling_mae_selector", "oracle_alpha_daily",
    ]
    causal_methods = set(methods) - {"oracle_alpha_daily"}

    daily_rows = load_csv(experiment / "results/daily_summary.csv")
    daily = {(row["method"], row["window"], row["date"]): row for row in daily_rows}
    windows = {(row["method"], row["window"]): row for row in load_csv(
        experiment / "results/window_summary.csv")}
    summaries = {row["method"]: row for row in load_csv(
        experiment / "results/summary_tables.csv")}
    periods = {(row["method"], row["period"]): row for row in load_csv(
        experiment / "results/periods.csv")}
    selector_rows = load_csv(experiment / "results/selector_scores.csv")
    shadow_rows = load_csv(experiment / "results/shadow_daily.csv")
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    information = json.loads((experiment / "information_checks.json").read_text(
        encoding="utf-8"))

    expected_days = sum(
        (date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])).days + 1
        for window in config["windows"])
    expected_rows = len(methods) * expected_days * 144
    maximum = defaultdict(float)
    totals = {method: defaultdict(float) for method in methods}
    groups: dict[tuple[str, str, str], list[dict[str, float]]] = defaultdict(list)
    row_count = 0
    with gzip.open(experiment / "dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        for raw in csv.DictReader(stream):
            row_count += 1
            method = raw["method"]
            if method not in methods:
                maximum["unknown_method"] += 1
                continue
            row = {key: float(raw[key]) for key in [
                "slot", "price", "price_forecast", "plan_kwh", "load_kwh", "pv_kwh",
                "e_start", "e_end", "charge_kwh", "discharge_kwh", "pv_spill_kwh",
                "paid_grid_spill_kwh", "emergency_kwh", "plan_cost", "emergency_cost",
                "total_cost",
            ]}
            row["selected_alpha"] = None if raw["selected_alpha"] == "" else float(
                raw["selected_alpha"])
            key = (method, raw["window"], raw["date"])
            groups[key].append(row)
            balance = (row["plan_kwh"] + row["pv_kwh"] + row["discharge_kwh"]
                       + row["emergency_kwh"] - row["load_kwh"] - row["charge_kwh"]
                       - row["pv_spill_kwh"] - row["paid_grid_spill_kwh"])
            state = (row["e_end"] - row["e_start"]
                     - config["charge_efficiency"] * row["charge_kwh"]
                     + row["discharge_kwh"] / config["discharge_efficiency"])
            plan_cost = row["price"] * row["plan_kwh"]
            emergency_cost = (config["emergency_price_multiple"] * row["price"]
                              * row["emergency_kwh"])
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
            maximum["negative"] = max(maximum["negative"], max(0.0, -min(
                row[name] for name in [
                    "plan_kwh", "load_kwh", "pv_kwh", "charge_kwh", "discharge_kwh",
                    "pv_spill_kwh", "paid_grid_spill_kwh", "emergency_kwh",
                ])))
            if row["charge_kwh"] > TOLERANCE and row["discharge_kwh"] > TOLERANCE:
                maximum["mutual_count"] += 1
            for field in SUM_FIELDS:
                totals[method][field] += row[field]

    maximum["row_count_error"] = abs(row_count - expected_rows)
    expected_groups = len(methods) * expected_days
    maximum["group_count_error"] = abs(len(groups) - expected_groups)
    inventory_totals = defaultdict(float)
    window_groups: dict[tuple[str, str], list[tuple[str, list[dict[str, float]]]]] = defaultdict(list)
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
        index = dates.index(label)
        alpha = rows[0]["selected_alpha"]
        if method == "mean7":
            expected_price = prices[max(0, index - 7):index].mean(axis=0)
            maximum["mean7_alpha_present"] = max(
                maximum["mean7_alpha_present"], 0.0 if alpha is None else 1.0)
        else:
            recent = prices[max(0, index - config["mean_history_days"]):index].mean(axis=0)
            expected_price = (1.0 - float(alpha)) * fixed + float(alpha) * recent
            if method in alpha_methods:
                expected_alpha = alphas[alpha_methods.index(method)]
                maximum["fixed_method_alpha"] = max(
                    maximum["fixed_method_alpha"], abs(float(alpha) - expected_alpha))
        recorded_price = np.asarray([row["price_forecast"] for row in rows])
        recorded_actual = np.asarray([row["price"] for row in rows])
        maximum["forecast_reconstruction"] = max(
            maximum["forecast_reconstruction"], float(np.max(np.abs(
                expected_price - recorded_price))))
        maximum["actual_price_alignment"] = max(
            maximum["actual_price_alignment"], float(np.max(np.abs(
                prices[index] - recorded_actual))))
        if key not in daily:
            maximum["missing_daily"] += 1
        else:
            source = daily[key]
            for field in SUM_FIELDS:
                maximum[f"daily_{field}"] = max(
                    maximum[f"daily_{field}"], abs(math.fsum(
                        row[field] for row in rows) - float(source[field])))
            maximum["daily_start"] = max(
                maximum["daily_start"], abs(rows[0]["e_start"] - float(source["e_start"])))
            maximum["daily_end"] = max(
                maximum["daily_end"], abs(rows[-1]["e_end"] - float(source["e_end"])))
        window_groups[(method, window)].append((label, rows))

    maximum["daily_group_count"] = abs(len(daily) - expected_groups)
    maximum["window_group_count"] = abs(
        len(window_groups) - len(methods) * len(config["windows"]))
    for key, day_groups in window_groups.items():
        day_groups.sort(key=lambda item: item[0])
        if key not in windows:
            maximum["missing_window"] += 1
            continue
        for (_, previous), (_, current) in zip(day_groups, day_groups[1:]):
            maximum["between_day_continuity"] = max(
                maximum["between_day_continuity"],
                abs(previous[-1]["e_end"] - current[0]["e_start"]))
        first_energy = day_groups[0][1][0]["e_start"]
        last_energy = day_groups[-1][1][-1]["e_end"]
        cash = math.fsum(row["total_cost"] for _, rows in day_groups for row in rows)
        mean_actual = float(np.mean([row["price"] for _, rows in day_groups for row in rows]))
        adjusted = cash + config["discharge_efficiency"] * mean_actual * (
            first_energy - last_energy)
        source = windows[key]
        maximum["window_inventory"] = max(
            maximum["window_inventory"], abs(adjusted - float(
                source["inventory_adjusted_cost"])))
        inventory_totals[key[0]] += adjusted

    for method in methods:
        if method not in summaries:
            maximum["missing_summary"] += 1
            continue
        for field in SUM_FIELDS:
            maximum[f"summary_{field}"] = max(
                maximum[f"summary_{field}"], abs(
                    totals[method][field] - float(summaries[method][field])))
        maximum["summary_inventory"] = max(
            maximum["summary_inventory"], abs(inventory_totals[method] - float(
                summaries[method]["inventory_adjusted_cost"])))

    shadow = {(row["date"], float(row["alpha"])): row for row in shadow_rows}
    score_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in selector_rows:
        score_groups[(row["target_date"], row["score_type"])].append(row)
    selector_pass = len(score_groups) == expected_days * 2
    chosen_alpha: dict[tuple[str, str], float] = {}
    for (target, score_type), rows in score_groups.items():
        rows.sort(key=lambda row: float(row["alpha"]))
        target_index = dates.index(target)
        first = target_index - config["online_score_days"]
        maximum["score_history_start"] = max(
            maximum["score_history_start"],
            0.0 if rows[0]["history_start"] == dates[first] else 1.0)
        maximum["score_history_end"] = max(
            maximum["score_history_end"],
            0.0 if rows[0]["history_end"] == dates[target_index - 1] else 1.0)
        recomputed = {}
        for row in rows:
            alpha = float(row["alpha"])
            if score_type == "cost":
                recomputed[alpha] = math.fsum(float(shadow[(dates[index], alpha)][
                    "inventory_adjusted_cost"]) for index in range(first, target_index))
            else:
                values = []
                for index in range(first, target_index):
                    recent = prices[max(0, index - config["mean_history_days"]):index].mean(axis=0)
                    forecast = (1.0 - alpha) * fixed + alpha * recent
                    values.append(float(np.mean(np.abs(forecast - prices[index]))))
                recomputed[alpha] = math.fsum(values)
            maximum[f"selector_{score_type}_score"] = max(
                maximum[f"selector_{score_type}_score"],
                abs(recomputed[alpha] - float(row["score"])))
        expected_alpha = min(alphas, key=lambda value: (recomputed[value], value))
        marked = [float(row["alpha"]) for row in rows if int(row["chosen"]) == 1]
        selector_pass = selector_pass and marked == [expected_alpha]
        chosen_alpha[(target, score_type)] = expected_alpha

    for (method, _, label), source in daily.items():
        if method == "rolling_cost_selector":
            maximum["rolling_cost_choice"] = max(
                maximum["rolling_cost_choice"], abs(float(source["selected_alpha"])
                - chosen_alpha[(label, "cost")]))
        elif method == "rolling_mae_selector":
            maximum["rolling_mae_choice"] = max(
                maximum["rolling_mae_choice"], abs(float(source["selected_alpha"])
                - chosen_alpha[(label, "mae")]))

    information_pass = (
        len(information) == len(causal_methods) * expected_days
        and all(bool(row["pass"]) for row in information)
        and all(row["method"] in causal_methods
                and row["history_end"] < row["date"]
                and abs(float(row["max_forecast_change_after_future_perturbation"])) <= 1e-12
                for row in information)
    )
    plan_audit_max = max(float(row["max_plan_audit"]) for row in daily_rows)
    plan_mutual_count = sum(int(row["plan_mutual_count"]) for row in daily_rows)
    solver_failures = sum(row["solver"] not in {"LP", "MILP"} for row in daily_rows)

    dev_costs = {
        alpha: float(periods[(alpha_name(alpha), "development")][
            "inventory_adjusted_cost"]) for alpha in alphas
    }
    static_alpha = min(alphas, key=lambda value: (dev_costs[value], value))
    candidate_costs = {
        "static_blend": dev_costs[static_alpha],
        "rolling_cost_selector": float(periods[(
            "rolling_cost_selector", "development")]["inventory_adjusted_cost"]),
    }
    raw_best = min(config["candidate_order"], key=lambda name: candidate_costs[name])
    best_cost = candidate_costs[raw_best]
    eligible = [name for name in config["candidate_order"]
                if candidate_costs[name] <= best_cost * (1 + config["selection_tolerance_fraction"])]
    selected_concept = min(eligible, key=config["candidate_order"].index)
    selected_method = alpha_name(static_alpha) if selected_concept == "static_blend" else selected_concept
    expected_later = {}
    for period in ["validation", "evaluation"]:
        selected_cost = float(periods[(selected_method, period)]["inventory_adjusted_cost"])
        mean7_cost = float(periods[("mean7", period)]["inventory_adjusted_cost"])
        fixed_cost = float(periods[(alpha_name(0.0), period)]["inventory_adjusted_cost"])
        mae_cost = float(periods[("rolling_mae_selector", period)]["inventory_adjusted_cost"])
        threshold = config["practical_improvement_fraction"]
        expected_later[period] = {
            "selected_cost": selected_cost,
            "mean7_cost": mean7_cost,
            "fixed_cost": fixed_cost,
            "rolling_mae_selector_cost": mae_cost,
            "beats_mean7_threshold": selected_cost <= mean7_cost * (1 - threshold),
            "beats_fixed_threshold": selected_cost <= fixed_cost * (1 - threshold),
            "beats_mae_selector": selected_cost < mae_cost,
        }
    scientific_pass = all(item["beats_mean7_threshold"]
                          and item["beats_fixed_threshold"]
                          and item["beats_mae_selector"]
                          for item in expected_later.values())
    selection_pass = (
        abs(float(selection["static_alpha"]) - static_alpha) <= 1e-12
        and selection["raw_best"] == raw_best
        and selection["eligible_within_tolerance"] == eligible
        and selection["selected_concept"] == selected_concept
        and selection["selected_method"] == selected_method
        and selection["later_checks"] == expected_later
        and bool(selection["scientific_target_pass"]) == scientific_pass
    )

    numerical = {key: value for key, value in maximum.items() if key != "mutual_count"}
    passed = (
        all(value <= TOLERANCE for value in numerical.values())
        and maximum["mutual_count"] == 0
        and selector_pass and information_pass and selection_pass
        and plan_audit_max <= TOLERANCE and plan_mutual_count == 0
        and solver_failures == 0
    )
    result = {
        "pass": passed,
        "expected_dispatch_rows": expected_rows,
        "actual_dispatch_rows": row_count,
        "expected_daily_groups": expected_groups,
        "actual_daily_groups": len(groups),
        "max_errors": dict(sorted(maximum.items())),
        "selector_reconstruction_pass": selector_pass,
        "information_boundary_pass": information_pass,
        "information_checks": len(information),
        "plan_audit_max": plan_audit_max,
        "plan_mutual_count": plan_mutual_count,
        "solver_failures": solver_failures,
        "selection_logic_pass": selection_pass,
        "note": "Independent audit reconstructs price blends, selectors, physics, costs, summaries and information boundaries.",
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
    args = parser.parse_args()
    audit(args.experiment.resolve())


if __name__ == "__main__":
    main()
