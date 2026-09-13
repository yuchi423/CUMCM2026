"""Run the 334-day Question 4-3 delivery model with a causal mean-14 price forecast."""
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
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_q2_inputs import read_sources
from q2_core import execute
from q3_core import choose_candidate, plan_with_audit
from q3_shared import fee_checks, hourly_margin, load_forecasts, read_q3_forecasts
from run_q4_3_poc import decision_price
from run_q4_price_poc import read_price_matrix

STRATEGY = "rolling_margin_mean14"
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
            "updates": int(sum(int(row["updates"]) for row in rows)),
            "changed_updates": int(sum(int(row["changed_updates"]) for row in rows)),
            "emergency_slots": int(sum(int(row["emergency_slots"]) for row in rows))}


def main(run_arg: str, price_input: Path) -> None:
    run_dir = (ROOT / run_arg).resolve()
    if not run_dir.is_relative_to((ROOT / "experiments").resolve()):
        raise ValueError("Run directory must be under experiments")
    run_dir.mkdir(parents=True, exist_ok=False)
    details, results = run_dir / "details", run_dir / "results"
    details.mkdir(); results.mkdir()

    cfg = json.loads((ROOT / "configs/q4_3_full.json").read_text(encoding="utf-8"))
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
    first, last = date_index[cfg["evaluation_start"]], date_index[cfg["evaluation_end"]]
    if last - first + 1 != 334:
        raise AssertionError("Expected 334 evaluation days")

    forecasts, residuals = {}, {}
    for i in range(1, len(dates)):
        for hour in cfg["issue_hours"]:
            start = hour * 6
            forecast = pv_curve(dates[i], hour, "linear")
            forecasts[(i, hour)] = forecast
            residuals[(i, hour)] = ((load[i, start:] - actual_pv[i, start:]) -
                                    (load_pred[i][start:] - forecast))

    shared_states = {}
    q3_daily = ROOT / "deliverables/q3/daily_summary.csv"
    with q3_daily.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            shared_states[row["date"]] = float(row["e_start"])
    energy = shared_states[cfg["evaluation_start"]]

    checks = fee_checks()
    sample_i = date_index["2025-06-21"]
    base, _ = decision_price(actual_price, sample_i, 36, cfg, False)
    target_changed = actual_price.copy()
    target_changed[sample_i, :] += 7.0
    unchanged, _ = decision_price(target_changed, sample_i, 36, cfg, False)
    history_changed = actual_price.copy()
    history_changed[sample_i - 1, :] += 0.2
    changed, _ = decision_price(history_changed, sample_i, 36, cfg, False)
    checks.update({
        "target_day_price_perturbation_max_delta": float(np.max(np.abs(base - unchanged))),
        "completed_history_perturbation_min_delta": float(np.min(np.abs(base - changed))),
        "future_price_boundary_pass": bool(np.max(np.abs(base - unchanged)) <= 1e-12 and
                                           np.min(np.abs(base - changed)) > 1e-6),
        "price_information": "previous 14 completed days only"
    })
    if not checks["pass_"] or not checks["future_price_boundary_pass"]:
        raise AssertionError(checks)
    dump(run_dir / "preflight_checks.json", checks)

    started = time.perf_counter()
    dump(run_dir / "run.json", {
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now().astimezone().isoformat(),
        "config": cfg, "strategy": STRATEGY,
        "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
        "input_hashes": {**source["input_hashes"], "data/raw/附件3.xlsx": forecast_hash,
                         "data/raw/附件4.xlsx": price_audit["sha256"],
                         "price_snapshot": sha(price_input),
                         "deliverables/q3/daily_summary.csv": sha(q3_daily)},
        "experiment_type": "334-day operational Q4-3 delivery with causal mean-14 price forecast",
        "initial_energy_source": "Q3 selected model state at 2025-02-01"
    })

    dispatch_path = details / f"{STRATEGY}_dispatch.csv.gz"
    updates_path = details / f"{STRATEGY}_updates.csv.gz"
    versions_path = details / f"{STRATEGY}_plan_versions.jsonl.gz"
    daily_rows, update_rows, price_rows = [], [], []
    with gzip.open(dispatch_path, "wt", encoding="utf-8", newline="") as dispatch_stream, \
         gzip.open(updates_path, "wt", encoding="utf-8", newline="") as updates_stream, \
         gzip.open(versions_path, "wt", encoding="utf-8") as versions_stream:
        dispatch_writer = update_writer = None
        for ordinal, i in enumerate(range(first, last + 1), 1):
            date, day_start_energy = dates[i], energy
            current = np.zeros(144)
            day_dispatch, day_updates = [], []
            full_price_forecast, _ = decision_price(actual_price, i, 0, cfg, False)
            price_rows.append({
                "date": date,
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
                paths = [residuals[(j, hour)] for j in history]
                margin = hourly_margin(paths, cfg["robust_quantile"])
                planned_load = lf + (np.zeros_like(lf) if margin is None else margin)
                previous = None if hour == 0 else current[start:].copy()
                price_forecast, _ = decision_price(actual_price, i, start, cfg, False)
                flow, info = plan_with_audit(price_forecast, planned_load, pvf, energy,
                    cfg["planned_terminal_energy"], cfg, previous)
                candidates = [("margin", flow["g"])]
                if previous is not None:
                    candidates.append(("keep_previous", previous))
                chosen, scored = choose_candidate(price_forecast, candidates, [planned_load], [pvf],
                    energy, cfg, previous, 0.0, 0.9)
                choice_plan = chosen.plan
                actual_fee = 0.0 if previous is None else float(np.sum(
                    0.5 * actual_price[i, start:] * np.abs(choice_plan - previous)))
                decision_fee = 0.0 if previous is None else float(np.sum(
                    0.5 * price_forecast * np.abs(choice_plan - previous)))
                increased = 0.0 if previous is None else float(np.maximum(choice_plan - previous, 0).sum())
                decreased = 0.0 if previous is None else float(np.maximum(previous - choice_plan, 0).sum())
                current[start:] = choice_plan
                update = {
                    "strategy": STRATEGY, "period": period(date, cfg), "date": date,
                    "issue_hour": hour, "plan_version": issue_no, "chosen_source": chosen.source,
                    "candidate_count": len(scored), "chosen_score": chosen.score,
                    "increase_kwh": increased, "decrease_kwh": decreased,
                    "adjustment_cost": actual_fee, "decision_adjustment_cost": decision_fee,
                    "changed": int(increased + decreased > 1e-6), "solver": info["formulation"]
                }
                day_updates.append(update)
                versions_stream.write(json.dumps({
                    "strategy": STRATEGY, "period": period(date, cfg), "date": date,
                    "issue_hour": hour, "start_slot": start, "history_end": dates[i - 1],
                    "price_history_start": dates[i - cfg["price_history_days"]],
                    "price_history_end": dates[i - 1],
                    "decision_price": price_forecast.tolist(),
                    "settlement_price": actual_price[i, start:].tolist(),
                    "old": None if previous is None else previous.tolist(),
                    "new": choice_plan.tolist(), "fee": actual_fee,
                    "decision_fee": decision_fee
                }, ensure_ascii=False) + "\n")
                if update_writer is None:
                    update_writer = csv.DictWriter(updates_stream, fieldnames=list(update))
                    update_writer.writeheader()
                update_writer.writerow(update)

                end_slot = 144 if issue_no == 3 else cfg["issue_hours"][issue_no + 1] * 6
                segment, energy = execute(actual_price[i, start:end_slot], current[start:end_slot],
                    load[i, start:end_slot], actual_pv[i, start:end_slot], energy, cfg)
                for local, row in enumerate(segment):
                    item = dict(row)
                    item.update(strategy=STRATEGY, period=period(date, cfg), date=date,
                        slot=start + local, issue_hour=hour, plan_version=issue_no,
                        ordinary_kwh=item.pop("plan_kwh"), ordinary_cost=item.pop("plan_cost"),
                        adjustment_cost=0.0)
                    item["total_cost"] = item["ordinary_cost"] + item["emergency_cost"]
                    day_dispatch.append(item)
                    if dispatch_writer is None:
                        dispatch_writer = csv.DictWriter(dispatch_stream, fieldnames=list(item))
                        dispatch_writer.writeheader()
                    dispatch_writer.writerow(item)

            ordinary_cost = float(math.fsum(row["ordinary_cost"] for row in day_dispatch))
            emergency_cost = float(math.fsum(row["emergency_cost"] for row in day_dispatch))
            adjustment_cost = float(math.fsum(row["adjustment_cost"] for row in day_updates))
            daily_rows.append({
                "strategy": STRATEGY, "period": period(date, cfg), "date": date,
                "ordinary_kwh": float(math.fsum(row["ordinary_kwh"] for row in day_dispatch)),
                "emergency_kwh": float(math.fsum(row["emergency_kwh"] for row in day_dispatch)),
                "ordinary_cost": ordinary_cost, "adjustment_cost": adjustment_cost,
                "emergency_cost": emergency_cost,
                "total_cost": ordinary_cost + adjustment_cost + emergency_cost,
                "paid_grid_spill_kwh": float(math.fsum(row["paid_grid_spill_kwh"] for row in day_dispatch)),
                "pv_spill_kwh": float(math.fsum(row["pv_spill_kwh"] for row in day_dispatch)),
                "e_start": day_start_energy, "e_end": energy,
                "updates": 3,
                "changed_updates": sum(row["changed"] for row in day_updates if row["issue_hour"]),
                "emergency_slots": sum(row["emergency_kwh"] > 1e-6 for row in day_dispatch)
            })
            update_rows.extend(day_updates)
            if ordinal % 30 == 0 or ordinal == 334:
                print(f"{ordinal}/334 {date} cost={daily_rows[-1]['total_cost']:.2f}", flush=True)

    table(results / "daily_summary.csv", daily_rows)
    table(results / "update_summary.csv", update_rows)
    table(results / "price_metrics_daily.csv", price_rows)
    table(results / "paper_dates_summary.csv",
          [row for row in daily_rows if row["date"] in cfg["paper_dates"]])

    groups = defaultdict(list)
    for row in daily_rows:
        groups[row["period"]].append(row)
        groups[row["date"][:7]].append(row)
        month = int(row["date"][5:7])
        groups[f"Q{(month - 2) // 3 + 1}"].append(row)
    def grouped(names: list[str]) -> list[dict]:
        return [aggregate(name, groups[name],
            actual_price[date_index[groups[name][0]["date"]]:date_index[groups[name][-1]["date"]] + 1], cfg)
            for name in names]
    table(results / "periods.csv", grouped(["development", "validation", "evaluation"]))
    table(results / "monthly_summary.csv", grouped(sorted(k for k in groups if k.startswith("2025-"))))
    table(results / "quarterly_summary.csv", grouped(["Q1", "Q2", "Q3", "Q4"]))
    summary = aggregate("all", daily_rows, actual_price[first:last + 1], cfg)
    table(results / "summary_tables.csv", [summary])
    dump(run_dir / "completion.json", {
        "pass": True, "runtime_seconds": time.perf_counter() - started,
        "strategy": STRATEGY, "days": 334, "dispatch_rows": 334 * 144,
        "plan_versions": 334 * 4, "updates": 334 * 3,
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
