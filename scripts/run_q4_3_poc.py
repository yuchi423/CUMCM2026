"""Run the approved 84-day Question 4-3 model-judge Cheap PoC."""
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
from pathlib import Path

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_q2_inputs import read_sources
from q2_core import execute
from q3_core import choose_candidate, empirical_cvar, plan_with_audit
from q3_shared import fee_checks, hourly_margin, load_forecasts, read_q3_forecasts
from run_q4_price_poc import read_price_matrix

VARIANTS = [
    {"name": "no_update_mean14", "issues": [0], "pv_source": "latest", "method": "point"},
    {"name": "state_only_mean14", "issues": [0, 6, 12, 18], "pv_source": "zero", "method": "point"},
    {"name": "rolling_margin_mean14", "issues": [0, 6, 12, 18], "pv_source": "latest", "method": "margin"},
    {"name": "rolling_scenario_mean14", "issues": [0, 6, 12, 18], "pv_source": "latest", "method": "scenario"},
    {"name": "rolling_scenario_shuffled", "issues": [0, 6, 12, 18], "pv_source": "latest", "method": "scenario", "shuffle": True},
    {"name": "rolling_scenario_price_update", "issues": [0, 6, 12, 18], "pv_source": "latest", "method": "scenario", "price_update": True}
]
SUMS = ["ordinary_kwh", "emergency_kwh", "ordinary_cost", "adjustment_cost",
        "emergency_cost", "total_cost", "paid_grid_spill_kwh", "pv_spill_kwh"]


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False,
        default=lambda x: x.item() if hasattr(x, "item") else x) + "\n", encoding="utf-8")


