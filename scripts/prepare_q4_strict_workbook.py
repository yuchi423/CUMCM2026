"""Prepare the selected Q4-2 delivery tables without authoring Excel files."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

PAPER_DATES = ["2025-03-20", "2025-06-21", "2025-09-23", "2025-12-21"]


def main(experiment: Path, output: Path) -> None:
    selection = json.loads((experiment / "selection.json").read_text(encoding="utf-8"))
    method = selection["selected_delivery"]
    grouped = defaultdict(list)
    with gzip.open(experiment / "dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        for raw in csv.DictReader(stream):
            if raw["method"] == method:
                grouped[raw["date"]].append({key: float(value) for key, value in raw.items()
                    if key not in {"method", "date"}})
    if len(grouped) != 334 or any(len(rows) != 144 for rows in grouped.values()):
        raise AssertionError("Incomplete selected-method ledger")
    def time_label(slot):
        return f"{slot//6:02}:{slot%6*10:02}"
    intervals = [f"{time_label(t)}-{time_label(t+1)}" for t in range(144)]
    plans, storage, emergency, fees, paper1, paper2, paper3 = [], [], [], [], [], [], []
    for date in sorted(grouped):
        rows = sorted(grouped[date], key=lambda row: row["slot"])
        plan = [row["plan_kwh"] for row in rows]
        pc = math.fsum(row["plan_cost"] for row in rows)
        bc = math.fsum(row["emergency_cost"] for row in rows)
        plans.append([date, *plan, math.fsum(plan), pc])
        fees.append([date, pc, bc, pc+bc,
            math.fsum(row["emergency_kwh"] for row in rows),
            math.fsum(row["paid_grid_spill_kwh"] for row in rows),
            math.fsum(row["pv_spill_kwh"] for row in rows), rows[0]["e_start"], rows[-1]["e_end"]])
        for block in range(6):
            sub = rows[block*24:(block+1)*24]
            entry = [date, f"{block*4:02}:00-{(block+1)*4:02}:00",
                math.fsum(row["charge_kwh"] for row in sub),
                math.fsum(row["discharge_kwh"] for row in sub),
                "00:00" if block == 0 else "24:00" if block == 1 else None,
                rows[0]["e_start"] if block == 0 else rows[-1]["e_end"] if block == 1 else None]
            storage.append(entry)
            if date in PAPER_DATES: paper2.append(entry)
        events = [[date, intervals[t], row["emergency_kwh"]]
            for t, row in enumerate(rows) if row["emergency_kwh"] > 1e-6]
        if not events: events = [[date, "无", 0.0]]
        emergency.extend(events)
        if date in PAPER_DATES:
            paper1.append([date, *[plan[t] for t in [60,72,84,96,108,120]], math.fsum(plan), pc, bc, pc+bc])
            paper3.extend(events)
    storage_headers = ["日期", "时间段", "充电量（kWh）", "放电量（kWh）", "时刻", "储电量（kWh）"]
    emergency_headers = ["日期", "紧急时间段", "购电量（kWh）"]
    sheets = {
        "计划购电量": {"headers": ["日期 / 时段（kWh）", *intervals, "全天计划购电量（kWh）", "全天计划购电费（元）"], "rows": plans},
        "充放电量": {"headers": storage_headers, "rows": storage},
        "紧急购电量": {"headers": emergency_headers, "rows": emergency},
        "费用汇总": {"headers": ["日期", "计划购电费（元）", "紧急购电费（元）", "总费用（元）", "紧急电量（kWh）", "已付未用网电（kWh）", "弃光（kWh）", "日初电量（kWh）", "日末电量（kWh）"], "rows": fees},
        "指定日表1": {"headers": ["日期", *[intervals[t] for t in [60,72,84,96,108,120]], "全天计划电量（kWh）", "计划电费（元）", "紧急电费（元）", "合计费用（元）"], "rows": paper1},
        "指定日表2": {"headers": storage_headers, "rows": paper2},
        "指定日表3": {"headers": emergency_headers, "rows": paper3},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    source = str(experiment.relative_to(root)) if experiment.is_relative_to(root) else str(experiment)
    output.write_text(json.dumps({"method": method, "source_experiment": source, "sheets": sheets}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    for name in ["指定日表1", "指定日表2", "指定日表3"]:
        with (experiment / "results" / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream); writer.writerow(sheets[name]["headers"]); writer.writerows(sheets[name]["rows"])
    print(f"Prepared {method}: 334 dates, 48096 intervals")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path); parser.add_argument("output", type=Path)
    args = parser.parse_args(); main(args.experiment.resolve(), args.output.resolve())
