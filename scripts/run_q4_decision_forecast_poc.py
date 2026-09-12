"""Run the approved decision-focused Q4 price-forecast PoC."""
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
from run_q4_price_poc import daily_price_metrics, read_price_matrix, supply_forecast


SUMS = [
    "plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
    "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh",
]


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
        writer.writeheader()
        writer.writerows(rows)


def alpha_name(alpha: float) -> str:
    return f"blend_a{int(round(alpha * 100)):03d}"


def aggregate_windows(rows: list[dict]) -> dict:
    return {
        "windows": len(rows),
        "days": int(sum(row["days"] for row in rows)),
        **{key: float(math.fsum(row[key] for row in rows)) for key in SUMS},
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
    results = output / "results"
    figures = output / "figures"
    results.mkdir(); figures.mkdir()

    config = json.loads((ROOT / "configs/q4_decision_forecast_poc.json").read_text(
        encoding="utf-8"))
    q2_path = ROOT / "experiments/q2-full-20260911-01/inputs.json"
    q2 = json.loads(q2_path.read_text(encoding="utf-8"))
    dates = q2["dates"]
    load = np.asarray(q2["load"], dtype=float)
    pv = np.asarray(q2["pv"], dtype=float)
    fixed = np.asarray(q2["prices"], dtype=float)
    price_dates, actual_price, headers, price_audit = read_price_matrix(price_input)
    if dates != price_dates or actual_price.shape != (365, 144):
        raise ValueError("Attachment 4 and Q2 dates do not align")
    shutil.copyfile(price_input, output / "input_prices.json")

    shared_states: dict[str, float] = {}
    with (ROOT / "experiments/q2-improve-20260911-01/daily.csv").open(
            encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["strategy"] == "combined":
                shared_states[row["date"]] = float(row["e_start"])
    required_history_start = min(
        dates.index(window["start"]) for window in config["windows"]
    ) - config["online_score_days"]
    missing = [dates[i] for i in range(required_history_start, len(dates))
               if dates[i] >= "2025-02-01" and dates[i] not in shared_states]
    if missing:
        raise ValueError(f"Missing Q2 shared states: {missing[:3]}")

    dump(output / "run.json", {
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now().astimezone().isoformat(),
        "config": config,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "q2_snapshot_sha256": hashlib.sha256(q2_path.read_bytes()).hexdigest(),
        "attachment4": price_audit,
        "experiment_type": "predeclared decision-focused price PoC; 84 disjoint days",
    })
    dump(output / "input_audit.json", {
        "dates_aligned": True,
        "days": len(dates),
        "slots": len(headers),
        "shared_window_initial_states": {
            window["name"]: shared_states[window["start"]]
            for window in config["windows"]
        },
        "score_history_first_date": dates[required_history_start],
    })

    supply_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    margin_cache: dict[int, np.ndarray] = {}
    mean_cache: dict[tuple[int, int], np.ndarray] = {}

    def supply(index: int) -> tuple[np.ndarray, np.ndarray]:
        if index not in supply_cache:
            supply_cache[index] = supply_forecast(load, pv, dates, index)
        return supply_cache[index]

    def forecasts(index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        load_f, pv_f = supply(index)
        if index not in margin_cache:
            residuals = []
            for old in range(max(1, index - config["residual_days"]), index):
                old_lf, old_vf = supply(old)
                residuals.append(load[old] - pv[old] - old_lf + old_vf)
            margin_cache[index] = margin(residuals, config)
        return load_f, pv_f, margin_cache[index]

    def mean_price(index: int, days_count: int) -> np.ndarray:
        key = (index, days_count)
        if key not in mean_cache:
            mean_cache[key] = actual_price[max(0, index - days_count):index].mean(axis=0)
        return mean_cache[key]

    def alpha_price(index: int, alpha: float) -> np.ndarray:
        recent = mean_price(index, config["mean_history_days"])
        return (1.0 - alpha) * fixed + alpha * recent

    def method_price(index: int, method: str, selected_alpha: float | None) -> np.ndarray:
        if method == "mean7":
            return mean_price(index, 7)
        if selected_alpha is None:
            raise ValueError(f"Method {method} requires alpha")
        return alpha_price(index, selected_alpha)

    def simulate(index: int, price_curve: np.ndarray, initial: float) -> dict:
        load_f, pv_f, extra = forecasts(index)
        flow, solver = plan(price_curve, load_f + extra, pv_f, initial, config)
        rows, ending = execute(
            actual_price[index], flow["g"], load[index], pv[index], initial, config)
        totals = {key: float(math.fsum(row[key] for row in rows)) for key in SUMS}
        adjusted = totals["total_cost"] + config["discharge_efficiency"] * float(
            np.mean(actual_price[index])) * (initial - ending)
        return {
            "flow": flow,
            "solver": solver,
            "rows": rows,
            "ending": float(ending),
            "totals": totals,
            "adjusted_day_cost": float(adjusted),
        }

    alphas = [float(value) for value in config["blend_alphas"]]
    alpha_methods = [alpha_name(value) for value in alphas]
    shadow_cache: dict[tuple[int, float], dict] = {}

    def shadow(index: int, alpha: float) -> dict:
        key = (index, alpha)
        if key not in shadow_cache:
            initial = shared_states[dates[index]]
            item = simulate(index, alpha_price(index, alpha), initial)
            shadow_cache[key] = {
                "date": dates[index],
                "alpha": alpha,
                "e_start": initial,
                "e_end": item["ending"],
                **item["totals"],
                "inventory_adjusted_cost": item["adjusted_day_cost"],
                "solver": item["solver"]["formulation"],
                "max_plan_audit": float(max(
                    value for key_name, value in item["solver"]["audit"].items()
                    if key_name not in {"pass", "mutual_count"})),
                "plan_mutual_count": int(item["solver"]["audit"]["mutual_count"]),
            }
        return shadow_cache[key]

    selector_cache: dict[int, dict] = {}
    selector_rows: list[dict] = []

    def selector(index: int) -> dict:
        if index in selector_cache:
            return selector_cache[index]
        first = index - config["online_score_days"]
        if first < required_history_start:
            raise ValueError("Insufficient predeclared score history")
        history = range(first, index)
        cost_scores = {}
        mae_scores = {}
        for alpha in alphas:
            cost_scores[alpha] = float(math.fsum(
                shadow(old, alpha)["inventory_adjusted_cost"] for old in history))
            mae_scores[alpha] = float(math.fsum(
                daily_price_metrics(alpha_price(old, alpha), actual_price[old])["mae"]
                for old in history))
        cost_alpha = min(alphas, key=lambda value: (cost_scores[value], value))
        mae_alpha = min(alphas, key=lambda value: (mae_scores[value], value))
        for score_type, scores, chosen in [
            ("cost", cost_scores, cost_alpha), ("mae", mae_scores, mae_alpha),
        ]:
            for alpha in alphas:
                selector_rows.append({
                    "target_date": dates[index],
                    "history_start": dates[first],
                    "history_end": dates[index - 1],
                    "score_type": score_type,
                    "alpha": alpha,
                    "score": scores[alpha],
                    "chosen": int(alpha == chosen),
                })
        selector_cache[index] = {
            "cost_alpha": cost_alpha,
            "mae_alpha": mae_alpha,
            "cost_scores": cost_scores,
            "mae_scores": mae_scores,
        }
        return selector_cache[index]

    methods = alpha_methods + [
        "mean7", "rolling_cost_selector", "rolling_mae_selector", "oracle_alpha_daily",
    ]
    daily_rows: list[dict] = []
    window_rows: list[dict] = []
    price_rows: list[dict] = []
    plan_rows: list[dict] = []
    ledger = gzip.open(output / "dispatch.csv.gz", "wt", encoding="utf-8", newline="")
    dispatch_writer = None

    for method in methods:
        for window in config["windows"]:
            start = dates.index(window["start"])
            finish = dates.index(window["end"])
            energy = shared_states[window["start"]]
            window_daily = []
            prices_in_window = []
            for index in range(start, finish + 1):
                selected_alpha: float | None
                score_type = "none"
                if method in alpha_methods:
                    selected_alpha = alphas[alpha_methods.index(method)]
                elif method == "mean7":
                    selected_alpha = None
                elif method == "rolling_cost_selector":
                    selected_alpha = selector(index)["cost_alpha"]
                    score_type = "cost"
                elif method == "rolling_mae_selector":
                    selected_alpha = selector(index)["mae_alpha"]
                    score_type = "mae"
                elif method == "oracle_alpha_daily":
                    candidates = []
                    for alpha in alphas:
                        item = simulate(index, alpha_price(index, alpha), energy)
                        candidates.append((item["adjusted_day_cost"], alpha, item))
                    _, selected_alpha, simulation = min(
                        candidates, key=lambda value: (value[0], value[1]))
                    score_type = "oracle"
                else:
                    raise KeyError(method)

                price_curve = method_price(index, method, selected_alpha)
                if method != "oracle_alpha_daily":
                    simulation = simulate(index, price_curve, energy)
                metrics = daily_price_metrics(price_curve, actual_price[index])
                price_rows.append({
                    "method": method,
                    "window": window["name"],
                    "period": window["period"],
                    "date": dates[index],
                    "selected_alpha": selected_alpha,
                    **metrics,
                })
                solver = simulation["solver"]
                record = {
                    "method": method,
                    "window": window["name"],
                    "period": window["period"],
                    "date": dates[index],
                    "selected_alpha": selected_alpha,
                    "selector_score_type": score_type,
                    "e_start": float(energy),
                    "e_end": simulation["ending"],
                    **simulation["totals"],
                    "inventory_adjusted_day_cost": simulation["adjusted_day_cost"],
                    "emergency_slots": int(sum(
                        row["emergency_kwh"] > 1e-6 for row in simulation["rows"])),
                    "solver": solver["formulation"],
                    "solve_seconds": float(solver["seconds"]),
                    "max_plan_audit": float(max(
                        value for key_name, value in solver["audit"].items()
                        if key_name not in {"pass", "mutual_count"})),
                    "plan_mutual_count": int(solver["audit"]["mutual_count"]),
                }
                daily_rows.append(record); window_daily.append(record)
                prices_in_window.extend(actual_price[index].tolist())
                plan_rows.append({
                    "method": method,
                    "window": window["name"],
                    "period": window["period"],
                    "date": dates[index],
                    "history_end": None if method == "oracle_alpha_daily" else dates[index - 1],
                    "causal": method != "oracle_alpha_daily",
                    "e_start": float(energy),
                    "selected_alpha": selected_alpha,
                    "selector_score_type": score_type,
                    "forecast_price": price_curve.tolist(),
                    "ordinary_plan": simulation["flow"]["g"].tolist(),
                    "solver": solver,
                })
                for row in simulation["rows"]:
                    out = dict(row)
                    out.update({
                        "method": method,
                        "window": window["name"],
                        "period": window["period"],
                        "date": dates[index],
                        "selected_alpha": selected_alpha,
                        "price_forecast": float(price_curve[int(row["slot"])]),
                    })
                    if dispatch_writer is None:
                        dispatch_writer = csv.DictWriter(ledger, fieldnames=list(out))
                        dispatch_writer.writeheader()
                    dispatch_writer.writerow(out)
                energy = simulation["ending"]
            totals = {key: float(math.fsum(row[key] for row in window_daily)) for key in SUMS}
            mean_actual = float(np.mean(prices_in_window))
            adjusted = totals["total_cost"] + config["discharge_efficiency"] * mean_actual * (
                window_daily[0]["e_start"] - window_daily[-1]["e_end"])
            window_rows.append({
                "method": method,
                "window": window["name"],
                "period": window["period"],
                "days": len(window_daily),
                **totals,
                "e_start": window_daily[0]["e_start"],
                "e_end": window_daily[-1]["e_end"],
                "mean_actual_price": mean_actual,
                "inventory_adjusted_cost": float(adjusted),
                "emergency_slots": int(sum(row["emergency_slots"] for row in window_daily)),
                "milp_days": int(sum(row["solver"] == "MILP" for row in window_daily)),
                "max_plan_audit": float(max(row["max_plan_audit"] for row in window_daily)),
            })
            print(method, window["name"], round(adjusted, 2), flush=True)
    ledger.close()

    table(results / "daily_summary.csv", daily_rows)
    table(results / "window_summary.csv", window_rows)
    table(results / "price_metrics_daily.csv", price_rows)
    table(results / "selector_scores.csv", selector_rows)
    table(results / "shadow_daily.csv", [shadow_cache[key] for key in sorted(shadow_cache)])
    with gzip.open(output / "plans.jsonl.gz", "wt", encoding="utf-8") as stream:
        for record in plan_rows:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                                    default=lambda x: x.item() if hasattr(x, "item") else x) + "\n")

    summaries = []
    periods = []
    price_metrics = []
    for method in methods:
        method_windows = [row for row in window_rows if row["method"] == method]
        summaries.append({"method": method, **aggregate_windows(method_windows)})
        for period in ["development", "validation", "evaluation"]:
            selected_rows = [row for row in method_windows if row["period"] == period]
            periods.append({"method": method, "period": period,
                            **aggregate_windows(selected_rows)})
        method_prices = [row for row in price_rows if row["method"] == method]
        price_metrics.append({
            "method": method,
            "days": len(method_prices),
            "mae": float(np.mean([row["mae"] for row in method_prices])),
            "rmse": float(math.sqrt(np.mean([row["mse"] for row in method_prices]))),
            "rank_correlation": float(np.mean([
                row["rank_correlation"] for row in method_prices])),
            "high_quartile_overlap": float(np.mean([
                row["high_quartile_overlap"] for row in method_prices])),
            "low_quartile_overlap": float(np.mean([
                row["low_quartile_overlap"] for row in method_prices])),
        })
    table(results / "summary_tables.csv", summaries)
    table(results / "periods.csv", periods)
    table(results / "price_forecast_metrics.csv", price_metrics)

    period_map = {(row["method"], row["period"]): row for row in periods}
    dev_alpha_costs = {
        alpha: period_map[(alpha_name(alpha), "development")]["inventory_adjusted_cost"]
        for alpha in alphas
    }
    static_alpha = min(alphas, key=lambda value: (dev_alpha_costs[value], value))
    static_method = alpha_name(static_alpha)
    candidate_costs = {
        "static_blend": dev_alpha_costs[static_alpha],
        "rolling_cost_selector": period_map[
            ("rolling_cost_selector", "development")]["inventory_adjusted_cost"],
    }
    raw_best = min(config["candidate_order"], key=lambda name: candidate_costs[name])
    best_cost = candidate_costs[raw_best]
    eligible = [name for name in config["candidate_order"]
                if candidate_costs[name] <= best_cost * (1.0 + config["selection_tolerance_fraction"])]
    selected_concept = min(eligible, key=config["candidate_order"].index)
    selected_method = static_method if selected_concept == "static_blend" else selected_concept
    later_checks = {}
    for period in ["validation", "evaluation"]:
        selected_cost = period_map[(selected_method, period)]["inventory_adjusted_cost"]
        mean7_cost = period_map[("mean7", period)]["inventory_adjusted_cost"]
        fixed_cost = period_map[(alpha_name(0.0), period)]["inventory_adjusted_cost"]
        mae_selector_cost = period_map[
            ("rolling_mae_selector", period)]["inventory_adjusted_cost"]
        threshold = config["practical_improvement_fraction"]
        later_checks[period] = {
            "selected_cost": selected_cost,
            "mean7_cost": mean7_cost,
            "fixed_cost": fixed_cost,
            "rolling_mae_selector_cost": mae_selector_cost,
            "beats_mean7_threshold": selected_cost <= mean7_cost * (1.0 - threshold),
            "beats_fixed_threshold": selected_cost <= fixed_cost * (1.0 - threshold),
            "beats_mae_selector": selected_cost < mae_selector_cost,
        }
    scientific_pass = all(
        item["beats_mean7_threshold"] and item["beats_fixed_threshold"]
        and item["beats_mae_selector"] for item in later_checks.values())
    dump(output / "selection.json", {
        "selection_period": "development only",
        "static_alpha_development_costs": dev_alpha_costs,
        "static_alpha": static_alpha,
        "static_method": static_method,
        "candidate_development_costs": candidate_costs,
        "raw_best": raw_best,
        "eligible_within_tolerance": eligible,
        "selected_concept": selected_concept,
        "selected_method": selected_method,
        "later_checks": later_checks,
        "scientific_target_pass": scientific_pass,
        "oracle_excluded": True,
        "fallback_if_fail": "D-015 lag1 operational baseline, then fixed_attachment1",
    })

    information_rows = []
    executable_methods = alpha_methods + [
        "mean7", "rolling_cost_selector", "rolling_mae_selector",
    ]
    for window in config["windows"]:
        for index in range(dates.index(window["start"]), dates.index(window["end"]) + 1):
            chosen = selector(index)
            for method in executable_methods:
                if method in alpha_methods:
                    alpha = alphas[alpha_methods.index(method)]
                elif method == "mean7":
                    alpha = None
                elif method == "rolling_cost_selector":
                    alpha = chosen["cost_alpha"]
                else:
                    alpha = chosen["mae_alpha"]
                original = method_price(index, method, alpha)
                changed_prices = actual_price.copy()
                changed_prices[index:] = changed_prices[index:] * 1.37 + 0.123
                if method == "mean7":
                    altered = changed_prices[max(0, index - 7):index].mean(axis=0)
                else:
                    recent = changed_prices[max(0, index - config["mean_history_days"]):index].mean(axis=0)
                    altered = (1.0 - float(alpha)) * fixed + float(alpha) * recent
                delta = float(np.max(np.abs(original - altered)))
                passed = delta <= 1e-12
                if not passed:
                    raise AssertionError((method, dates[index], delta))
                information_rows.append({
                    "window": window["name"],
                    "date": dates[index],
                    "method": method,
                    "history_end": dates[index - 1],
                    "max_forecast_change_after_future_perturbation": delta,
                    "pass": passed,
                })
    dump(output / "information_checks.json", information_rows)
    dump(output / "completion.json", {
        "methods": len(methods),
        "days_per_method": sum(
            (date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])).days + 1
            for window in config["windows"]),
        "dispatch_rows": len(methods) * len(config["windows"]) * 14 * 144,
        "selector_target_days": len(selector_cache),
        "shadow_day_alpha_runs": len(shadow_cache),
        "information_checks": len(information_rows),
        "selected_method": selected_method,
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