def table(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decision_price(prices: np.ndarray, index: int, start: int, cfg: dict,
                   update: bool = False) -> tuple[np.ndarray, float]:
    history = prices[max(0, index - cfg["price_history_days"]):index]
    if len(history) != cfg["price_history_days"]:
        raise ValueError("Insufficient price history")
    base = history.mean(axis=0)
    bias = 0.0
    if update and start:
        bias = float(np.mean(prices[index, :start] - base[:start]))
    return np.maximum(cfg["price_floor"], base[start:] + bias), bias


def shuffled_paths(paths: list[np.ndarray], seed: int) -> tuple[list[np.ndarray], float, int]:
    values = np.asarray(paths, dtype=float)
    rng = np.random.default_rng(seed)
    out = values.copy()
    changed = 0
    for t in range(values.shape[1]):
        order = rng.permutation(values.shape[0])
        out[:, t] = values[order, t]
        changed += int(np.any(order != np.arange(values.shape[0])))
    marginal_error = float(np.max(np.abs(np.sort(values, axis=0) - np.sort(out, axis=0))))
    return [row.copy() for row in out], marginal_error, changed


def window_summary(strategy: str, window: dict, rows: list[dict], prices: np.ndarray, cfg: dict) -> dict:
    totals = {key: float(math.fsum(float(row[key]) for row in rows)) for key in SUMS}
    mean_price = float(np.mean(prices))
    adjusted = totals["total_cost"] + cfg["terminal_value_multiple"] * mean_price * (
        float(rows[0]["e_start"]) - float(rows[-1]["e_end"]))
    return {"strategy": strategy, "window": window["name"], "period": window["period"],
            "days": len(rows), **totals, "e_start": float(rows[0]["e_start"]),
            "e_end": float(rows[-1]["e_end"]), "mean_actual_price": mean_price,
            "inventory_adjusted_cost": float(adjusted),
            "daily_cvar90": empirical_cvar(
                [row["total_cost"] for row in rows], cfg["scenario_alpha"]),
            "updates": int(sum(row["updates"] for row in rows)),
            "changed_updates": int(sum(row["changed_updates"] for row in rows)),
            "emergency_slots": int(sum(row["emergency_slots"] for row in rows))}


def combined_summary(strategy: str, period: str, window_rows: list[dict], daily: list[dict], cfg: dict) -> dict:
    totals = {key: float(math.fsum(float(row[key]) for row in window_rows)) for key in SUMS}
    return {"strategy": strategy, "period": period,
            "windows": len(window_rows), "days": int(sum(row["days"] for row in window_rows)),
            **totals, "inventory_adjusted_cost": float(math.fsum(
                float(row["inventory_adjusted_cost"]) for row in window_rows)),
            "daily_cvar90": empirical_cvar(
                [row["total_cost"] for row in daily], cfg["scenario_alpha"]),
            "total_window_start_energy": float(math.fsum(float(row["e_start"]) for row in window_rows)),
            "total_window_end_energy": float(math.fsum(float(row["e_end"]) for row in window_rows)),
            "updates": int(sum(row["updates"] for row in window_rows)),
            "changed_updates": int(sum(row["changed_updates"] for row in window_rows)),
            "emergency_slots": int(sum(row["emergency_slots"] for row in window_rows))}


def gate(candidate: dict, baseline: dict, cfg: dict) -> dict:
    cost_improvement = (baseline["inventory_adjusted_cost"] - candidate["inventory_adjusted_cost"]) / baseline["inventory_adjusted_cost"]
    cvar_improvement = (baseline["daily_cvar90"] - candidate["daily_cvar90"]) / baseline["daily_cvar90"]
    cash_ratio = candidate["total_cost"] / baseline["total_cost"] - 1.0
    passed = (cost_improvement >= cfg["practical_improvement_fraction"] or
              (cvar_improvement >= cfg["cvar_improvement_fraction"] and
               cash_ratio <= cfg["cash_worsening_tolerance_fraction"]))
    return {"inventory_cost_improvement_fraction": float(cost_improvement),
            "cvar90_improvement_fraction": float(cvar_improvement),
            "cash_worsening_fraction": float(cash_ratio), "pass": bool(passed)}


def main(run_arg: str, price_input: Path) -> None:
    run_dir = (ROOT / run_arg).resolve()
    if not run_dir.is_relative_to((ROOT / "experiments").resolve()):
        raise ValueError("Run directory must be under experiments")
    run_dir.mkdir(parents=True, exist_ok=False)
    details, results = run_dir / "details", run_dir / "results"
    details.mkdir(); results.mkdir()
    cfg = json.loads((ROOT / "configs/q4_3_poc.json").read_text(encoding="utf-8"))
    source = read_sources(cfg)
    dates = source["dates"]
    load, actual_pv = np.asarray(source["load"], float), np.asarray(source["pv"], float)
    price_dates, actual_price, _, price_audit = read_price_matrix(price_input)
    if dates != price_dates:
        raise ValueError("Attachment 2 and Attachment 4 dates do not align")
    shutil.copyfile(price_input, run_dir / "input_prices.json")
    _, pv_curve, forecast_hash = read_q3_forecasts(cfg, actual_pv)
    load_pred = load_forecasts(load, dates)
    date_index = {d: i for i, d in enumerate(dates)}
    forecasts, residuals = {}, {}
    for i in range(1, len(dates)):
        for hour in cfg["issue_hours"]:
            start = hour * 6
            forecast = pv_curve(dates[i], hour, "linear")
            forecasts[(i, hour)] = forecast
            residuals[(i, hour)] = ((load[i, start:] - actual_pv[i, start:]) -
                                    (load_pred[i][start:] - forecast))

    shared_states = {}
    with (ROOT / "deliverables/q3/daily_summary.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            shared_states[row["date"]] = float(row["e_start"])
    if not all(window["start"] in shared_states for window in cfg["windows"]):
        raise ValueError("Missing shared PoC start states")

    checks = fee_checks()
    sample_i, sample_start = date_index["2025-06-21"], 36
    base, _ = decision_price(actual_price, sample_i, sample_start, cfg, True)
    future_changed = actual_price.copy(); future_changed[sample_i, sample_start:] += 7.0
    future, _ = decision_price(future_changed, sample_i, sample_start, cfg, True)
    prefix_changed = actual_price.copy(); prefix_changed[sample_i, :sample_start] += 0.2
    prefix, _ = decision_price(prefix_changed, sample_i, sample_start, cfg, True)
    checks.update({"future_price_suffix_perturbation_max_delta": float(np.max(np.abs(base - future))),
                   "observed_price_prefix_perturbation_min_delta": float(np.min(np.abs(base - prefix))),
                   "future_price_boundary_pass": bool(np.max(np.abs(base - future)) <= 1e-12 and
                                                       np.max(np.abs(base - prefix)) > 1e-6),
                   "target_mapping": "same-day remainder only"})
    if not checks["pass_"] or not checks["future_price_boundary_pass"]:
        raise AssertionError(checks)
    dump(run_dir / "preflight_checks.json", checks)

    started = time.perf_counter()
    dump(run_dir / "run.json", {"code_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": __import__("datetime").datetime.now().astimezone().isoformat(),
        "config": cfg, "variants": VARIANTS, "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__,
        "input_hashes": {**source["input_hashes"], "data/raw/附件3.xlsx": forecast_hash,
                         "data/raw/附件4.xlsx": price_audit["sha256"],
                         "price_snapshot": sha(price_input),
                         "deliverables/q3/daily_summary.csv": sha(ROOT / "deliverables/q3/daily_summary.csv")},
        "experiment_type": "approved predeclared 84-day Q4-3 model-judge Cheap PoC"})

    all_daily, all_updates, window_rows = [], [], []
    shuffle_errors, shuffle_changed = [], 0
    for spec in VARIANTS:
        strategy = spec["name"]
        dispatch_path = details / f"{strategy}_dispatch.csv.gz"
        updates_path = details / f"{strategy}_updates.csv.gz"
        versions_path = details / f"{strategy}_plan_versions.jsonl.gz"
        with gzip.open(dispatch_path, "wt", encoding="utf-8", newline="") as dispatch_stream, \
             gzip.open(updates_path, "wt", encoding="utf-8", newline="") as updates_stream, \
             gzip.open(versions_path, "wt", encoding="utf-8") as versions_stream:
            dispatch_writer = update_writer = None
            for window in cfg["windows"]:
                energy = shared_states[window["start"]]
                window_daily = []
                first, last = date_index[window["start"]], date_index[window["end"]]
                for i in range(first, last + 1):
                    d, day_start_energy = dates[i], energy
                    current = np.zeros(144)
                    day_rows, day_updates = [], []
                    for issue_no, hour in enumerate(spec["issues"]):
                        start = hour * 6
                        forecast_hour = 0 if spec["pv_source"] == "zero" else hour
                        pvf = forecasts[(i, 0)][start:] if forecast_hour == 0 else forecasts[(i, forecast_hour)]
                        lf = load_pred[i][start:]
                        history = list(range(max(1, i - cfg["residual_days"]), i))
                        paths = [residuals[(j, forecast_hour)][start - forecast_hour * 6:]
                                 if forecast_hour else residuals[(j, 0)][start:] for j in history]
                        previous = None if hour == 0 else current[start:].copy()
                        price_forecast, price_bias = decision_price(actual_price, i, start, cfg,
                                                                    bool(spec.get("price_update")))
                        candidates, scenario_loads, scenario_pvs, solver_names = [], [], [], []
                        if spec["method"] == "margin":
                            margin_value = hourly_margin(paths, cfg["robust_quantile"])
                            planned_load = lf + (np.zeros_like(lf) if margin_value is None else margin_value)
                            flow, info = plan_with_audit(price_forecast, planned_load, pvf, energy,
                                cfg["planned_terminal_energy"], cfg, previous)
                            candidates.append(("margin", flow["g"])); solver_names.append(info["formulation"])
                            scenario_loads, scenario_pvs = [planned_load], [pvf]
                        elif spec["method"] == "scenario" and paths:
                            paths = paths[-cfg["scenario_days"]:]
                            if spec.get("shuffle"):
                                paths, marginal_error, changed = shuffled_paths(paths,
                                    cfg["shuffle_seed"] + i * 5 + hour)
                                shuffle_errors.append(marginal_error); shuffle_changed += changed
                            flow, info = plan_with_audit(price_forecast, lf, pvf, energy,
                                cfg["planned_terminal_energy"], cfg, previous)
                            candidates.append(("point", flow["g"])); solver_names.append(info["formulation"])
                            margin_value = hourly_margin(paths, cfg["robust_quantile"])
                            flow, info = plan_with_audit(price_forecast, lf + margin_value, pvf, energy,
                                cfg["planned_terminal_energy"], cfg, previous)
                            candidates.append(("margin", flow["g"])); solver_names.append(info["formulation"])
                            for number, path in enumerate(paths):
                                scenario_load = np.maximum(0.0, lf + path)
                                flow, info = plan_with_audit(price_forecast, scenario_load, pvf, energy,
                                    cfg["planned_terminal_energy"], cfg, previous)
                                candidates.append((f"residual_{number}", flow["g"])); solver_names.append(info["formulation"])
                                scenario_loads.append(scenario_load); scenario_pvs.append(pvf)
                            scenario_loads.append(lf); scenario_pvs.append(pvf)
                        else:
                            flow, info = plan_with_audit(price_forecast, lf, pvf, energy,
                                cfg["planned_terminal_energy"], cfg, previous)
                            candidates.append(("point", flow["g"])); solver_names.append(info["formulation"])
                            scenario_loads, scenario_pvs = [lf], [pvf]
                        if previous is not None:
                            candidates.append(("keep_previous", previous))
                        risk = cfg["scenario_risk_weight"] if spec["method"] == "scenario" else 0.0
                        chosen, scored = choose_candidate(price_forecast, candidates, scenario_loads,
                            scenario_pvs, energy, cfg, previous, risk, cfg["scenario_alpha"])
                        choice_plan = chosen.plan
                        actual_fee = 0.0 if previous is None else float(np.sum(
                            0.5 * actual_price[i, start:] * np.abs(choice_plan - previous)))
                        decision_fee = 0.0 if previous is None else float(np.sum(
                            0.5 * price_forecast * np.abs(choice_plan - previous)))
                        increased = 0.0 if previous is None else float(np.maximum(choice_plan - previous, 0).sum())
                        decreased = 0.0 if previous is None else float(np.maximum(previous - choice_plan, 0).sum())
                        current[start:] = choice_plan
                        update = {"strategy": strategy, "window": window["name"], "period": window["period"],
                            "date": d, "issue_hour": hour, "plan_version": issue_no,
                            "chosen_source": chosen.source, "candidate_count": len(scored),
                            "chosen_score": chosen.score, "scenario_mean": chosen.scenario_mean,
                            "scenario_cvar": chosen.scenario_cvar, "increase_kwh": increased,
                            "decrease_kwh": decreased, "adjustment_cost": actual_fee,
                            "decision_adjustment_cost": decision_fee,
                            "changed": int(increased + decreased > 1e-6), "price_bias": price_bias,
                            "solver": "+".join(sorted(set(solver_names)))}
                        day_updates.append(update)
                        versions_stream.write(json.dumps({"strategy": strategy, "window": window["name"],
                            "period": window["period"], "date": d, "issue_hour": hour,
                            "start_slot": start, "history_end": dates[i - 1],
                            "price_update": bool(spec.get("price_update")), "price_bias": price_bias,
                            "decision_price": price_forecast.tolist(),
                            "settlement_price": actual_price[i, start:].tolist(),
                            "old": None if previous is None else previous.tolist(),
                            "new": choice_plan.tolist(), "fee": actual_fee,
                            "decision_fee": decision_fee}, ensure_ascii=False) + "\n")
                        if update_writer is None:
                            update_writer = csv.DictWriter(updates_stream, fieldnames=list(update)); update_writer.writeheader()
                        update_writer.writerow(update)

                        end_slot = 144 if issue_no + 1 == len(spec["issues"]) else spec["issues"][issue_no + 1] * 6
                        segment, energy = execute(actual_price[i, start:end_slot], current[start:end_slot],
                            load[i, start:end_slot], actual_pv[i, start:end_slot], energy, cfg)
                        for local, row in enumerate(segment):
                            item = dict(row)
                            item.update(strategy=strategy, window=window["name"], period=window["period"],
                                date=d, slot=start + local, issue_hour=hour, plan_version=issue_no,
                                ordinary_kwh=item.pop("plan_kwh"), ordinary_cost=item.pop("plan_cost"),
                                adjustment_cost=0.0)
                            item["total_cost"] = item["ordinary_cost"] + item["emergency_cost"]
                            day_rows.append(item)
                            if dispatch_writer is None:
                                dispatch_writer = csv.DictWriter(dispatch_stream, fieldnames=list(item)); dispatch_writer.writeheader()
                            dispatch_writer.writerow(item)

                    ordinary_cost = float(math.fsum(row["ordinary_cost"] for row in day_rows))
                    emergency_cost = float(math.fsum(row["emergency_cost"] for row in day_rows))
                    adjustment_cost = float(math.fsum(row["adjustment_cost"] for row in day_updates))
                    daily = {"strategy": strategy, "window": window["name"], "period": window["period"],
                        "date": d, "ordinary_kwh": float(math.fsum(row["ordinary_kwh"] for row in day_rows)),
                        "emergency_kwh": float(math.fsum(row["emergency_kwh"] for row in day_rows)),
                        "ordinary_cost": ordinary_cost, "adjustment_cost": adjustment_cost,
                        "emergency_cost": emergency_cost,
                        "total_cost": ordinary_cost + adjustment_cost + emergency_cost,
                        "paid_grid_spill_kwh": float(math.fsum(row["paid_grid_spill_kwh"] for row in day_rows)),
                        "pv_spill_kwh": float(math.fsum(row["pv_spill_kwh"] for row in day_rows)),
                        "e_start": day_start_energy, "e_end": energy,
                        "updates": max(0, len(day_updates) - 1),
                        "changed_updates": sum(row["changed"] for row in day_updates if row["issue_hour"]),
                        "emergency_slots": sum(row["emergency_kwh"] > 1e-6 for row in day_rows)}
                    window_daily.append(daily); all_daily.append(daily)
                    all_updates.extend(row for row in day_updates if row["issue_hour"])
                window_rows.append(window_summary(strategy, window, window_daily,
                    actual_price[first:last + 1], cfg))
                print(strategy, window["name"], round(window_rows[-1]["inventory_adjusted_cost"], 2), flush=True)

    if shuffle_errors and max(shuffle_errors) > 1e-12:
        raise AssertionError("Shuffled negative control changed per-slot marginals")
    dump(run_dir / "shuffle_checks.json", {"max_per_slot_marginal_error": max(shuffle_errors or [0.0]),
        "changed_column_permutations": shuffle_changed, "pass": bool(shuffle_changed > 0 and max(shuffle_errors or [0.0]) <= 1e-12)})
    table(results / "daily_summary.csv", all_daily)
    table(results / "window_summary.csv", window_rows)
    table(results / "update_summary.csv", all_updates)

    periods, summaries = [], []
    for spec in VARIANTS:
        strategy = spec["name"]
        selected_windows = [row for row in window_rows if row["strategy"] == strategy]
        selected_daily = [row for row in all_daily if row["strategy"] == strategy]
        summaries.append(combined_summary(strategy, "all", selected_windows, selected_daily, cfg))
        for period in ["development", "validation", "evaluation"]:
            wrows = [row for row in selected_windows if row["period"] == period]
            drows = [row for row in selected_daily if row["period"] == period]
            periods.append(combined_summary(strategy, period, wrows, drows, cfg))
    table(results / "summary_tables.csv", summaries)
    table(results / "periods.csv", periods)

    lookup = {(row["strategy"], row["period"]): row for row in periods}
    baseline, candidate, shuffled, updated = "rolling_margin_mean14", "rolling_scenario_mean14", \
        "rolling_scenario_shuffled", "rolling_scenario_price_update"
    a_checks, b_checks, negative = {}, {}, {}
    for period in ["validation", "evaluation"]:
        a_checks[period] = gate(lookup[(candidate, period)], lookup[(baseline, period)], cfg)
        b_checks[period] = gate(lookup[(updated, period)], lookup[(candidate, period)], cfg)
        negative[period] = {"candidate_inventory_cost": lookup[(candidate, period)]["inventory_adjusted_cost"],
            "shuffled_inventory_cost": lookup[(shuffled, period)]["inventory_adjusted_cost"],
            "candidate_cvar90": lookup[(candidate, period)]["daily_cvar90"],
            "shuffled_cvar90": lookup[(shuffled, period)]["daily_cvar90"],
            "pass": bool(lookup[(candidate, period)]["inventory_adjusted_cost"] < lookup[(shuffled, period)]["inventory_adjusted_cost"] or
                         lookup[(candidate, period)]["daily_cvar90"] < lookup[(shuffled, period)]["daily_cvar90"])}
    a_pass = all(row["pass"] for row in a_checks.values()) and all(row["pass"] for row in negative.values())
    b_pass = a_pass and all(row["pass"] for row in b_checks.values()) and checks["future_price_boundary_pass"]
    selected = updated if b_pass else candidate if a_pass else baseline
    dump(run_dir / "selection.json", {"baseline": baseline, "candidate_a": candidate,
        "candidate_b": updated, "negative_control": shuffled, "candidate_a_period_checks": a_checks,
        "candidate_a_negative_control_checks": negative, "candidate_b_period_checks": b_checks,
        "candidate_a_scientific_target_pass": a_pass, "candidate_b_scientific_target_pass": b_pass,
        "selected_for_human_model_gate": selected,
        "selection_status": "computed candidate; requires HUMAN MODEL GATE",
        "full_result4_3_generated": False})
    dump(run_dir / "completion.json", {"pass": True, "runtime_seconds": time.perf_counter() - started,
        "strategies": len(VARIANTS), "windows_each": len(cfg["windows"]), "days_each": 84,
        "dispatch_rows": len(VARIANTS) * 84 * 144, "selected_for_human_model_gate": selected,
        "full_detail_location": "details/ (locally regenerated audit evidence)"})
    print("completed", round(time.perf_counter() - started, 2), "seconds", selected, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--price-input", type=Path,
        default=ROOT / "experiments/q4-saa-full-20260912-01/input_prices.json")
    args = parser.parse_args()
    main(args.run_dir, args.price_input.resolve())
