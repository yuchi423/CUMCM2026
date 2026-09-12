"""Independent physical, cost and version-ledger audit for a Q3 run."""
from __future__ import annotations

import csv
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main(run_arg):
    run = ROOT / run_arg
    cfg = json.loads((run / "run.json").read_text(encoding="utf-8"))["config"]
    summary = {r["strategy"]: r for r in read_csv(run / "results/summary_tables.csv")}
    daily = read_csv(run / "results/daily_summary.csv")
    errors = defaultdict(float)
    counts = defaultdict(int)

    def err(key, value):
        errors[key] = max(errors[key], abs(float(value)))

    for name, expected in summary.items():
        dispatch_path = run / "details" / f"{name}_dispatch.csv.gz"
        update_path = run / "details" / f"{name}_updates.csv.gz"
        version_path = run / "details" / f"{name}_plan_versions.jsonl.gz"
        rows = []
        with gzip.open(dispatch_path, "rt", encoding="utf-8", newline="") as file:
            for r in csv.DictReader(file):
                rows.append(r)
                counts["dispatch_rows"] += 1
                e0, e1 = float(r["e_start"]), float(r["e_end"])
                c, d = float(r["charge_kwh"]), float(r["discharge_kwh"])
                err("state", e1 - e0 - cfg["charge_efficiency"] * c + d / cfg["discharge_efficiency"])
                err("energy_bounds", max(0.0, cfg["energy_min"] - e1, e1 - cfg["energy_max"]))
                err("ordinary_cost", float(r["ordinary_cost"]) - float(r["price"]) * float(r["ordinary_kwh"]))
                err("emergency_cost", float(r["emergency_cost"]) - cfg["emergency_price_multiple"] *
                    float(r["price"]) * float(r["emergency_kwh"]))
                err("row_total", float(r["total_cost"]) - float(r["ordinary_cost"]) - float(r["emergency_cost"]))
                balance = (float(r["ordinary_kwh"]) + float(r["pv_kwh"]) + float(r["discharge_kwh"]) +
                           float(r["emergency_kwh"]) - float(r["load_kwh"]) - float(r["charge_kwh"]) -
                           float(r["pv_spill_kwh"]) - float(r["paid_grid_spill_kwh"]))
                err("physical_balance", balance)
                ordinary, load, pv = float(r["ordinary_kwh"]), float(r["load_kwh"]), float(r["pv_kwh"])
                cap = cfg["power_kw"] * cfg["step_hours"]
                expected_pv_load = min(load, pv)
                expected_grid_load = min(ordinary, load - expected_pv_load)
                shortage = max(0.0, load - expected_pv_load - expected_grid_load)
                if shortage > 0:
                    expected_discharge = min(shortage, cap, max(0.0, e0 - cfg["energy_min"]) * cfg["discharge_efficiency"])
                    expected_emergency = shortage - expected_discharge
                    expected_pv_charge = expected_grid_charge = 0.0
                else:
                    available = min(cap, max(0.0, cfg["energy_max"] - e0) / cfg["charge_efficiency"])
                    expected_pv_charge = min(pv - expected_pv_load, available)
                    expected_grid_charge = min(ordinary - expected_grid_load,
                                               max(0.0, available - expected_pv_charge))
                    expected_discharge = expected_emergency = 0.0
                err("pv_load_priority", float(r["pv_load_kwh"]) - expected_pv_load)
                err("grid_load_priority", float(r["grid_load_kwh"]) - expected_grid_load)
                err("discharge_rule", float(r["discharge_kwh"]) - expected_discharge)
                err("emergency_rule", float(r["emergency_kwh"]) - expected_emergency)
                err("pv_charge_priority", float(r["pv_charge_kwh"]) - expected_pv_charge)
                err("grid_charge_rule", float(r["grid_charge_kwh"]) - expected_grid_charge)
                err("charge_total", float(r["charge_kwh"]) - expected_pv_charge - expected_grid_charge)
                if int(r["slot"]) < int(r["issue_hour"]) * 6:
                    errors["past_plan_modified"] = 1.0
        for a, b in zip(rows, rows[1:]):
            if (a["strategy"], a["date"], int(a["slot"]) + 1) == (b["strategy"], b["date"], int(b["slot"])) or \
               (a["strategy"] == b["strategy"] and int(a["slot"]) == 143 and int(b["slot"]) == 0):
                err("continuity", float(a["e_end"]) - float(b["e_start"]))

        updates = []
        with gzip.open(update_path, "rt", encoding="utf-8", newline="") as file:
            for r in csv.DictReader(file):
                updates.append(r)
                counts["updates"] += 1
                if int(r["issue_hour"]) not in (0, 6, 12, 18):
                    errors["invalid_issue_hour"] = 1.0
        version_fees = []
        with gzip.open(version_path, "rt", encoding="utf-8") as file:
            for line in file:
                v = json.loads(line)
                err("version_start", v["start_slot"] - v["issue_hour"] * 6)
                err("version_length", len(v["new"]) - (144 - v["start_slot"]))
                if v["old"] is not None:
                    err("old_version_length", len(v["old"]) - len(v["new"]))
                if v["old"] is None:
                    fee = 0.0
                else:
                    fee = math.fsum(0.5 * p * abs(n - o) for p, n, o in zip(v["price"], v["new"], v["old"]))
                err("version_fee", fee - v["fee"])
                version_fees.append(fee)
        err("update_version_count", len(updates) - len(version_fees))
        err("update_fee_total", math.fsum(float(r["adjustment_cost"]) for r in updates) - math.fsum(version_fees))

        evaluated_rows = [r for r in rows if r["date"] >= cfg["evaluation_start"]]
        evaluated_updates = [r for r in updates if r["date"] >= cfg["evaluation_start"]]
        ordinary = math.fsum(float(r["ordinary_cost"]) for r in evaluated_rows)
        emergency = math.fsum(float(r["emergency_cost"]) for r in evaluated_rows)
        adjustment = math.fsum(float(r["adjustment_cost"]) for r in evaluated_updates)
        err("summary_ordinary", ordinary - float(expected["ordinary_cost"]))
        err("summary_emergency", emergency - float(expected["emergency_cost"]))
        err("summary_adjustment", adjustment - float(expected["adjustment_cost"]))
        err("summary_total", ordinary + emergency + adjustment - float(expected["total_cost"]))

        by_day = {r["date"]: r for r in daily if r["strategy"] == name}
        for d in by_day:
            dr = [r for r in evaluated_rows if r["date"] == d]
            du = [r for r in evaluated_updates if r["date"] == d]
            value = math.fsum(float(r["ordinary_cost"]) + float(r["emergency_cost"]) for r in dr)
            value += math.fsum(float(r["adjustment_cost"]) for r in du)
            err("daily_total", value - float(by_day[d]["total_cost"]))

    passed = all(v <= 1e-6 for v in errors.values())
    result = {"pass_": passed, "counts": dict(counts), "max_errors": dict(errors),
              "scope": "Independent physical/state/cost checks and full old/new plan version fee reconstruction"}
    (run / "audit_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise AssertionError(result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
