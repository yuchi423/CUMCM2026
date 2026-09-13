"""Recompute Q4-3 aggregate CVaR columns from immutable daily ledgers."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from q3_core import empirical_cvar


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def cvar(rows: list[dict[str, str]], alpha: float) -> float:
    return empirical_cvar([float(row["total_cost"]) for row in rows], alpha)


def refresh_full(run: Path, alpha: float) -> dict:
    results = run / "results"
    daily = read_csv(results / "daily_summary.csv")

    def selected(label: str) -> list[dict[str, str]]:
        if label == "all":
            return daily
        if label in {"development", "validation", "evaluation"}:
            return [row for row in daily if row["period"] == label]
        if label.startswith("2025-"):
            return [row for row in daily if row["date"].startswith(label)]
        if label.startswith("Q"):
            quarter = int(label[1:])
            return [row for row in daily
                    if (int(row["date"][5:7]) - 1) // 3 + 1 == quarter]
        raise ValueError(f"Unknown full-run aggregate label: {label}")

    changes = {}
    for name in ["summary_tables.csv", "periods.csv", "monthly_summary.csv",
                 "quarterly_summary.csv"]:
        path = results / name
        rows = read_csv(path)
        for row in rows:
            label = row["period"]
            old = float(row["daily_cvar90"])
            new = cvar(selected(label), alpha)
            row["daily_cvar90"] = repr(new)
            changes[f"{name}:{label}"] = {"old": old, "new": new}
        write_csv(path, rows)
    return changes


def gate(candidate: dict[str, str], baseline: dict[str, str], cfg: dict) -> dict:
    inventory = ((float(baseline["inventory_adjusted_cost"])
                  - float(candidate["inventory_adjusted_cost"]))
                 / float(baseline["inventory_adjusted_cost"]))
    risk = ((float(baseline["daily_cvar90"]) - float(candidate["daily_cvar90"]))
            / float(baseline["daily_cvar90"]))
    cash = float(candidate["total_cost"]) / float(baseline["total_cost"]) - 1.0
    passed = (inventory >= cfg["practical_improvement_fraction"] or
              (risk >= cfg["cvar_improvement_fraction"] and
               cash <= cfg["cash_worsening_tolerance_fraction"]))
    return {"inventory_cost_improvement_fraction": inventory,
            "cvar90_improvement_fraction": risk,
            "cash_worsening_fraction": cash, "pass": bool(passed)}


def refresh_poc(run: Path, alpha: float) -> dict:
    results = run / "results"
    daily = read_csv(results / "daily_summary.csv")
    changes = {}
    specs = [
        ("window_summary.csv", lambda row: [item for item in daily
         if item["strategy"] == row["strategy"] and item["window"] == row["window"]]),
        ("periods.csv", lambda row: [item for item in daily
         if item["strategy"] == row["strategy"] and item["period"] == row["period"]]),
        ("summary_tables.csv", lambda row: [item for item in daily
         if item["strategy"] == row["strategy"]]),
    ]
    for name, selector in specs:
        path = results / name
        rows = read_csv(path)
        for row in rows:
            old = float(row["daily_cvar90"])
            new = cvar(selector(row), alpha)
            row["daily_cvar90"] = repr(new)
            key = row["strategy"] + ":" + row.get("window", row.get("period", "all"))
            changes[f"{name}:{key}"] = {"old": old, "new": new}
        write_csv(path, rows)

    cfg = json.loads((ROOT / "configs/q4_3_poc.json").read_text(encoding="utf-8"))
    periods = read_csv(results / "periods.csv")
    lookup = {(row["strategy"], row["period"]): row for row in periods}
    baseline, candidate = "rolling_margin_mean14", "rolling_scenario_mean14"
    shuffled, updated = "rolling_scenario_shuffled", "rolling_scenario_price_update"
    a_checks, b_checks, negative = {}, {}, {}
    for period in ["validation", "evaluation"]:
        a_checks[period] = gate(lookup[(candidate, period)], lookup[(baseline, period)], cfg)
        b_checks[period] = gate(lookup[(updated, period)], lookup[(candidate, period)], cfg)
        c = lookup[(candidate, period)]
        s = lookup[(shuffled, period)]
        negative[period] = {
            "candidate_inventory_cost": float(c["inventory_adjusted_cost"]),
            "shuffled_inventory_cost": float(s["inventory_adjusted_cost"]),
            "candidate_cvar90": float(c["daily_cvar90"]),
            "shuffled_cvar90": float(s["daily_cvar90"]),
            "pass": (float(c["inventory_adjusted_cost"]) < float(s["inventory_adjusted_cost"])
                     or float(c["daily_cvar90"]) < float(s["daily_cvar90"]))}
    a_pass = all(row["pass"] for row in a_checks.values()) and all(
        row["pass"] for row in negative.values())
    selection_path = run / "selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection.update(candidate_a_period_checks=a_checks,
                     candidate_a_negative_control_checks=negative,
                     candidate_b_period_checks=b_checks,
                     candidate_a_scientific_target_pass=a_pass,
                     candidate_b_scientific_target_pass=False,
                     selected_for_human_model_gate=baseline if not a_pass else candidate)
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["full", "poc"])
    parser.add_argument("run_dir")
    parser.add_argument("--alpha", type=float, default=0.9)
    args = parser.parse_args()
    run = (ROOT / args.run_dir).resolve()
    if not run.is_relative_to((ROOT / "experiments").resolve()):
        raise ValueError("Run directory must be under experiments")
    changes = refresh_full(run, args.alpha) if args.kind == "full" else refresh_poc(
        run, args.alpha)
    correction = {"pass": True, "kind": args.kind, "alpha": args.alpha,
                  "method": "equal-weight empirical CVaR with fractional boundary mass",
                  "changes": changes}
    (run / "cvar_correction.json").write_text(
        json.dumps(correction, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run": str(run), "changed_aggregates": len(changes)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
