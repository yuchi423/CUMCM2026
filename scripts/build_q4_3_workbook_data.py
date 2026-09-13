"""Build a values-only payload for the official result4-3 workbook template."""
from __future__ import annotations

import csv
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = "rolling_scenario_mean14"


def main(run_arg: str) -> None:
    run = (ROOT / run_arg).resolve()
    audit = json.loads((run / "audit_summary.json").read_text(encoding="utf-8"))
    if not audit["pass"]:
        raise AssertionError("Numerical audit must pass before workbook preparation")
    cfg = json.loads((run / "run.json").read_text(encoding="utf-8"))["config"]
    first = cfg["evaluation_start"]
    dispatch = defaultdict(list)
    with gzip.open(run / "details" / f"{STRATEGY}_dispatch.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["date"] >= first:
                dispatch[row["date"]].append(row)
    initial = {}
    with gzip.open(run / "details" / f"{STRATEGY}_plan_versions.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item["date"] >= first and item["issue_hour"] == 0:
                initial[item["date"]] = item
    adjustment = defaultdict(float)
    with gzip.open(run / "details" / f"{STRATEGY}_updates.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["date"] >= first:
                adjustment[row["date"]] += float(row["adjustment_cost"])

    plan_rows, adjusted_rows, storage_rows, emergency_rows = [], [], [], []
    for date in sorted(dispatch):
        day = sorted(dispatch[date], key=lambda row: int(row["slot"]))
        if len(day) != 144 or date not in initial:
            raise AssertionError((date, len(day), date in initial))
        initial_plan = [float(value) for value in initial[date]["new"]]
        final_plan = [float(row["ordinary_kwh"]) for row in day]
        prices = [float(row["price"]) for row in day]
        initial_cost = math.fsum(p * g for p, g in zip(prices, initial_plan))
        ordinary_cost = math.fsum(float(row["ordinary_cost"]) for row in day)
        plan_rows.append([date, *initial_plan, math.fsum(initial_plan), initial_cost])
        adjusted_rows.append([date, *final_plan, math.fsum(final_plan), ordinary_cost + adjustment[date]])
        for block in range(6):
            part = day[block * 24:(block + 1) * 24]
            storage_rows.append([
                date if block == 0 else None,
                f"{block * 4:02}:00-{(block + 1) * 4:02}:00",
                math.fsum(float(row["charge_kwh"]) for row in part),
                math.fsum(float(row["discharge_kwh"]) for row in part),
                "0:00" if block == 0 else "24:00" if block == 1 else None,
                float(day[0]["e_start"]) if block == 0 else float(day[-1]["e_end"]) if block == 1 else None,
            ])
        events = []
        for slot, row in enumerate(day):
            amount = float(row["emergency_kwh"])
            if amount > 1e-6:
                start_minute, end_minute = slot * 10, (slot + 1) * 10
                label = f"{start_minute // 60:02}:{start_minute % 60:02}-{end_minute // 60:02}:{end_minute % 60:02}"
                events.append([date, label, amount])
        emergency_rows.extend(events or [[date, "全天无紧急购电", 0.0]])

    payload = {
        "template": "data/templates/result4-3.xlsx",
        "output": "output/result4-3.xlsx",
        "sheets": {
            "计划购电量": plan_rows,
            "调整购电量": adjusted_rows,
            "充放电量": storage_rows,
            "紧急购电量": emergency_rows,
        },
        "semantics": {
            "计划购电量": "0:00 initial plan; cost uses actual delivery prices",
            "调整购电量": "last effective plan for each slot; cost is final ordinary cost plus cumulative adjustment fee",
            "充放电量": "actual AC-side charge/discharge; internal energy at 0:00 and 24:00",
            "紧急购电量": "all positive emergency slots; one explicit zero row on days without emergency",
            "time_header_mapping": "official headers retained exactly; B:EO store model slots 0:143 in order"
        },
        "counts": {"days": len(plan_rows), "storage_rows": len(storage_rows),
                   "emergency_rows": len(emergency_rows)}
    }
    if payload["counts"]["days"] != 334 or payload["counts"]["storage_rows"] != 334 * 6:
        raise AssertionError(payload["counts"])
    (run / "workbook_data.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
