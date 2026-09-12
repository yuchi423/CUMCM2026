"""Run the approved Q4 Detailed PoC inspired by the supplied reference pages."""
from __future__ import annotations

import argparse
import csv
import gzip
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
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
from scipy.stats import spearmanr


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from q2_core import margin, plan, step
from run_q4_price_poc import read_price_matrix


SUMS = [
    "plan_kwh", "emergency_kwh", "plan_cost", "emergency_cost", "total_cost",
    "paid_grid_spill_kwh", "pv_spill_kwh", "charge_kwh", "discharge_kwh",
    "preserved_emergency_kwh",
]

SPECS = {
    "fixed_greedy": {"price": "fixed", "controller": "greedy"},
    "mean7_greedy": {"price": "mean7", "controller": "greedy"},
    "netload_shape_greedy": {"price": "netload_shape", "controller": "greedy"},
    "mean7_value_update": {"price": "mean7", "controller": "value"},
    "netload_shape_value_update": {"price": "netload_shape", "controller": "value"},
    "mean7_value_shift6h": {"price": "mean7", "controller": "value", "shift": True},
    "oracle_greedy": {"price": "oracle", "controller": "greedy"},
    "oracle_value_update": {"price": "oracle", "controller": "value"},
}


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


def supply_forecast(
    load: np.ndarray, pv: np.ndarray, dates: list[str], index: int,
    cutoff: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Forecast a target day using only completed days before cutoff."""
    stop = index if cutoff is None else min(index, cutoff)
    if stop <= 0:
        raise ValueError("Supply forecast requires completed history")
    weekday = date.fromisoformat(dates[index]).weekday()
    chosen = [j for j in range(max(0, stop - 35), stop)
              if date.fromisoformat(dates[j]).weekday() == weekday]
    if len(chosen) < 2:
        chosen = list(range(max(0, stop - 7), stop))
    return load[chosen].mean(axis=0), pv[max(0, stop - 7):stop].mean(axis=0)


class PriceModels:
    def __init__(self, prices: np.ndarray, fixed: np.ndarray, load: np.ndarray,
                 pv: np.ndarray, dates: list[str], config: dict):
        self.prices = prices
        self.fixed = fixed
        self.load = load
        self.pv = pv
        self.dates = dates
        self.config = config
        self.supply_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self.base_cache: dict[tuple[str, int], tuple[np.ndarray, dict]] = {}
        self.rho_cache: dict[tuple[str, int, bool], float] = {}

    def supply(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        if index not in self.supply_cache:
            self.supply_cache[index] = supply_forecast(
                self.load, self.pv, self.dates, index)
        return self.supply_cache[index]

    def base(self, method: str, index: int) -> tuple[np.ndarray, dict]:
        key = (method, index)
        if key in self.base_cache:
            return self.base_cache[key]
        if index <= 0:
            raise ValueError("Price forecast requires completed history")
        if method == "fixed":
            predicted = self.fixed.copy()
            info = {"level": float(np.mean(predicted)), "intercept": None, "netload_coef": None}
        elif method == "oracle":
            predicted = self.prices[index].copy()
            info = {"level": float(np.mean(predicted)), "intercept": None, "netload_coef": None}
        elif method == "mean7":
            predicted = self.prices[max(0, index - 7):index].mean(axis=0)
            info = {"level": float(np.mean(predicted)), "intercept": None, "netload_coef": None}
        elif method == "netload_shape":
            start = max(1, index - self.config["price_history_days"])
            history = list(range(start, index))
            if len(history) < 7:
                raise ValueError("Net-load price regression requires at least seven reconstructed days")
            levels = np.asarray([float(np.mean(self.prices[j])) for j in history])
            predicted_net = []
            shapes = []
            for j, level in zip(history, levels):
                load_f, pv_f = self.supply(j)
                predicted_net.append(float(np.sum(load_f - pv_f)) / 100000.0)
                shapes.append(self.prices[j] / level)
            design = np.c_[np.ones(len(history)), np.asarray(predicted_net)]
            beta, *_ = np.linalg.lstsq(design, levels, rcond=None)
            load_f, pv_f = self.supply(index)
            net = float(np.sum(load_f - pv_f)) / 100000.0
            level = max(self.config["price_floor"], float(beta[0] + beta[1] * net))
            shape = np.mean(np.asarray(shapes), axis=0)
            predicted = level * shape
            info = {"level": level, "intercept": float(beta[0]),
                    "netload_coef": float(beta[1]), "netload_scaled": net,
                    "history_days": len(history)}
        else:
            raise KeyError(method)
        predicted = np.maximum(self.config["price_floor"], predicted)
        self.base_cache[key] = (predicted, info)
        return self.base_cache[key]

    def rho(self, method: str, index: int, shift: bool = False) -> float:
        key = (method, index, shift)
        if key in self.rho_cache:
            return self.rho_cache[key]
        residuals = []
        for j in range(max(1, index - self.config["price_history_days"]), index):
            forecast, _ = self.base(method, j)
            if shift:
                forecast = np.roll(forecast, self.config["negative_control_shift_slots"])
            residuals.append(self.prices[j] - forecast)
        values = np.asarray(residuals)
        previous = values[:, :-1].ravel()
        following = values[:, 1:].ravel()
        denominator = float(previous @ previous)
        raw = 0.0 if denominator <= 1e-15 else float(previous @ following / denominator)
        result = float(np.clip(raw, self.config["price_error_rho_min"],
                               self.config["price_error_rho_max"]))
        self.rho_cache[key] = result
        return result

    def updated(self, method: str, index: int, start: int, shift: bool = False) -> tuple[np.ndarray, dict]:
        base, base_info = self.base(method, index)
        if shift:
            base = np.roll(base, self.config["negative_control_shift_slots"])
        if method == "oracle" or start == 0:
            return base.copy(), {**base_info, "rho": None, "observed_error": None}
        rho = self.rho(method, index, shift)
        last = start - 1
        error = float(self.prices[index, last] - base[last])
        result = base.copy()
        powers = rho ** np.arange(1, 145 - start)
        result[start:] = np.maximum(
            self.config["price_floor"], base[start:] + powers * error)
        return result, {**base_info, "rho": rho, "observed_error": error}


def price_metrics(predicted: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    error = predicted - actual
    rank = float(spearmanr(predicted, actual).statistic)
    if not math.isfinite(rank):
        rank = 0.0
    return {"mae": float(np.mean(np.abs(error))), "mse": float(np.mean(error ** 2)),
            "rank_correlation": rank}


def reserve_plan(price: np.ndarray, load: np.ndarray, pv: np.ndarray, ordinary: np.ndarray,
                 initial: float, terminal_value: float, config: dict) -> tuple[np.ndarray, dict]:
    """Allocate fixed-plan surplus and battery energy over the remaining horizon."""
    n = len(price)
    deficit = np.maximum(load - pv - ordinary, 0.0)
    surplus = np.maximum(ordinary + pv - load, 0.0)
    charge = np.arange(0, n)
    discharge = np.arange(n, 2 * n)
    energy = np.arange(2 * n, 3 * n)
    cap = config["power_kw"] * config["step_hours"]
    lower = np.zeros(3 * n)
    upper = np.full(3 * n, np.inf)
    upper[charge] = np.minimum(cap, surplus)
    upper[discharge] = np.minimum(cap, deficit)
    lower[energy] = config["energy_min"]
    upper[energy] = config["energy_max"]
    objective = np.zeros(3 * n)
    objective[discharge] = -config["emergency_price_multiple"] * price
    objective[energy] = -1e-9
    objective[energy[-1]] -= terminal_value
    rows, columns, values = [], [], []
    rhs = []
    eta_c, eta_d = config["charge_efficiency"], config["discharge_efficiency"]
    for t in range(n):
        row = len(rhs)
        rows.extend([row, row, row])
        columns.extend([int(energy[t]), int(charge[t]), int(discharge[t])])
        values.extend([1.0, -eta_c, 1.0 / eta_d])
        if t:
            rows.append(row); columns.append(int(energy[t - 1])); values.append(-1.0)
            rhs.append(0.0)
        else:
            rhs.append(float(initial))
    matrix = coo_matrix((values, (rows, columns)), shape=(n, 3 * n)).tocsc()
    begin = time.perf_counter()
    result = milp(objective, bounds=Bounds(lower, upper),
                  constraints=LinearConstraint(matrix, np.asarray(rhs), np.asarray(rhs)),
                  options={"time_limit": 30})
    if result.status != 0:
        raise RuntimeError(f"Reserve solver status {result.status}: {result.message}")
    c = result.x[charge]
    d = result.x[discharge]
    e = result.x[energy]
    previous = np.r_[initial, e[:-1]]
    audit = {
        "state": float(np.max(np.abs(e - previous - eta_c * c + d / eta_d))),
        "bounds": float(max(0.0, config["energy_min"] - float(np.min(e)),
                            float(np.max(e)) - config["energy_max"])),
        "charge_limit": float(max(0.0, float(np.max(c - np.minimum(cap, surplus))))),
        "discharge_limit": float(max(0.0, float(np.max(d - np.minimum(cap, deficit))))),
    }
    audit["pass"] = all(value <= 1e-6 for value in audit.values())
    if not audit["pass"]:
        raise AssertionError(audit)
    return e, {"seconds": time.perf_counter() - begin, "audit": audit,
               "terminal_value": terminal_value, "forecast_deficit_kwh": float(np.sum(deficit)),
               "forecast_discharge_kwh": float(np.sum(d))}


def execute_slot(price: float, ordinary: float, load: float, pv: float, energy: float,
                 reserve_end: float | None, config: dict) -> dict:
    base = step(ordinary, load, pv, energy, config)
    if reserve_end is None or base["discharge_kwh"] <= 1e-9:
        row = base
        preserved = 0.0
    else:
        floor = max(config["energy_min"], min(energy, reserve_end))
        controlled_config = dict(config, energy_min=floor)
        row = step(ordinary, load, pv, energy, controlled_config)
        preserved = max(0.0, base["discharge_kwh"] - row["discharge_kwh"])
    row.update({"price": float(price), "plan_cost": float(price) * row["plan_kwh"],
                "emergency_cost": config["emergency_price_multiple"] * float(price) * row["emergency_kwh"],
                "preserved_emergency_kwh": preserved})
    row["total_cost"] = row["plan_cost"] + row["emergency_cost"]
    return row


def aggregate_windows(rows: list[dict]) -> dict:
    return {
        "windows": len(rows), "days": int(sum(row["days"] for row in rows)),
        **{field: float(math.fsum(row[field] for row in rows)) for field in SUMS},
        "inventory_adjusted_cost": float(math.fsum(row["inventory_adjusted_cost"] for row in rows)),
        "total_window_start_energy": float(math.fsum(row["e_start"] for row in rows)),
        "total_window_end_energy": float(math.fsum(row["e_end"] for row in rows)),
        "emergency_slots": int(sum(row["emergency_slots"] for row in rows)),
        "reserve_solves": int(sum(row["reserve_solves"] for row in rows)),
        "reserve_failures": int(sum(row["reserve_failures"] for row in rows)),
        "max_audit": float(max(row["max_audit"] for row in rows)),
    }


def run(output: Path, price_input: Path) -> None:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    results_dir = output / "results"
    figures_dir = output / "figures"
    results_dir.mkdir(); figures_dir.mkdir()
    config = json.loads((ROOT / "configs/q4_reference_poc.json").read_text(encoding="utf-8"))
    q2_path = ROOT / "experiments/q2-full-20260911-01/inputs.json"
    q2 = json.loads(q2_path.read_text(encoding="utf-8"))
    dates = q2["dates"]
    load = np.asarray(q2["load"], dtype=float)
    pv = np.asarray(q2["pv"], dtype=float)
    fixed = np.asarray(q2["prices"], dtype=float)
    price_dates, actual_price, headers, price_audit = read_price_matrix(price_input)
    if price_dates != dates:
        raise ValueError("Attachment 4 dates do not align")
    shutil.copyfile(price_input, output / "input_prices.json")

    shared_starts = {}
    with (ROOT / "experiments/q2-improve-20260911-01/daily.csv").open(
        encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["strategy"] == "combined":
                shared_starts[row["date"]] = float(row["e_start"])
    for window in config["windows"]:
        if window["start"] not in shared_starts:
            raise ValueError(f"Missing shared state {window['start']}")

    model = PriceModels(actual_price, fixed, load, pv, dates, config)
    dump(output / "run.json", {
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now().astimezone().isoformat(), "config": config,
        "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
        "q2_snapshot_sha256": __import__("hashlib").sha256(q2_path.read_bytes()).hexdigest(),
        "attachment4": price_audit,
        "experiment_type": "approved reference-method Detailed PoC; disjoint 84-day evidence only",
    })
    dump(output / "input_audit.json", {
        "dates_aligned": True, "days": len(dates), "slots": len(headers),
        "shared_window_initial_states": {w["name"]: shared_starts[w["start"]] for w in config["windows"]},
    })

    def supply(index: int) -> tuple[np.ndarray, np.ndarray]:
        return model.supply(index)

    margin_cache = {}
    def forecasts(index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        load_f, pv_f = supply(index)
        if index not in margin_cache:
            residuals = []
            for h in range(max(1, index - config["residual_days"]), index):
                old_load, old_pv = supply(h)
                residuals.append(load[h] - pv[h] - old_load + old_pv)
            margin_cache[index] = margin(residuals, config)
        return load_f, pv_f, margin_cache[index]

    daily_rows, window_rows, price_rows, update_rows, plan_records = [], [], [], [], []
    ledger_path = output / "dispatch.csv.gz"
    ledger = gzip.open(ledger_path, "wt", encoding="utf-8", newline="")
    writer = None
    for method in config["methods"]:
        spec = SPECS[method]
        for window in config["windows"]:
            start_index = dates.index(window["start"])
            end_index = dates.index(window["end"])
            energy = shared_starts[window["start"]]
            window_daily = []
            window_prices = []
            for index in range(start_index, end_index + 1):
                load_f, pv_f, extra = forecasts(index)
                base_price, base_info = model.base(spec["price"], index)
                ordinary_flow, plan_solver = plan(base_price, load_f + extra, pv_f, energy, config)
                ordinary = ordinary_flow["g"].copy()
                metrics = price_metrics(base_price, actual_price[index])
                price_rows.append({"method": method, "window": window["name"],
                                   "period": window["period"], "date": dates[index], **metrics})
                day_start = energy
                day_dispatch = []
                updates = []
                issue_slots = config["issue_slots"] if spec["controller"] == "value" else [0]
                for issue_number, issue in enumerate(issue_slots):
                    next_issue = 144 if issue_number + 1 == len(issue_slots) else issue_slots[issue_number + 1]
                    reserve = None
                    control_price = None
                    update_info = {"rho": None, "observed_error": None}
                    reserve_info = {"seconds": 0.0, "audit": {"pass": True, "state": 0.0,
                                    "bounds": 0.0, "charge_limit": 0.0, "discharge_limit": 0.0},
                                    "terminal_value": 0.0, "forecast_deficit_kwh": 0.0,
                                    "forecast_discharge_kwh": 0.0}
                    if spec["controller"] == "value":
                        control_price, update_info = model.updated(
                            spec["price"], index, issue, bool(spec.get("shift", False)))
                        terminal_value = (config["terminal_value_efficiency_multiple"]
                                          * float(np.mean(control_price[:config["terminal_value_dawn_slots"]])))
                        reserve, reserve_info = reserve_plan(
                            control_price[issue:], (load_f + extra)[issue:], pv_f[issue:],
                            ordinary[issue:], energy, terminal_value, config)
                    update = {
                        "method": method, "window": window["name"], "period": window["period"],
                        "date": dates[index], "issue_slot": issue, "issue_hour": issue / 6,
                        "e_start": float(energy), "ordinary_plan_sum": float(np.sum(ordinary)),
                        "price_rho": update_info.get("rho"),
                        "observed_price_error": update_info.get("observed_error"),
                        "terminal_value": reserve_info["terminal_value"],
                        "forecast_deficit_kwh": reserve_info["forecast_deficit_kwh"],
                        "forecast_discharge_kwh": reserve_info["forecast_discharge_kwh"],
                        "solve_seconds": reserve_info["seconds"],
                        "max_reserve_audit": float(max(
                            value for key, value in reserve_info["audit"].items() if key != "pass")),
                    }
                    update_rows.append(update); updates.append(update)
                    for slot in range(issue, next_issue):
                        reference = None if reserve is None else float(reserve[slot - issue])
                        row = execute_slot(
                            float(actual_price[index, slot]), float(ordinary[slot]),
                            float(load[index, slot]), float(pv[index, slot]), energy, reference, config)
                        row.update({"method": method, "window": window["name"], "period": window["period"],
                                    "date": dates[index], "slot": slot, "issue_slot": issue,
                                    "reserve_end": reference, "price_forecast_0": float(base_price[slot]),
                                    "controller_price": (None if control_price is None else float(control_price[slot]))})
                        day_dispatch.append(row)
                        if writer is None:
                            writer = csv.DictWriter(ledger, fieldnames=list(row))
                            writer.writeheader()
                        writer.writerow(row)
                        energy = row["e_end"]
                record = {
                    "method": method, "window": window["name"], "period": window["period"],
                    "date": dates[index], "e_start": float(day_start), "e_end": float(energy),
                    **{field: float(math.fsum(row[field] for row in day_dispatch)) for field in SUMS},
                    "emergency_slots": int(sum(row["emergency_kwh"] > 1e-6 for row in day_dispatch)),
                    "reserve_solves": len(updates) if spec["controller"] == "value" else 0,
                    "reserve_failures": 0,
                    "max_audit": float(max([float(plan_solver["audit"][key])
                        for key in plan_solver["audit"] if key not in {"pass", "mutual_count"}]
                        + [row["max_reserve_audit"] for row in updates])),
                    "plan_solver": plan_solver["formulation"],
                    "price_level": base_info.get("level"),
                    "price_intercept": base_info.get("intercept"),
                    "price_netload_coef": base_info.get("netload_coef"),
                }
                daily_rows.append(record); window_daily.append(record)
                window_prices.extend(actual_price[index].tolist())
                plan_records.append({
                    "method": method, "window": window["name"], "period": window["period"],
                    "date": dates[index], "history_end": None if spec["price"] == "oracle" else dates[index - 1],
                    "causal": spec["price"] != "oracle", "e_start": float(day_start),
                    "price_method": spec["price"], "controller": spec["controller"],
                    "shift": bool(spec.get("shift", False)), "base_price": base_price.tolist(),
                    "ordinary_plan": ordinary.tolist(), "updates": updates,
                })
            sums = {field: float(math.fsum(row[field] for row in window_daily)) for field in SUMS}
            mean_price = float(np.mean(window_prices))
            adjusted = sums["total_cost"] + config["discharge_efficiency"] * mean_price * (
                window_daily[0]["e_start"] - window_daily[-1]["e_end"])
            window_row = {
                "method": method, "window": window["name"], "period": window["period"],
                "days": len(window_daily), **sums, "e_start": window_daily[0]["e_start"],
                "e_end": window_daily[-1]["e_end"], "mean_actual_price": mean_price,
                "inventory_adjusted_cost": float(adjusted),
                "emergency_slots": int(sum(row["emergency_slots"] for row in window_daily)),
                "reserve_solves": int(sum(row["reserve_solves"] for row in window_daily)),
                "reserve_failures": 0,
                "max_audit": float(max(row["max_audit"] for row in window_daily)),
            }
            window_rows.append(window_row)
            print(method, window["name"], round(sums["total_cost"], 2),
                  "preserved", round(sums["preserved_emergency_kwh"], 1), flush=True)
    ledger.close()

    table(results_dir / "daily_summary.csv", daily_rows)
    table(results_dir / "window_summary.csv", window_rows)
    table(results_dir / "price_metrics_daily.csv", price_rows)
    table(results_dir / "controller_updates.csv", update_rows)
    with gzip.open(output / "plans.jsonl.gz", "wt", encoding="utf-8") as stream:
        for record in plan_records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                                    default=lambda x: x.item() if hasattr(x, "item") else x) + "\n")

    summaries, periods, price_summaries = [], [], []
    for method in config["methods"]:
        method_windows = [row for row in window_rows if row["method"] == method]
        summaries.append({"method": method, **aggregate_windows(method_windows)})
        for period in ["development", "validation", "evaluation"]:
            chosen = [row for row in method_windows if row["period"] == period]
            periods.append({"method": method, "period": period, **aggregate_windows(chosen)})
        rows = [row for row in price_rows if row["method"] == method]
        price_summaries.append({
            "method": method, "days": len(rows),
            "mae": float(np.mean([row["mae"] for row in rows])),
            "rmse": float(math.sqrt(np.mean([row["mse"] for row in rows]))),
            "rank_correlation": float(np.mean([row["rank_correlation"] for row in rows])),
        })
    table(results_dir / "summary_tables.csv", summaries)
    table(results_dir / "periods.csv", periods)
    table(results_dir / "price_forecast_metrics.csv", price_summaries)

    period_map = {(row["method"], row["period"]): row for row in periods}
    development = {name: period_map[(name, "development")]["inventory_adjusted_cost"]
                   for name in config["candidate_order"]}
    raw_best = min(development, key=development.get)
    best = development[raw_best]
    eligible = [name for name in config["candidate_order"]
                if development[name] <= best * (1 + config["selection_tolerance_fraction"])]
    selected = min(eligible, key=config["candidate_order"].index)
    later = {}
    threshold = config["practical_improvement_fraction"]
    for period in ["validation", "evaluation"]:
        selected_cost = period_map[(selected, period)]["inventory_adjusted_cost"]
        baseline_cost = period_map[("mean7_greedy", period)]["inventory_adjusted_cost"]
        fixed_cost = period_map[("fixed_greedy", period)]["inventory_adjusted_cost"]
        negative_cost = period_map[("mean7_value_shift6h", period)]["inventory_adjusted_cost"]
        later[period] = {
            "selected_cost": selected_cost, "mean7_greedy_cost": baseline_cost,
            "fixed_greedy_cost": fixed_cost, "shifted_value_cost": negative_cost,
            "beats_mean7_by_threshold": selected_cost <= baseline_cost * (1 - threshold),
            "beats_fixed_by_threshold": selected_cost <= fixed_cost * (1 - threshold),
            "beats_shifted_control": selected_cost < negative_cost,
        }
    scientific_pass = all(all([
        later[p]["beats_mean7_by_threshold"], later[p]["beats_fixed_by_threshold"],
        later[p]["beats_shifted_control"],
    ]) for p in ["validation", "evaluation"])
    effects = []
    for period in ["development", "validation", "evaluation"]:
        base = period_map[("mean7_greedy", period)]["inventory_adjusted_cost"]
        price_only = period_map[("netload_shape_greedy", period)]["inventory_adjusted_cost"]
        control_only = period_map[("mean7_value_update", period)]["inventory_adjusted_cost"]
        combined = period_map[("netload_shape_value_update", period)]["inventory_adjusted_cost"]
        effects.append({"period": period, "baseline": base,
                        "price_forecast_effect": price_only - base,
                        "storage_control_effect": control_only - base,
                        "combined_effect": combined - base,
                        "interaction": combined - price_only - control_only + base})
    table(results_dir / "effect_decomposition.csv", effects)
    dump(output / "selection.json", {
        "selection_period": "development only", "raw_best": raw_best,
        "development_costs": development, "eligible_within_tolerance": eligible,
        "selected": selected, "later_checks": later,
        "scientific_target_pass": scientific_pass,
        "oracle_excluded": True, "shifted_control_excluded": True,
        "fallback_if_fail": "D-015 lag1 operational baseline, then fixed_attachment1",
    })

    information_checks = []
    poc_indices = [i for window in config["windows"]
                   for i in range(dates.index(window["start"]), dates.index(window["end"]) + 1)]
    for index in poc_indices:
        altered_price = actual_price.copy()
        altered_price[index:] = altered_price[index:] * 1.23 + 0.071
        altered_load = load.copy(); altered_pv = pv.copy()
        altered_load[index:] += 12345.0; altered_pv[index:] *= 0.37
        changed_model = PriceModels(altered_price, fixed, altered_load, altered_pv, dates, config)
        for price_method in ["mean7", "netload_shape", "oracle"]:
            original, _ = model.base(price_method, index)
            changed, _ = changed_model.base(price_method, index)
            delta = float(np.max(np.abs(original - changed)))
            expected_change = price_method == "oracle"
            passed = delta > 0 if expected_change else delta == 0
            information_checks.append({"date": dates[index], "issue_slot": 0,
                                       "price_method": price_method, "max_change": delta,
                                       "expected_change": expected_change, "pass": passed})
        for issue in [36, 72, 108]:
            changed_price = actual_price.copy()
            changed_price[index, issue:] = changed_price[index, issue:] * 1.31 + 0.057
            issue_model = PriceModels(changed_price, fixed, load, pv, dates, config)
            for price_method in ["mean7", "netload_shape", "oracle"]:
                original, _ = model.updated(price_method, index, issue)
                changed, _ = issue_model.updated(price_method, index, issue)
                delta = float(np.max(np.abs(original[issue:] - changed[issue:])))
                expected_change = price_method == "oracle"
                passed = delta > 0 if expected_change else delta == 0
                information_checks.append({"date": dates[index], "issue_slot": issue,
                                           "price_method": price_method, "max_change": delta,
                                           "expected_change": expected_change, "pass": passed})
    if not all(row["pass"] for row in information_checks):
        raise AssertionError("Information boundary check failed")
    dump(output / "information_checks.json", information_checks)
    dump(output / "completion.json", {
        "methods": len(config["methods"]), "days_per_method": len(poc_indices),
        "dispatch_rows": len(config["methods"]) * len(poc_indices) * 144,
        "information_checks": len(information_checks), "selected": selected,
        "scientific_target_pass": scientific_pass,
        "runtime_seconds": time.perf_counter() - started,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--price-input", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "experiments").resolve()):
        raise ValueError("Output must be under experiments/")
    run(output, args.price_input.resolve())


if __name__ == "__main__":
    main()
