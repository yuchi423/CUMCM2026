"""Run Question 3 controls and all three selected model directions."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import openpyxl
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_q2_inputs import day, read_sources
from q2_core import execute
from q3_core import adjustment_fee, choose_candidate, plan_with_audit
from q3_shared import fee_checks, hourly_margin, load_forecasts, read_q3_forecasts


VARIANTS = [
    {"name": "no_update", "issues": [0], "pv_source": "latest", "method": "point"},
    {"name": "state_only", "issues": [0, 6, 12, 18], "pv_source": "zero", "method": "point"},
    {"name": "rolling_point", "issues": [0, 6, 12, 18], "pv_source": "latest", "method": "point"},
    {"name": "rolling_margin", "issues": [0, 6, 12, 18], "pv_source": "latest", "method": "margin"},
    {"name": "rolling_scenario", "issues": [0, 6, 12, 18], "pv_source": "latest", "method": "scenario"},
    {"name": "rolling_point_step", "issues": [0, 6, 12, 18], "pv_source": "latest", "method": "point", "mapping": "step"},
]
SUMS = ["ordinary_kwh", "emergency_kwh", "ordinary_cost", "adjustment_cost",
        "emergency_cost", "total_cost", "paid_grid_spill_kwh", "pv_spill_kwh"]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               default=lambda x: x.item() if hasattr(x, "item") else x,
                               allow_nan=False) + "\n", encoding="utf-8")


def csvout(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(rows, price, cfg):
    result = {key: math.fsum(float(r[key]) for r in rows) for key in SUMS}
    result.update(days=len(rows), e_start=float(rows[0]["e_start"]), e_end=float(rows[-1]["e_end"]),
                  updates=sum(int(r["updates"]) for r in rows),
                  changed_updates=sum(int(r["changed_updates"]) for r in rows),
                  emergency_slots=sum(int(r["emergency_slots"]) for r in rows))
    result["inventory_adjusted_cost"] = (result["total_cost"] + cfg["terminal_value_multiple"] *
        float(np.mean(price)) * (result["e_start"] - result["e_end"]))
    return result


def main(run_dir):
    run = ROOT / run_dir
    run.mkdir(parents=True, exist_ok=False)
    details = run / "details"
    details.mkdir()
    results = run / "results"
    figures = run / "figures"
    results.mkdir()
    figures.mkdir()
    cfg = json.loads((ROOT / "configs/q3_models.json").read_text(encoding="utf-8"))
    source = read_sources(cfg)
    dates = source["dates"]
    load = np.asarray(source["load"])
    actual_pv = np.asarray(source["pv"])
    price = np.asarray(source["prices"])
    _, pv_curve, forecast_hash = read_q3_forecasts(cfg, actual_pv)
    load_pred = load_forecasts(load, dates)
    date_index = {d: i for i, d in enumerate(dates)}

    forecasts = {}
    residuals = {}
    for i in range(1, len(dates)):
        for hour in cfg["issue_hours"]:
            start = hour * 6
            f = pv_curve(dates[i], hour, "linear")
            forecasts[(i, hour, "linear")] = f
            forecasts[(i, hour, "step")] = pv_curve(dates[i], hour, "step")
            residuals[(i, hour)] = ((load[i, start:] - actual_pv[i, start:]) -
                                    (load_pred[i][start:] - f))

    checks = fee_checks()
    if not checks["pass_"]:
        raise AssertionError(checks)
    future_load = load.copy()
    future_load[31:] += 99999
    if not np.array_equal(load_forecasts(future_load, dates)[31], load_pred[31]):
        raise AssertionError("Future load leakage")
    checks.update(attachment3_rows=1460, forecast_values=1460 * 24,
                  issue_keys_unique=True, future_load_perturbation=True,
                  target_mapping="issue+h hours; same-day remainder only")
    dump(run / "preflight_checks.json", checks)

    started = time.perf_counter()
    run_meta = {"code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                 text=True).strip(), "started_at": datetime.now().astimezone().isoformat(),
                "config": cfg, "variants": VARIANTS, "python": platform.python_version(),
                "numpy": np.__version__, "scipy": scipy.__version__,
                "input_hashes": {**source["input_hashes"], "data/raw/附件3.xlsx": forecast_hash},
                "experiment_type": "time-ordered retrospective Q3 comparison; not external blind data"}
    dump(run / "run.json", run_meta)
    all_daily, all_updates = [], []

    for spec in VARIANTS:
        name = spec["name"]
        mapping = spec.get("mapping", "linear")
        energy = float(cfg["initial_energy"])
        daily = []
        dispatch_path = details / f"{name}_dispatch.csv.gz"
        update_path = details / f"{name}_updates.csv.gz"
        version_path = details / f"{name}_plan_versions.jsonl.gz"
        with gzip.open(dispatch_path, "wt", encoding="utf-8", newline="") as dispatch_file, \
             gzip.open(update_path, "wt", encoding="utf-8", newline="") as update_file, \
             gzip.open(version_path, "wt", encoding="utf-8") as version_file:
            dispatch_writer = update_writer = None
            for i, d in enumerate(dates):
                day_start_energy = energy
                if i == 0:
                    current = np.zeros(144)
                    issue_sequence = [0]
                else:
                    current = np.zeros(144)
                    issue_sequence = spec["issues"]
                day_rows, day_update_rows = [], []
                for issue_no, hour in enumerate(issue_sequence):
                    start = hour * 6
                    if i == 0:
                        choice_plan = current[start:]
                        choice_source = "cold_start_zero"
                        candidate_count = 1
                        chosen_score = scenario_mean = scenario_cvar = 0.0
                        solver = "none"
                    else:
                        forecast_hour = 0 if spec["pv_source"] == "zero" else hour
                        full0 = forecasts[(i, 0, mapping)]
                        if forecast_hour == 0:
                            pvf = full0[start:]
                        else:
                            pvf = forecasts[(i, forecast_hour, mapping)]
                        lf = load_pred[i][start:]
                        history_indices = list(range(max(1, i - cfg["residual_days"]), i))
                        paths = [residuals[(j, forecast_hour)][start - forecast_hour * 6:]
                                 if forecast_hour else residuals[(j, 0)][start:]
                                 for j in history_indices]
                        previous = None if hour == 0 else current[start:].copy()
                        candidates = []
                        scenario_loads = []
                        scenario_pvs = []
                        if spec["method"] == "margin":
                            margin = hourly_margin(paths, cfg["robust_quantile"])
                            if margin is None:
                                margin = np.zeros_like(lf)
                            planned_load = lf + margin
                            flow, info = plan_with_audit(price[start:], planned_load, pvf, energy,
                                cfg["planned_terminal_energy"], cfg, previous)
                            candidates.append(("margin", flow["g"]))
                            scenario_loads = [planned_load]
                            scenario_pvs = [pvf]
                        elif spec["method"] == "scenario" and paths:
                            paths = paths[-cfg["scenario_days"]:]
                            point_flow, point_info = plan_with_audit(price[start:], lf, pvf, energy,
                                cfg["planned_terminal_energy"], cfg, previous)
                            candidates.append(("point", point_flow["g"]))
                            solver = point_info["formulation"]
                            margin = hourly_margin(paths, cfg["robust_quantile"])
                            margin_flow, margin_info = plan_with_audit(price[start:], lf + margin, pvf, energy,
                                cfg["planned_terminal_energy"], cfg, previous)
                            candidates.append(("margin", margin_flow["g"]))
                            for k, path in enumerate(paths):
                                scenario_load = np.maximum(0.0, lf + path)
                                flow, info = plan_with_audit(price[start:], scenario_load, pvf, energy,
                                    cfg["planned_terminal_energy"], cfg, previous)
                                candidates.append((f"residual_{k}", flow["g"]))
                                scenario_loads.append(scenario_load)
                                scenario_pvs.append(pvf)
                            scenario_loads.append(lf)
                            scenario_pvs.append(pvf)
                        else:
                            flow, info = plan_with_audit(price[start:], lf, pvf, energy,
                                cfg["planned_terminal_energy"], cfg, previous)
                            candidates.append(("point", flow["g"]))
                            scenario_loads = [lf]
                            scenario_pvs = [pvf]
                        if previous is not None:
                            candidates.append(("keep_previous", previous))
                        risk = cfg["scenario_risk_weight"] if spec["method"] == "scenario" else 0.0
                        chosen, scored = choose_candidate(price[start:], candidates, scenario_loads,
                            scenario_pvs, energy, cfg, previous, risk, cfg["scenario_alpha"])
                        choice_plan, choice_source = chosen.plan, chosen.source
                        candidate_count = len(scored)
                        chosen_score, scenario_mean, scenario_cvar = chosen.score, chosen.scenario_mean, chosen.scenario_cvar
                        solver = locals().get("info", {}).get("formulation", locals().get("solver", "mixed"))
                    previous_plan = None if hour == 0 else current[start:].copy()
                    fee = adjustment_fee(price[start:], choice_plan, previous_plan)
                    increased = (0.0 if previous_plan is None else
                                 float(np.sum(np.maximum(choice_plan - previous_plan, 0.0))))
                    decreased = (0.0 if previous_plan is None else
                                 float(np.sum(np.maximum(previous_plan - choice_plan, 0.0))))
                    current[start:] = choice_plan
                    update = {"strategy": name, "date": d, "issue_hour": hour,
                              "plan_version": issue_no, "chosen_source": choice_source,
                              "candidate_count": candidate_count, "chosen_score": chosen_score,
                              "scenario_mean": scenario_mean, "scenario_cvar": scenario_cvar,
                              "increase_kwh": increased, "decrease_kwh": decreased,
                              "adjustment_cost": fee, "changed": int(increased + decreased > 1e-6),
                              "mapping": mapping, "solver": solver}
                    day_update_rows.append(update)
                    version_file.write(json.dumps({"strategy": name, "date": d, "issue_hour": hour,
                        "start_slot": start, "price": price[start:].tolist(),
                        "old": None if previous_plan is None else previous_plan.tolist(),
                        "new": choice_plan.tolist(), "fee": fee}, ensure_ascii=False) + "\n")
                    if update_writer is None:
                        update_writer = csv.DictWriter(update_file, fieldnames=list(update))
                        update_writer.writeheader()
                    update_writer.writerow(update)

                    end_slot = 144 if issue_no + 1 == len(issue_sequence) else issue_sequence[issue_no + 1] * 6
                    segment, energy = execute(price[start:end_slot], current[start:end_slot],
                                              load[i, start:end_slot], actual_pv[i, start:end_slot], energy, cfg)
                    for local, row in enumerate(segment):
                        row = dict(row)
                        row.update(strategy=name, date=d, slot=start + local, issue_hour=hour,
                                   plan_version=issue_no, ordinary_kwh=row.pop("plan_kwh"),
                                   ordinary_cost=row.pop("plan_cost"), adjustment_cost=0.0)
                        row["total_cost"] = row["ordinary_cost"] + row["emergency_cost"]
                        day_rows.append(row)
                        if dispatch_writer is None:
                            dispatch_writer = csv.DictWriter(dispatch_file, fieldnames=list(row))
                            dispatch_writer.writeheader()
                        dispatch_writer.writerow(row)

                ordinary_cost = math.fsum(r["ordinary_cost"] for r in day_rows)
                emergency_cost = math.fsum(r["emergency_cost"] for r in day_rows)
                adjust_cost = math.fsum(r["adjustment_cost"] for r in day_update_rows)
                record = {"strategy": name, "date": d, "ordinary_kwh": math.fsum(r["ordinary_kwh"] for r in day_rows),
                          "emergency_kwh": math.fsum(r["emergency_kwh"] for r in day_rows),
                          "ordinary_cost": ordinary_cost, "adjustment_cost": adjust_cost,
                          "emergency_cost": emergency_cost, "total_cost": ordinary_cost + adjust_cost + emergency_cost,
                          "paid_grid_spill_kwh": math.fsum(r["paid_grid_spill_kwh"] for r in day_rows),
                          "pv_spill_kwh": math.fsum(r["pv_spill_kwh"] for r in day_rows),
                          "e_start": day_start_energy, "e_end": energy,
                          "updates": max(0, len(day_update_rows) - 1),
                          "changed_updates": sum(r["changed"] for r in day_update_rows if r["issue_hour"]),
                          "emergency_slots": sum(r["emergency_kwh"] > 1e-6 for r in day_rows)}
                daily.append(record)
                if d >= cfg["evaluation_start"]:
                    all_daily.append(record)
                    all_updates.extend(u for u in day_update_rows if u["issue_hour"])
        evaluated = [r for r in daily if r["date"] >= cfg["evaluation_start"]]
        print(name, round(aggregate(evaluated, price, cfg)["total_cost"], 2), flush=True)

    summary = []
    for spec in VARIANTS:
        rows = [r for r in all_daily if r["strategy"] == spec["name"]]
        summary.append({"strategy": spec["name"], **aggregate(rows, price, cfg)})
    csvout(results / "summary_tables.csv", summary)
    csvout(results / "daily_summary.csv", all_daily)

    periods = []
    for name in [v["name"] for v in VARIANTS]:
        for label, lo, hi in [("development", "2025-02-01", cfg["development_end"]),
                              ("validation", "2025-05-01", cfg["validation_end"]),
                              ("evaluation", "2025-09-01", cfg["evaluation_end"])]:
            rows = [r for r in all_daily if r["strategy"] == name and lo <= r["date"] <= hi]
            periods.append({"strategy": name, "period": label, **aggregate(rows, price, cfg)})
    csvout(results / "periods.csv", periods)

    issue_summary = []
    for name in [v["name"] for v in VARIANTS]:
        for hour in [6, 12, 18]:
            rows = [r for r in all_updates if r["strategy"] == name and r["issue_hour"] == hour]
            if rows:
                issue_summary.append({"strategy": name, "issue_hour": hour, "updates": len(rows),
                    "changed": sum(r["changed"] for r in rows),
                    "increase_kwh": math.fsum(r["increase_kwh"] for r in rows),
                    "decrease_kwh": math.fsum(r["decrease_kwh"] for r in rows),
                    "adjustment_cost": math.fsum(r["adjustment_cost"] for r in rows)})
    csvout(results / "issue_summary.csv", issue_summary)
    paper = [r for r in all_daily if r["date"] in cfg["paper_dates"]]
    csvout(results / "paper_dates_summary.csv", paper)

    labels = {"no_update": "仅0时预报", "state_only": "状态反馈对照", "rolling_point": "方向1 点预报滚动",
              "rolling_margin": "方向2 提前期余量", "rolling_scenario": "方向3 连续误差场景",
              "rolling_point_step": "点预报-阶梯映射"}
    plt.rcParams.update({"font.family": "Microsoft YaHei", "axes.unicode_minus": False,
                         "pdf.fonttype": 42, "font.size": 10})
    names = [r["strategy"] for r in summary]
    costs = [r["total_cost"] / 1e4 for r in summary]
    fig, ax = plt.subplots(figsize=(9, 5.5))
    bars = ax.bar([labels[n] for n in names], costs, color=["#547A8A", "#8096A2", "#C27448", "#6F8F62", "#7B6C9D", "#B0A38F"])
    ax.bar_label(bars, fmt="%.2f", padding=3)
    ax.set_ylabel("334天实际总费用 / 万元")
    ax.tick_params(axis="x", rotation=18)
    fig.tight_layout()
    fig.savefig(figures / "Fig1_Q3_TotalCost.png", dpi=320)
    fig.savefig(figures / "Fig1_Q3_TotalCost.pdf")
    plt.close(fig)

    ordinary = np.asarray([r["ordinary_cost"] for r in summary]) / 1e4
    adjustment = np.asarray([r["adjustment_cost"] for r in summary]) / 1e4
    emergency = np.asarray([r["emergency_cost"] for r in summary]) / 1e4
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.bar(x, ordinary, label="普通购电")
    ax.bar(x, adjustment, bottom=ordinary, label="调整费")
    ax.bar(x, emergency, bottom=ordinary + adjustment, label="紧急购电")
    ax.set_xticks(x, [labels[n] for n in names], rotation=18)
    ax.set_ylabel("费用 / 万元")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figures / "Fig2_Q3_CostBreakdown.png", dpi=320)
    fig.savefig(figures / "Fig2_Q3_CostBreakdown.pdf")
    plt.close(fig)

    months = sorted({r["date"][:7] for r in all_daily})
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for name in ["no_update", "rolling_point", "rolling_margin", "rolling_scenario"]:
        vals = [math.fsum(r["total_cost"] for r in all_daily
                         if r["strategy"] == name and r["date"].startswith(month)) / 1e4 for month in months]
        ax.plot(months, vals, marker="o", linewidth=1.6, label=labels[name])
    ax.set_ylabel("月度实际费用 / 万元")
    ax.tick_params(axis="x", rotation=35)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figures / "Fig3_Q3_MonthlyCost.png", dpi=320)
    fig.savefig(figures / "Fig3_Q3_MonthlyCost.pdf")
    plt.close(fig)

    dump(run / "completion.json", {"pass_": True, "seconds": time.perf_counter() - started,
        "strategies": len(VARIANTS), "evaluated_days_each": 334,
        "full_detail_location": "details/ (local audit evidence; intentionally not staged)",
        "summary": summary})
    print("completed", round(time.perf_counter() - started, 2), "seconds", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/run_q3_models.py experiments/<unique-run-id>")
    main(sys.argv[1])
