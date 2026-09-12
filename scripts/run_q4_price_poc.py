"""Run the approved Task 4 causal-price Cheap PoC from the repository root."""
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
from datetime import date, datetime
from pathlib import Path

import numpy as np
import scipy
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from q2_core import execute, margin, plan


SUMS = [
    "plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
    "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh",
]


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


def manifest_hashes() -> dict[str, str]:
    with (ROOT / "data/MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
        return {row["path_or_uri"]: row["sha256"] for row in csv.DictReader(stream)}


def read_price_matrix(snapshot_path: Path) -> tuple[list[str], np.ndarray, list[str], dict]:
    rel = "data/raw/附件4.xlsx"
    path = ROOT / rel
    expected = manifest_hashes()[rel]
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected:
        raise ValueError("附件4 hash mismatch")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot["source"] != rel or snapshot["source_sha256"] != actual_hash:
        raise ValueError("Prepared price snapshot does not match Attachment 4")
    dates = snapshot["dates"]
    values = snapshot["prices"]
    headers = snapshot["headers"]
    if len(dates) != 365 or len(set(dates)) != 365 or len(headers) != 144:
        raise ValueError("附件4 must contain 365 unique dates")
    array = np.asarray(values)
    if array.shape != (365, 144) or not np.all(np.isfinite(array)) or np.min(array) <= 0:
        raise ValueError("Invalid price snapshot values")
    audit = {
        "sha256": actual_hash,
        "days": len(dates),
        "slots_per_day": int(array.shape[1]),
        "missing": 0,
        "minimum": float(array.min()),
        "maximum": float(array.max()),
        "mean": float(array.mean()),
        "unique_day_curves": len({tuple(row) for row in values}),
        "snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        "reader_python": snapshot["python"],
        "reader_openpyxl": snapshot["openpyxl"],
    }
    return dates, array, headers, audit


def supply_forecast(load: np.ndarray, pv: np.ndarray, dates: list[str], i: int) -> tuple[np.ndarray, np.ndarray]:
    weekday = date.fromisoformat(dates[i]).weekday()
    indices = [j for j in range(max(0, i - 35), i) if date.fromisoformat(dates[j]).weekday() == weekday]
    if len(indices) < 2:
        indices = list(range(max(0, i - 7), i))
    if not indices:
        raise ValueError("Supply forecast requires past data")
    return load[indices].mean(axis=0), pv[max(0, i - 7):i].mean(axis=0)


def price_forecast(method: str, prices: np.ndarray, i: int, fixed: np.ndarray, cfg: dict) -> np.ndarray:
    if i <= 0:
        raise ValueError("Price forecast requires at least one past day")
    if method == "fixed_attachment1":
        result = fixed.copy()
    elif method == "oracle":
        result = prices[i].copy()
    elif method == "lag1":
        result = prices[i - 1].copy()
    elif method.startswith("mean"):
        count = int(method[4:])
        result = prices[max(0, i - count):i].mean(axis=0)
    elif method == "weekday35":
        weekday = date.fromisoformat(cfg["dates"][i]).weekday()
        indices = [j for j in range(max(0, i - 35), i)
                   if date.fromisoformat(cfg["dates"][j]).weekday() == weekday]
        if len(indices) < 2:
            indices = list(range(max(0, i - 7), i))
        result = prices[indices].mean(axis=0)
    elif method == "ewma14":
        history = prices[max(0, i - 14):i]
        alpha = float(cfg["ewma_alpha"])
        weights = (1.0 - alpha) ** np.arange(len(history) - 1, -1, -1)
        result = np.average(history, axis=0, weights=weights)
    elif method == "lag1_shift6h":
        result = np.roll(prices[i - 1], int(cfg["negative_control_shift_slots"]))
    else:
        raise KeyError(method)
    if result.shape != (144,) or not np.all(np.isfinite(result)) or np.min(result) <= 0:
        raise ValueError(f"Invalid forecast from {method}")
    return result


def daily_price_metrics(predicted: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    error = predicted - actual
    rank = float(spearmanr(predicted, actual).statistic)
    if not math.isfinite(rank):
        rank = 0.0
    count = len(actual) // 4
    high_actual = set(np.argpartition(actual, -count)[-count:])
    high_pred = set(np.argpartition(predicted, -count)[-count:])
    low_actual = set(np.argpartition(actual, count)[:count])
    low_pred = set(np.argpartition(predicted, count)[:count])
    return {
        "mae": float(np.mean(np.abs(error))),
        "mse": float(np.mean(error ** 2)),
        "rank_correlation": rank,
        "high_quartile_overlap": len(high_actual & high_pred) / count,
        "low_quartile_overlap": len(low_actual & low_pred) / count,
    }


def aggregate_price_metrics(rows: list[dict]) -> dict:
    days = len(rows)
    return {
        "days": days,
        "mae": float(np.mean([r["mae"] for r in rows])),
        "rmse": float(math.sqrt(np.mean([r["mse"] for r in rows]))),
        "rank_correlation": float(np.mean([r["rank_correlation"] for r in rows])),
        "high_quartile_overlap": float(np.mean([r["high_quartile_overlap"] for r in rows])),
        "low_quartile_overlap": float(np.mean([r["low_quartile_overlap"] for r in rows])),
    }


def aggregate_windows(rows: list[dict]) -> dict:
    return {
        "windows": len(rows),
        "days": int(sum(r["days"] for r in rows)),
        **{key: float(sum(r[key] for r in rows)) for key in SUMS},
        "inventory_adjusted_cost": float(sum(r["inventory_adjusted_cost"] for r in rows)),
        "total_window_start_energy": float(sum(r["e_start"] for r in rows)),
        "total_window_end_energy": float(sum(r["e_end"] for r in rows)),
        "emergency_slots": int(sum(r["emergency_slots"] for r in rows)),
        "milp_days": int(sum(r["milp_days"] for r in rows)),
        "max_plan_audit": float(max(r["max_plan_audit"] for r in rows)),
    }


def run(output: Path, price_input: Path) -> None:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    results = output / "results"
    results.mkdir()
    figures = output / "figures"
    figures.mkdir()

    cfg = json.loads((ROOT / "configs/q4_price_poc.json").read_text(encoding="utf-8"))
    q2_source = ROOT / "experiments/q2-full-20260911-01/inputs.json"
    q2 = json.loads(q2_source.read_text(encoding="utf-8"))
    dates = q2["dates"]
    load, pv, fixed = np.asarray(q2["load"]), np.asarray(q2["pv"]), np.asarray(q2["prices"])
    price_dates, actual_prices, headers, price_audit = read_price_matrix(price_input)
    if dates != price_dates or actual_prices.shape != (365, 144):
        raise ValueError("Price and supply dates do not align")
    cfg["dates"] = dates
    shutil.copyfile(price_input, output / "input_prices.json")

    combined_states = {}
    with (ROOT / "experiments/q2-improve-20260911-01/daily.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["strategy"] == "combined":
                combined_states[row["date"]] = float(row["e_start"])
    missing_starts = [w["start"] for w in cfg["windows"] if w["start"] not in combined_states]
    if missing_starts:
        raise ValueError(f"Missing Q2 shared states: {missing_starts}")

    input_audit = {
        "q2_snapshot_sha256": hashlib.sha256(q2_source.read_bytes()).hexdigest(),
        "attachment4": price_audit,
        "dates_aligned": True,
        "shared_window_initial_states": {w["name"]: combined_states[w["start"]] for w in cfg["windows"]},
        "time_headers_first_last": [headers[0], headers[-1]],
    }
    dump(output / "input_audit.json", input_audit)
    dump(output / "run.json", {
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now().astimezone().isoformat(),
        "config": {k: v for k, v in cfg.items() if k != "dates"},
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "price_reader_python": price_audit["reader_python"],
        "price_reader_openpyxl": price_audit["reader_openpyxl"],
        "experiment_type": "approved Cheap PoC; six disjoint 14-day windows; not a full-year delivery",
    })

    supply_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    def supply(i: int) -> tuple[np.ndarray, np.ndarray]:
        if i not in supply_cache:
            supply_cache[i] = supply_forecast(load, pv, dates, i)
        return supply_cache[i]

    planning_cfg = dict(cfg)
    price_metric_daily = []
    daily = []
    window_summaries = []
    plan_records = []
    dispatch_path = output / "dispatch.csv.gz"
    ledger = gzip.open(dispatch_path, "wt", encoding="utf-8", newline="")
    dispatch_writer = None
    for method in cfg["methods"]:
        for window in cfg["windows"]:
            start = dates.index(window["start"])
            finish = dates.index(window["end"])
            energy = combined_states[window["start"]]
            day_rows = []
            actual_window_prices = []
            for i in range(start, finish + 1):
                lf, vf = supply(i)
                residuals = []
                for h in range(max(1, i - cfg["residual_days"]), i):
                    old_lf, old_vf = supply(h)
                    residuals.append(load[h] - pv[h] - old_lf + old_vf)
                extra = margin(residuals, planning_cfg)
                predicted_price = price_forecast(method, actual_prices, i, fixed, cfg)
                flow, solver = plan(predicted_price, lf + extra, vf, energy, planning_cfg)
                rows, ending = execute(actual_prices[i], flow["g"], load[i], pv[i], energy, planning_cfg)
                metrics = daily_price_metrics(predicted_price, actual_prices[i])
                price_metric_daily.append({"method": method, "window": window["name"],
                    "period": window["period"], "date": dates[i], **metrics})
                record = {
                    "method": method, "window": window["name"], "period": window["period"],
                    "date": dates[i], "e_start": float(energy), "e_end": float(ending),
                    **{key: float(sum(r[key] for r in rows)) for key in SUMS},
                    "predicted_plan_cost": float(np.dot(predicted_price, flow["g"])),
                    "actual_mean_price": float(np.mean(actual_prices[i])),
                    "forecast_mean_price": float(np.mean(predicted_price)),
                    "emergency_slots": int(sum(r["emergency_kwh"] > 1e-6 for r in rows)),
                    "solver": solver["formulation"],
                    "solve_seconds": float(solver["seconds"]),
                    "max_plan_audit": float(max(v for k, v in solver["audit"].items()
                                                if k not in {"pass", "mutual_count"})),
                    "plan_mutual_count": int(solver["audit"]["mutual_count"]),
                }
                daily.append(record)
                day_rows.append(record)
                actual_window_prices.extend(actual_prices[i].tolist())
                for row in rows:
                    out = dict(row)
                    out.update(method=method, window=window["name"], period=window["period"],
                               date=dates[i], price_forecast=float(predicted_price[int(row["slot"])]))
                    if dispatch_writer is None:
                        dispatch_writer = csv.DictWriter(ledger, fieldnames=list(out))
                        dispatch_writer.writeheader()
                    dispatch_writer.writerow(out)
                plan_records.append({
                    "method": method, "window": window["name"], "period": window["period"],
                    "date": dates[i], "history_end": dates[i - 1] if method != "oracle" else None,
                    "causal": method != "oracle", "e_start": float(energy),
                    "actual_price": actual_prices[i].tolist(), "forecast_price": predicted_price.tolist(),
                    "ordinary_plan": flow["g"].tolist(), "solver": solver,
                })
                energy = ending
            sums = {key: float(sum(r[key] for r in day_rows)) for key in SUMS}
            mean_price = float(np.mean(actual_window_prices))
            inventory_adjusted = sums["total_cost"] + cfg["discharge_efficiency"] * mean_price * (
                day_rows[0]["e_start"] - day_rows[-1]["e_end"])
            window_summaries.append({
                "method": method, "window": window["name"], "period": window["period"],
                "days": len(day_rows), **sums, "e_start": day_rows[0]["e_start"],
                "e_end": day_rows[-1]["e_end"], "mean_actual_price": mean_price,
                "inventory_adjusted_cost": float(inventory_adjusted),
                "emergency_slots": int(sum(r["emergency_slots"] for r in day_rows)),
                "milp_days": int(sum(r["solver"] == "MILP" for r in day_rows)),
                "max_plan_audit": float(max(r["max_plan_audit"] for r in day_rows)),
            })
            print(method, window["name"], round(sums["total_cost"], 2), flush=True)
    ledger.close()

    table(results / "daily_summary.csv", daily)
    table(results / "window_summary.csv", window_summaries)
    table(results / "price_metrics_daily.csv", price_metric_daily)
    with gzip.open(output / "plans.jsonl.gz", "wt", encoding="utf-8") as stream:
        for record in plan_records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                                    default=lambda x: x.item() if hasattr(x, "item") else x) + "\n")

    summary = []
    periods = []
    price_metrics = []
    for method in cfg["methods"]:
        all_windows = [r for r in window_summaries if r["method"] == method]
        summary.append({"method": method, **aggregate_windows(all_windows)})
        for period in ["development", "validation", "evaluation"]:
            selected_windows = [r for r in all_windows if r["period"] == period]
            periods.append({"method": method, "period": period, **aggregate_windows(selected_windows)})
        for period in ["development", "validation", "evaluation", "all"]:
            rows = [r for r in price_metric_daily if r["method"] == method and
                    (period == "all" or r["period"] == period)]
            price_metrics.append({"method": method, "period": period, **aggregate_price_metrics(rows)})
    table(results / "summary_tables.csv", summary)
    table(results / "periods.csv", periods)
    table(results / "price_forecast_metrics.csv", price_metrics)

    development = {r["method"]: r for r in periods if r["period"] == "development"}
    candidates = cfg["causal_candidates"]
    raw_best = min(candidates, key=lambda name: development[name]["inventory_adjusted_cost"])
    best_cost = development[raw_best]["inventory_adjusted_cost"]
    eligible = [name for name in candidates if development[name]["inventory_adjusted_cost"]
                <= best_cost * (1.0 + cfg["selection_tolerance_fraction"])]
    selected = min(eligible, key=candidates.index)
    later = {}
    for period in ["validation", "evaluation"]:
        values = {r["method"]: r for r in periods if r["period"] == period}
        later[period] = {
            "selected_cost": values[selected]["inventory_adjusted_cost"],
            "fixed_control_cost": values["fixed_attachment1"]["inventory_adjusted_cost"],
            "negative_control_cost": values["lag1_shift6h"]["inventory_adjusted_cost"],
            "beats_fixed_control": values[selected]["inventory_adjusted_cost"] < values["fixed_attachment1"]["inventory_adjusted_cost"],
            "beats_negative_control": values[selected]["inventory_adjusted_cost"] < values["lag1_shift6h"]["inventory_adjusted_cost"],
        }
    scientific_target_pass = all(
        later[period]["beats_fixed_control"] and later[period]["beats_negative_control"]
        for period in ["validation", "evaluation"]
    )
    recommended = selected if scientific_target_pass else cfg["fallback_method"]
    dump(output / "selection.json", {
        "selection_period": "development windows only",
        "raw_best": raw_best,
        "best_inventory_adjusted_cost": best_cost,
        "eligible_within_tolerance": eligible,
        "selected": selected,
        "tie_rule": "within 0.5% prefer lag1, mean7, mean14, mean28, weekday35, ewma14",
        "later_checks": later,
        "scientific_target_pass": scientific_target_pass,
        "fallback_triggered": not scientific_target_pass,
        "recommended_delivery_method": recommended,
        "fallback_note": (None if scientific_target_pass else
            "Selected method was not stably better than both controls; retain lag1 only as the approved causal baseline, without claiming savings."),
        "oracle_excluded": True,
        "negative_control_excluded": True,
    })

    information = []
    for window in cfg["windows"]:
        start = dates.index(window["start"])
        finish = dates.index(window["end"])
        for i in range(start, finish + 1):
            changed = actual_prices.copy()
            changed[i:] = changed[i:] * 1.37 + 0.123
            for method in cfg["methods"]:
                original = price_forecast(method, actual_prices, i, fixed, cfg)
                altered = price_forecast(method, changed, i, fixed, cfg)
                delta = float(np.max(np.abs(original - altered)))
                expected = method == "oracle"
                passed = delta > 0 if expected else delta == 0
                if not passed:
                    raise AssertionError((window["name"], method, dates[i], delta))
                information.append({"window": window["name"], "date": dates[i],
                                    "method": method, "max_change": delta,
                                    "oracle_expected_to_change": expected, "pass": passed})
    dump(output / "information_checks.json", information)
    dump(output / "completion.json", {
        "methods": len(cfg["methods"]), "windows": len(cfg["windows"]),
        "days_per_method": sum((date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1
                               for w in cfg["windows"]),
        "dispatch_rows": len(cfg["methods"]) * len(daily) // len(cfg["methods"]) * 144,
        "selected": selected, "runtime_seconds": time.perf_counter() - started,
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
