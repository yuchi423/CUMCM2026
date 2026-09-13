"""Run the 365-day causal warm-up and 334-day Question 4-3 scenario model."""
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
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import openpyxl
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_q2_inputs import read_sources
from q2_core import execute
from q3_core import choose_candidate, empirical_cvar, plan_with_audit
from q3_shared import fee_checks, hourly_margin, load_forecasts, read_q3_forecasts
from run_q4_price_poc import read_price_matrix

STRATEGY = "rolling_scenario_mean14"
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


def period(date: str, cfg: dict) -> str:
    if date < cfg["evaluation_start"]:
        return "warmup"
    if date <= cfg["development_end"]:
        return "development"
    if date <= cfg["validation_end"]:
        return "validation"
    return "evaluation"


def aggregate(name: str, rows: list[dict], prices: np.ndarray, cfg: dict) -> dict:
    totals = {key: float(math.fsum(float(row[key]) for row in rows)) for key in SUMS}
    mean_price = float(np.mean(prices))
    adjusted = totals["total_cost"] + cfg["terminal_value_multiple"] * mean_price * (
        float(rows[0]["e_start"]) - float(rows[-1]["e_end"]))
    return {"strategy": STRATEGY, "period": name, "days": len(rows), **totals,
            "e_start": float(rows[0]["e_start"]), "e_end": float(rows[-1]["e_end"]),
            "mean_actual_price": mean_price, "inventory_adjusted_cost": float(adjusted),
            "daily_cvar90": empirical_cvar(
                [row["total_cost"] for row in rows], cfg["scenario_alpha"]),
            "updates": int(sum(int(row["updates"]) for row in rows)),
            "changed_updates": int(sum(int(row["changed_updates"]) for row in rows)),
            "emergency_slots": int(sum(int(row["emergency_slots"]) for row in rows))}


def cold_start_load(cfg: dict) -> np.ndarray:
    """Use the already supplied Q1 load curve as the causal prior on 1 January."""
    book = openpyxl.load_workbook(ROOT / "data/raw/附件1.xlsx", read_only=True, data_only=True)
    rows = list(book["Sheet1"].iter_rows(min_row=2, values_only=True))
    book.close()
    values = np.asarray([float(row[2]) for row in rows], dtype=float) * cfg["step_hours"]
    if len(values) != 144 or not np.all(np.isfinite(values)) or np.min(values) < 0:
        raise AssertionError("Invalid Q1 cold-start load prior")
    return values


def causal_price(prices: np.ndarray, index: int, start: int, cfg: dict,
                 q1_price: np.ndarray) -> tuple[np.ndarray, str, str | None, str | None]:
    """Forecast from completed days only; Attachment 1 is the Jan-1 cold-start prior."""
    if index == 0:
        return q1_price[start:].copy(), "attachment1_prior", None, None
    begin = max(0, index - cfg["price_history_days"])
    curve = np.maximum(cfg["price_floor"], prices[begin:index].mean(axis=0))[start:]
    return curve, "completed_day_mean", str(begin), str(index - 1)


