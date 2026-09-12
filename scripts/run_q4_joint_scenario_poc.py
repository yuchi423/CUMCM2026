"""Run the approved matched price-supply residual scenario PoC for Q4."""
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
from datetime import date, datetime
from pathlib import Path

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from q2_core import execute, margin, plan
from run_q4_price_poc import read_price_matrix, supply_forecast


SUMS = [
    "plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
    "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh",
]
METHODS = [
    "fixed", "mean7", "mean14", "rolling_cost_selector",
    "load_scenario_selector", "joint_scenario_selector",
    "shuffled_scenario_selector", "oracle_plan_daily",
]
PLAN_CANDIDATES = ["fixed", "blend25", "blend50", "blend75", "mean14", "mean7"]


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(
        value, ensure_ascii=False, indent=2, allow_nan=False,
        default=lambda x: x.item() if hasattr(x, "item") else x,
    ) + "\n", encoding="utf-8")


def table(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def aggregate_windows(rows: list[dict]) -> dict:
    return {
        "windows": len(rows), "days": int(sum(row["days"] for row in rows)),
        **{field: float(math.fsum(row[field] for row in rows)) for field in SUMS},
        "inventory_adjusted_cost": float(math.fsum(
            row["inventory_adjusted_cost"] for row in rows)),
        "total_window_start_energy": float(math.fsum(row["e_start"] for row in rows)),
        "total_window_end_energy": float(math.fsum(row["e_end"] for row in rows)),
        "emergency_slots": int(sum(row["emergency_slots"] for row in rows)),
        "milp_days": int(sum(row["milp_days"] for row in rows)),
        "max_plan_audit": float(max(row["max_plan_audit"] for row in rows)),
    }


def run(output: Path, price_input: Path) -> None:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    results = output / "results"; figures = output / "figures"
    results.mkdir(); figures.mkdir()
    config = json.loads((ROOT / "configs/q4_joint_scenario_poc.json").read_text(
        encoding="utf-8"))
    q2_path = ROOT / "experiments/q2-full-20260911-01/inputs.json"
    q2 = json.loads(q2_path.read_text(encoding="utf-8"))
    dates = q2["dates"]
    load = np.asarray(q2["load"], dtype=float)
    pv = np.asarray(q2["pv"], dtype=float)
    fixed_price = np.asarray(q2["prices"], dtype=float)
    price_dates, actual_price, headers, price_audit = read_price_matrix(price_input)
    if dates != price_dates:
        raise ValueError("Attachment 4 and Q2 dates do not align")
    shutil.copyfile(price_input, output / "input_prices.json")

    shared_states = {}
    with (ROOT / "experiments/q2-improve-20260911-01/daily.csv").open(
            encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["strategy"] == "combined":
                shared_states[row["date"]] = float(row["e_start"])
    first_target = min(dates.index(window["start"]) for window in config["windows"])
    first_history = first_target - config["scenario_history_days"]
    if dates[first_history] not in shared_states:
        raise ValueError("Missing shared state for scenario history")

    dump(output / "run.json", {
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now().astimezone().isoformat(), "config": config,
        "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
        "q2_snapshot_sha256": hashlib.sha256(q2_path.read_bytes()).hexdigest(),
        "attachment4": price_audit,
        "experiment_type": "predeclared 28-day matched residual scenario plan-selection PoC",
    })
    dump(output / "input_audit.json", {
        "dates_aligned": True, "days": len(dates), "slots": len(headers),
        "scenario_history_first_date": dates[first_history],
        "shared_window_initial_states": {
            window["name"]: shared_states[window["start"]]
            for window in config["windows"]},
    })

    supply_cache = {}; margin_cache = {}; price_mean_cache = {}; residual_cache = {}

    def supply(index: int) -> tuple[np.ndarray, np.ndarray]:
        if index not in supply_cache:
            supply_cache[index] = supply_forecast(load, pv, dates, index)
        return supply_cache[index]

    def forecasts(index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        load_f, pv_f = supply(index)
        if index not in margin_cache:
            residuals = []
            for old in range(max(1, index - config["residual_days"]), index):
                old_lf, old_pv = supply(old)
                residuals.append(load[old] - pv[old] - old_lf + old_pv)
            margin_cache[index] = margin(residuals, config)
        return load_f, pv_f, margin_cache[index]

    def mean_price(index: int, days_count: int) -> np.ndarray:
        key = (index, days_count)
        if key not in price_mean_cache:
            price_mean_cache[key] = actual_price[max(0, index - days_count):index].mean(axis=0)
        return price_mean_cache[key]

    def candidate_price(index: int, candidate: str) -> np.ndarray:
        recent14 = mean_price(index, config["mean_history_days"])
        if candidate == "fixed":
            return fixed_price.copy()
        if candidate == "mean7":
            return mean_price(index, 7)
        if candidate == "mean14":
            return recent14
        alpha = {"blend25": 0.25, "blend50": 0.5, "blend75": 0.75}[candidate]
        return (1.0 - alpha) * fixed_price + alpha * recent14

    def residual(index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if index not in residual_cache:
            load_f, pv_f = supply(index)
            residual_cache[index] = (
                load[index] - load_f,
                pv[index] - pv_f,
                actual_price[index] - mean_price(index, config["mean_history_days"]),
            )
        return residual_cache[index]

    def make_plan(index: int, energy: float, candidate: str) -> dict:
        load_f, pv_f, extra = forecasts(index)
        curve = candidate_price(index, candidate)
        flow, solver = plan(curve, load_f + extra, pv_f, energy, config)
        return {"price": curve, "flow": flow, "solver": solver}

    def make_plans(index: int, energy: float) -> dict[str, dict]:
        return {candidate: make_plan(index, energy, candidate)
                for candidate in PLAN_CANDIDATES}

    def replay(ordinary: np.ndarray, scenario_price: np.ndarray, scenario_load: np.ndarray,
               scenario_pv: np.ndarray, energy: float) -> dict:
        rows, ending = execute(
            scenario_price, ordinary, scenario_load, scenario_pv, energy, config)
        totals = {field: float(math.fsum(row[field] for row in rows)) for field in SUMS}
        adjusted = totals["total_cost"] + config["discharge_efficiency"] * float(
            np.mean(scenario_price)) * (energy - ending)
        return {"rows": rows, "ending": float(ending), "totals": totals,
                "inventory_adjusted_cost": float(adjusted)}

    shadow_cache = {}
    historical_plans_cache = {}

    def historical_policy_cost(index: int, candidate: str) -> float:
        key = (index, candidate)
        if key not in shadow_cache:
            energy = shared_states[dates[index]]
            if index not in historical_plans_cache:
                historical_plans_cache[index] = make_plans(index, energy)
            plan_item = historical_plans_cache[index][candidate]
            result = replay(plan_item["flow"]["g"], actual_price[index], load[index], pv[index], energy)
            shadow_cache[key] = result["inventory_adjusted_cost"]
        return shadow_cache[key]

    selector_scores = []
    candidate_plan_records = []

    def select_plan(index: int, energy: float, selector_name: str) -> tuple[str, dict]:
        history = list(range(index - config["scenario_history_days"], index))
        plans = make_plans(index, energy)
        scores = {}
        for candidate in PLAN_CANDIDATES:
            if selector_name == "rolling_cost_selector":
                values = [historical_policy_cost(old, candidate) for old in history]
            elif selector_name == "oracle_plan_daily":
                values = [replay(plans[candidate]["flow"]["g"], actual_price[index],
                                 load[index], pv[index], energy)["inventory_adjusted_cost"]]
            else:
                values = []
                base_load, base_pv = supply(index)
                for position, old in enumerate(history):
                    load_error, pv_error, price_error = residual(old)
                    scenario_load = np.maximum(0.0, base_load + load_error)
                    scenario_pv = np.maximum(0.0, base_pv + pv_error)
                    if selector_name == "load_scenario_selector":
                        scenario_price = mean_price(index, config["mean_history_days"])
                    elif selector_name == "joint_scenario_selector":
                        scenario_price = np.maximum(
                            config["price_floor"],
                            mean_price(index, config["mean_history_days"]) + price_error)
                    elif selector_name == "shuffled_scenario_selector":
                        shifted = history[(position + config["independent_price_shift_days"])
                                          % len(history)]
                        shifted_price_error = residual(shifted)[2]
                        scenario_price = np.maximum(
                            config["price_floor"],
                            mean_price(index, config["mean_history_days"]) + shifted_price_error)
                    else:
                        raise KeyError(selector_name)
                    values.append(replay(
                        plans[candidate]["flow"]["g"], scenario_price,
                        scenario_load, scenario_pv, energy)["inventory_adjusted_cost"])
            scores[candidate] = float(np.mean(values))
        selected = min(PLAN_CANDIDATES, key=lambda name: (scores[name], PLAN_CANDIDATES.index(name)))
        for candidate in PLAN_CANDIDATES:
            item = plans[candidate]
            selector_scores.append({
                "selector": selector_name, "target_date": dates[index],
                "history_start": dates[history[0]], "history_end": dates[history[-1]],
                "candidate": candidate, "score": scores[candidate],
                "chosen": int(candidate == selected),
            })
            candidate_plan_records.append({
                "selector": selector_name, "target_date": dates[index],
                "history_start": dates[history[0]], "history_end": dates[history[-1]],
                "candidate": candidate, "e_start": energy,
                "forecast_price": item["price"].tolist(),
                "ordinary_plan": item["flow"]["g"].tolist(),
                "score": scores[candidate],
                "solver": item["solver"],
            })
        return selected, plans[selected]

    daily_rows = []; window_rows = []; actual_plan_records = []
    ledger = gzip.open(output / "dispatch.csv.gz", "wt", encoding="utf-8", newline="")
    writer = None
    for method in METHODS:
        for window in config["windows"]:
            start = dates.index(window["start"]); finish = dates.index(window["end"])
            energy = shared_states[window["start"]]
            window_daily = []; window_prices = []
            for index in range(start, finish + 1):
                if method in {"fixed", "mean7", "mean14"}:
                    plan_item = make_plan(index, energy, method)
                    chosen = method
                else:
                    chosen, plan_item = select_plan(index, energy, method)
                day_start = energy
                actual = replay(plan_item["flow"]["g"], actual_price[index],
                                load[index], pv[index], energy)
                solver = plan_item["solver"]
                record = {
                    "method": method, "window": window["name"], "period": window["period"],
                    "date": dates[index], "selected_plan": chosen,
                    "e_start": float(day_start), "e_end": actual["ending"],
                    **actual["totals"],
                    "inventory_adjusted_day_cost": actual["inventory_adjusted_cost"],
                    "emergency_slots": int(sum(
                        row["emergency_kwh"] > 1e-6 for row in actual["rows"])),
                    "solver": solver["formulation"], "solve_seconds": float(solver["seconds"]),
                    "max_plan_audit": float(max(value for key, value in solver["audit"].items()
                                                     if key not in {"pass", "mutual_count"})),
                    "plan_mutual_count": int(solver["audit"]["mutual_count"]),
                }
                daily_rows.append(record); window_daily.append(record)
                window_prices.extend(actual_price[index].tolist())
                actual_plan_records.append({
                    "method": method, "window": window["name"], "period": window["period"],
                    "date": dates[index], "history_end": None if method == "oracle_plan_daily"
                    else dates[index - 1], "causal": method != "oracle_plan_daily",
                    "selected_plan": chosen, "e_start": day_start,
                    "forecast_price": plan_item["price"].tolist(),
                    "ordinary_plan": plan_item["flow"]["g"].tolist(), "solver": solver,
                })
                for row in actual["rows"]:
                    out = dict(row)
                    out.update({
                        "method": method, "window": window["name"], "period": window["period"],
                        "date": dates[index], "selected_plan": chosen,
                        "price_forecast": float(plan_item["price"][int(row["slot"])]),
                    })
                    if writer is None:
                        writer = csv.DictWriter(ledger, fieldnames=list(out)); writer.writeheader()
                    writer.writerow(out)
                energy = actual["ending"]
            totals = {field: float(math.fsum(row[field] for row in window_daily))
                      for field in SUMS}
            mean_actual = float(np.mean(window_prices))
            adjusted = totals["total_cost"] + config["discharge_efficiency"] * mean_actual * (
                window_daily[0]["e_start"] - window_daily[-1]["e_end"])
            window_rows.append({
                "method": method, "window": window["name"], "period": window["period"],
                "days": len(window_daily), **totals,
                "e_start": window_daily[0]["e_start"], "e_end": window_daily[-1]["e_end"],
                "mean_actual_price": mean_actual, "inventory_adjusted_cost": float(adjusted),
                "emergency_slots": int(sum(row["emergency_slots"] for row in window_daily)),
                "milp_days": int(sum(row["solver"] == "MILP" for row in window_daily)),
                "max_plan_audit": float(max(row["max_plan_audit"] for row in window_daily)),
            })
            print(method, window["name"], round(adjusted, 2), flush=True)
    ledger.close()

    table(results / "daily_summary.csv", daily_rows)
    table(results / "window_summary.csv", window_rows)
    table(results / "selector_scores.csv", selector_scores)
    table(results / "shadow_policy_costs.csv", [
        {"date": dates[index], "candidate": candidate,
         "inventory_adjusted_cost": shadow_cache[(index, candidate)]}
        for index, candidate in sorted(shadow_cache)])
    with gzip.open(output / "plans.jsonl.gz", "wt", encoding="utf-8") as stream:
        for record in actual_plan_records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                                    default=lambda x: x.item() if hasattr(x, "item") else x) + "\n")
    with gzip.open(output / "candidate_plans.jsonl.gz", "wt", encoding="utf-8") as stream:
        for record in candidate_plan_records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                                    default=lambda x: x.item() if hasattr(x, "item") else x) + "\n")

    summaries = []; periods = []
    for method in METHODS:
        method_windows = [row for row in window_rows if row["method"] == method]
        summaries.append({"method": method, **aggregate_windows(method_windows)})
        for period in ["development", "validation", "evaluation"]:
            periods.append({"method": method, "period": period, **aggregate_windows(
                [row for row in method_windows if row["period"] == period])})
    table(results / "summary_tables.csv", summaries)
    table(results / "periods.csv", periods)
    period_map = {(row["method"], row["period"]): row for row in periods}
    dev_costs = {name: period_map[(name, "development")]["inventory_adjusted_cost"]
                 for name in config["candidate_order"]}
    raw_best = min(config["candidate_order"], key=lambda name: dev_costs[name])
    best_cost = dev_costs[raw_best]
    eligible = [name for name in config["candidate_order"] if dev_costs[name]
                <= best_cost * (1 + config["selection_tolerance_fraction"])]
    selected = min(eligible, key=config["candidate_order"].index)
    later = {}
    for period in ["validation", "evaluation"]:
        selected_cost = period_map[(selected, period)]["inventory_adjusted_cost"]
        mean7_cost = period_map[("mean7", period)]["inventory_adjusted_cost"]
        fixed_cost = period_map[("fixed", period)]["inventory_adjusted_cost"]
        shuffled_cost = period_map[("shuffled_scenario_selector", period)][
            "inventory_adjusted_cost"]
        threshold = config["practical_improvement_fraction"]
        later[period] = {
            "selected_cost": selected_cost, "mean7_cost": mean7_cost,
            "fixed_cost": fixed_cost, "shuffled_cost": shuffled_cost,
            "beats_mean7_threshold": selected_cost <= mean7_cost * (1 - threshold),
            "beats_fixed_threshold": selected_cost <= fixed_cost * (1 - threshold),
            "joint_beats_shuffled_if_selected": (
                selected != "joint_scenario_selector" or selected_cost < shuffled_cost),
        }
    scientific_pass = all(
        item["beats_mean7_threshold"] and item["beats_fixed_threshold"]
        and item["joint_beats_shuffled_if_selected"] for item in later.values())
    dump(output / "selection.json", {
        "selection_period": "development only", "candidate_development_costs": dev_costs,
        "raw_best": raw_best, "eligible_within_tolerance": eligible,
        "selected": selected, "later_checks": later,
        "scientific_target_pass": scientific_pass, "oracle_excluded": True,
        "shuffled_is_negative_control": True,
        "fallback_if_fail": "D-015 lag1 operational baseline, then fixed_attachment1",
    })
    perturbation_cache = {}
    information = []
    for row in actual_plan_records:
        if row["causal"]:
            index = dates.index(row["date"])
            if index not in perturbation_cache:
                changed = actual_price.copy()
                changed[index:] = changed[index:] * 1.37 + 0.123
                original_target_mean = actual_price[
                    index - config["mean_history_days"]:index].mean(axis=0)
                changed_target_mean = changed[
                    index - config["mean_history_days"]:index].mean(axis=0)
                deltas = [float(np.max(np.abs(
                    original_target_mean - changed_target_mean)))]
                for old in range(index - config["scenario_history_days"], index):
                    original_old_mean = actual_price[
                        old - config["mean_history_days"]:old].mean(axis=0)
                    changed_old_mean = changed[
                        old - config["mean_history_days"]:old].mean(axis=0)
                    original_error = actual_price[old] - original_old_mean
                    changed_error = changed[old] - changed_old_mean
                    deltas.append(float(np.max(np.abs(original_error - changed_error))))
                perturbation_cache[index] = max(deltas)
                if perturbation_cache[index] > 1e-12:
                    raise AssertionError((row["date"], perturbation_cache[index]))
            information.append({
                "method": row["method"], "date": row["date"],
                "history_end": row["history_end"],
                "max_causal_price_source_change_after_target_future_perturbation":
                    perturbation_cache[index],
                "target_and_future_price_perturbation_changes_choice": False,
                "pass": row["history_end"] < row["date"]
                    and perturbation_cache[index] <= 1e-12,
            })
    dump(output / "information_checks.json", information)
    dump(output / "completion.json", {
        "methods": len(METHODS), "days_per_method": sum(
            (date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])).days + 1
            for window in config["windows"]),
        "dispatch_rows": len(METHODS) * 84 * 144,
        "selector_candidate_scores": len(selector_scores),
        "scenario_replays": sum(1 if row["selector"] in {
            "rolling_cost_selector", "oracle_plan_daily"} else config["scenario_history_days"]
            for row in selector_scores),
        "information_checks": len(information), "selected": selected,
        "scientific_target_pass": scientific_pass,
        "runtime_seconds": time.perf_counter() - started,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--price-input", required=True, type=Path)
    args = parser.parse_args()
    target = args.output.resolve()
    if not target.is_relative_to((ROOT / "experiments").resolve()):
        raise ValueError("Output must be under experiments/")
    run(target, args.price_input.resolve())


if __name__ == "__main__":
    main()