def main(run_arg: str, price_input: Path) -> None:
    run_dir = (ROOT / run_arg).resolve()
    if not run_dir.is_relative_to((ROOT / "experiments").resolve()):
        raise ValueError("Run directory must be under experiments")
    run_dir.mkdir(parents=True, exist_ok=False)
    details, results = run_dir / "details", run_dir / "results"
    details.mkdir(); results.mkdir()

    cfg = json.loads((ROOT / "configs/q4_3_scenario_full.json").read_text(encoding="utf-8"))
    source = read_sources(cfg)
    dates = source["dates"]
    load, actual_pv = np.asarray(source["load"], float), np.asarray(source["pv"], float)
    q1_price = np.asarray(source["prices"], dtype=float)
    price_dates, actual_price, _, price_audit = read_price_matrix(price_input)
    if dates != price_dates:
        raise ValueError("Attachment 2 and Attachment 4 dates do not align")
    shutil.copyfile(price_input, run_dir / "input_prices.json")
    _, pv_curve, forecast_hash = read_q3_forecasts(cfg, actual_pv)
    load_pred = load_forecasts(load, dates)
    load_pred[0] = cold_start_load(cfg)
    date_index = {d: i for i, d in enumerate(dates)}
    first, last = date_index[cfg["evaluation_start"]], date_index[cfg["evaluation_end"]]
    if len(dates) != 365 or last - first + 1 != 334:
        raise AssertionError("Expected 31 warm-up and 334 evaluation days")

    forecasts, residuals = {}, {}
    for i in range(len(dates)):
        for hour in cfg["issue_hours"]:
            start = hour * 6
            forecast = pv_curve(dates[i], hour, "linear")
            forecasts[(i, hour)] = forecast
            if i:
                residuals[(i, hour)] = ((load[i, start:] - actual_pv[i, start:]) -
                                        (load_pred[i][start:] - forecast))

    checks = fee_checks()
    sample_i = date_index["2025-06-21"]
    base = causal_price(actual_price, sample_i, 36, cfg, q1_price)[0]
    target_changed = actual_price.copy(); target_changed[sample_i, :] += 7.0
    unchanged = causal_price(target_changed, sample_i, 36, cfg, q1_price)[0]
    history_changed = actual_price.copy(); history_changed[sample_i - 1, :] += 0.2
    changed = causal_price(history_changed, sample_i, 36, cfg, q1_price)[0]
    future_load = load.copy(); future_load[31:] += 99999.0
    future_load_pred = load_forecasts(future_load, dates)
    checks.update({
        "target_day_price_perturbation_max_delta": float(np.max(np.abs(base - unchanged))),
        "completed_history_perturbation_min_delta": float(np.min(np.abs(base - changed))),
        "future_price_boundary_pass": bool(np.max(np.abs(base - unchanged)) <= 1e-12 and
                                           np.min(np.abs(base - changed)) > 1e-6),
        "future_load_perturbation_max_delta": float(np.max(np.abs(load_pred[31] - future_load_pred[31]))),
        "future_load_boundary_pass": bool(np.max(np.abs(load_pred[31] - future_load_pred[31])) <= 1e-12),
        "price_information": "Attachment 1 prior on Jan 1; previous up-to-14 completed days thereafter",
        "target_mapping": "same-day remaining slots"
    })
    if not checks["pass_"] or not checks["future_price_boundary_pass"] or not checks["future_load_boundary_pass"]:
        raise AssertionError(checks)
    dump(run_dir / "preflight_checks.json", checks)

    started = time.perf_counter()
    dump(run_dir / "run.json", {
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now().astimezone().isoformat(),
        "config": cfg, "strategy": STRATEGY,
        "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
        "input_hashes": {**source["input_hashes"], "data/raw/附件3.xlsx": forecast_hash,
                         "data/raw/附件4.xlsx": price_audit["sha256"], "price_snapshot": sha(price_input)},
        "experiment_type": "Q4-3 direction-3 causal full-year replay with Jan warm-up",
        "initial_energy_source": "official Jan-1 6000 kWh initial condition",
        "cold_start_source": "Attachment 1 load and price curves; Attachment 3 Jan-1 PV forecasts"
    })

    dispatch_path = details / f"{STRATEGY}_dispatch.csv.gz"
    updates_path = details / f"{STRATEGY}_updates.csv.gz"
    versions_path = details / f"{STRATEGY}_plan_versions.jsonl.gz"
    all_daily, all_updates, eval_price_rows = [], [], []
    solve_count = 0
    max_solver_audit = defaultdict(float)
    formulation_counts = defaultdict(int)
    energy = float(cfg["initial_energy"])
    with gzip.open(dispatch_path, "wt", encoding="utf-8", newline="") as dispatch_stream, \
         gzip.open(updates_path, "wt", encoding="utf-8", newline="") as updates_stream, \
         gzip.open(versions_path, "wt", encoding="utf-8") as versions_stream:
        dispatch_writer = update_writer = None
        for ordinal, i in enumerate(range(0, last + 1), 1):
            d, day_start_energy = dates[i], energy
            current = np.zeros(144)
            day_dispatch, day_updates = [], []
            full_price_forecast = causal_price(actual_price, i, 0, cfg, q1_price)[0]
            if i >= first:
                eval_price_rows.append({
                    "date": d,
                    "mae": float(np.mean(np.abs(full_price_forecast - actual_price[i]))),
                    "rmse": float(np.sqrt(np.mean((full_price_forecast - actual_price[i]) ** 2))),
                    "mean_bias": float(np.mean(full_price_forecast - actual_price[i])),
                    "forecast_mean": float(np.mean(full_price_forecast)),
                    "actual_mean": float(np.mean(actual_price[i]))
                })
            for issue_no, hour in enumerate(cfg["issue_hours"]):
                start = hour * 6
                pvf = forecasts[(i, hour)]
                lf = load_pred[i][start:]
                history = list(range(max(1, i - cfg["residual_days"]), i))
                paths = [residuals[(j, hour)] for j in history][-cfg["scenario_days"]:]
                previous = None if hour == 0 else current[start:].copy()
                price_forecast, price_source, price_begin, price_end = causal_price(
                    actual_price, i, start, cfg, q1_price)
                candidates, scenario_loads, scenario_pvs, solver_names = [], [], [], []
                if paths:
                    flow, info = plan_with_audit(price_forecast, lf, pvf, energy,
                        cfg["planned_terminal_energy"], cfg, previous)
                    candidates.append(("point", flow["g"])); solver_names.append(info["formulation"])
                    solve_count += 1; formulation_counts[info["formulation"]] += 1
                    for k, value in info["audit"].items():
                        if isinstance(value, (int, float)) and k != "pass":
                            max_solver_audit[k] = max(max_solver_audit[k], abs(float(value)))
                    margin = hourly_margin(paths, cfg["robust_quantile"])
                    flow, info = plan_with_audit(price_forecast, lf + margin, pvf, energy,
                        cfg["planned_terminal_energy"], cfg, previous)
                    candidates.append(("margin", flow["g"])); solver_names.append(info["formulation"])
                    solve_count += 1; formulation_counts[info["formulation"]] += 1
                    for k, value in info["audit"].items():
                        if isinstance(value, (int, float)) and k != "pass":
                            max_solver_audit[k] = max(max_solver_audit[k], abs(float(value)))
                    for number, path in enumerate(paths):
                        scenario_load = np.maximum(0.0, lf + path)
                        flow, info = plan_with_audit(price_forecast, scenario_load, pvf, energy,
                            cfg["planned_terminal_energy"], cfg, previous)
                        candidates.append((f"residual_{number}", flow["g"])); solver_names.append(info["formulation"])
                        scenario_loads.append(scenario_load); scenario_pvs.append(pvf)
                        solve_count += 1; formulation_counts[info["formulation"]] += 1
                        for k, value in info["audit"].items():
                            if isinstance(value, (int, float)) and k != "pass":
                                max_solver_audit[k] = max(max_solver_audit[k], abs(float(value)))
                    scenario_loads.append(lf); scenario_pvs.append(pvf)
                else:
                    flow, info = plan_with_audit(price_forecast, lf, pvf, energy,
                        cfg["planned_terminal_energy"], cfg, previous)
                    candidates.append(("point", flow["g"])); solver_names.append(info["formulation"])
                    scenario_loads, scenario_pvs = [lf], [pvf]
                    solve_count += 1; formulation_counts[info["formulation"]] += 1
                    for k, value in info["audit"].items():
                        if isinstance(value, (int, float)) and k != "pass":
                            max_solver_audit[k] = max(max_solver_audit[k], abs(float(value)))
                if previous is not None:
                    candidates.append(("keep_previous", previous))
                chosen, scored = choose_candidate(price_forecast, candidates, scenario_loads,
                    scenario_pvs, energy, cfg, previous, cfg["scenario_risk_weight"], cfg["scenario_alpha"])
                choice_plan = chosen.plan
                actual_fee = 0.0 if previous is None else float(np.sum(
                    0.5 * actual_price[i, start:] * np.abs(choice_plan - previous)))
                decision_fee = 0.0 if previous is None else float(np.sum(
                    0.5 * price_forecast * np.abs(choice_plan - previous)))
                increased = 0.0 if previous is None else float(np.maximum(choice_plan - previous, 0).sum())
                decreased = 0.0 if previous is None else float(np.maximum(previous - choice_plan, 0).sum())
                current[start:] = choice_plan
                update = {
                    "strategy": STRATEGY, "period": period(d, cfg), "date": d,
                    "issue_hour": hour, "plan_version": issue_no, "chosen_source": chosen.source,
                    "candidate_count": len(scored), "chosen_score": chosen.score,
                    "scenario_mean": chosen.scenario_mean, "scenario_cvar": chosen.scenario_cvar,
                    "increase_kwh": increased, "decrease_kwh": decreased,
                    "adjustment_cost": actual_fee, "decision_adjustment_cost": decision_fee,
                    "changed": int(increased + decreased > 1e-6),
                    "scenario_count": len(scenario_loads), "solver": "+".join(sorted(set(solver_names)))
                }
                day_updates.append(update)
                versions_stream.write(json.dumps({
                    "strategy": STRATEGY, "period": period(d, cfg), "date": d,
                    "issue_hour": hour, "start_slot": start,
                    "residual_history_start": None if not history else dates[history[-len(paths)]],
                    "residual_history_end": None if not history else dates[history[-1]],
                    "price_source": price_source,
                    "price_history_start": None if price_begin is None else dates[int(price_begin)],
                    "price_history_end": None if price_end is None else dates[int(price_end)],
                    "decision_price": price_forecast.tolist(),
                    "settlement_price": actual_price[i, start:].tolist(),
                    "old": None if previous is None else previous.tolist(),
                    "new": choice_plan.tolist(), "fee": actual_fee, "decision_fee": decision_fee,
                    "chosen_source": chosen.source, "scenario_count": len(scenario_loads)
                }, ensure_ascii=False) + "\n")
                if update_writer is None:
                    update_writer = csv.DictWriter(updates_stream, fieldnames=list(update)); update_writer.writeheader()
                update_writer.writerow(update)

                end_slot = 144 if issue_no == 3 else cfg["issue_hours"][issue_no + 1] * 6
                segment, energy = execute(actual_price[i, start:end_slot], current[start:end_slot],
                    load[i, start:end_slot], actual_pv[i, start:end_slot], energy, cfg)
                for local, row in enumerate(segment):
                    item = dict(row)
                    item.update(strategy=STRATEGY, period=period(d, cfg), date=d,
                        slot=start + local, issue_hour=hour, plan_version=issue_no,
                        ordinary_kwh=item.pop("plan_kwh"), ordinary_cost=item.pop("plan_cost"),
                        adjustment_cost=0.0)
                    item["total_cost"] = item["ordinary_cost"] + item["emergency_cost"]
                    day_dispatch.append(item)
                    if dispatch_writer is None:
                        dispatch_writer = csv.DictWriter(dispatch_stream, fieldnames=list(item)); dispatch_writer.writeheader()
                    dispatch_writer.writerow(item)

            ordinary_cost = float(math.fsum(row["ordinary_cost"] for row in day_dispatch))
            emergency_cost = float(math.fsum(row["emergency_cost"] for row in day_dispatch))
            adjustment_cost = float(math.fsum(row["adjustment_cost"] for row in day_updates))
            record = {
                "strategy": STRATEGY, "period": period(d, cfg), "date": d,
                "ordinary_kwh": float(math.fsum(row["ordinary_kwh"] for row in day_dispatch)),
                "emergency_kwh": float(math.fsum(row["emergency_kwh"] for row in day_dispatch)),
                "ordinary_cost": ordinary_cost, "adjustment_cost": adjustment_cost,
                "emergency_cost": emergency_cost, "total_cost": ordinary_cost + adjustment_cost + emergency_cost,
                "paid_grid_spill_kwh": float(math.fsum(row["paid_grid_spill_kwh"] for row in day_dispatch)),
                "pv_spill_kwh": float(math.fsum(row["pv_spill_kwh"] for row in day_dispatch)),
                "e_start": day_start_energy, "e_end": energy,
                "updates": 3,
                "changed_updates": sum(row["changed"] for row in day_updates if row["issue_hour"]),
                "emergency_slots": sum(row["emergency_kwh"] > 1e-6 for row in day_dispatch)
            }
            all_daily.append(record)
            all_updates.extend(day_updates)
            if ordinal % 30 == 0 or ordinal == 365:
                print(f"{ordinal}/365 {d} cost={record['total_cost']:.2f}", flush=True)

    warmup_rows = [row for row in all_daily if row["date"] < cfg["evaluation_start"]]
    daily_rows = [row for row in all_daily if row["date"] >= cfg["evaluation_start"]]
    update_rows = [row for row in all_updates if row["date"] >= cfg["evaluation_start"]]
    table(results / "warmup_daily.csv", warmup_rows)
    table(results / "daily_summary.csv", daily_rows)
    table(results / "update_summary.csv", update_rows)
    table(results / "price_metrics_daily.csv", eval_price_rows)
    table(results / "paper_dates_summary.csv", [row for row in daily_rows if row["date"] in cfg["paper_dates"]])

    groups = defaultdict(list)
    for row in daily_rows:
        groups[row["period"]].append(row)
        groups[row["date"][:7]].append(row)
        month = int(row["date"][5:7])
        groups[f"Q{(month - 1) // 3 + 1}"].append(row)

    def grouped(names: list[str]) -> list[dict]:
        output = []
        for name in names:
            rows = groups[name]
            begin = date_index[rows[0]["date"]]; end = date_index[rows[-1]["date"]]
            output.append(aggregate(name, rows, actual_price[begin:end + 1], cfg))
        return output

    table(results / "periods.csv", grouped(["development", "validation", "evaluation"]))
    table(results / "monthly_summary.csv", grouped(sorted(k for k in groups if k.startswith("2025-"))))
    table(results / "quarterly_summary.csv", grouped(["Q1", "Q2", "Q3", "Q4"]))
    summary = aggregate("all", daily_rows, actual_price[first:last + 1], cfg)
    table(results / "summary_tables.csv", [summary])
    solver_checks = {"pass": all(v <= 2e-5 for v in max_solver_audit.values()),
        "solve_count": solve_count, "formulation_counts": dict(formulation_counts),
        "max_solver_audit": dict(max_solver_audit)}
    dump(run_dir / "solver_audit_summary.json", solver_checks)
    if not solver_checks["pass"]:
        raise AssertionError(solver_checks)
    dump(run_dir / "completion.json", {
        "pass": True, "runtime_seconds": time.perf_counter() - started,
        "strategy": STRATEGY, "warmup_days": 31, "evaluation_days": 334,
        "dispatch_rows": 365 * 144, "plan_versions": 365 * 4,
        "evaluation_updates": 334 * 3,
        "full_detail_location": "details/ (locally regenerated audit evidence)"
    })
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--price-input", type=Path,
        default=ROOT / "experiments/q4-saa-full-20260912-01/input_prices.json")
    args = parser.parse_args()
    main(args.run_dir, args.price_input.resolve())
