"""Build the complete Q3 workbook payload from the audited rolling-margin ledger."""
from __future__ import annotations

import csv
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
RUN = ROOT / sys.argv[2]
TMP = ROOT / "tmp/q3-workbook"
STRATEGY = "rolling_margin"
PAPER_DATES = ["2025-03-20", "2025-06-21", "2025-09-23", "2025-12-21"]


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def interval(slot):
    def label(value):
        return f"{value // 6:02}:{value % 6 * 10:02}"
    return f"{label(slot)}-{label(slot + 1)}"


audit = json.loads((RUN / "audit_summary.json").read_text(encoding="utf-8"))
assert audit["pass_"]
daily = {r["date"]: r for r in read_csv(RUN / "results/daily_summary.csv")
         if r["strategy"] == STRATEGY}
dates = sorted(daily)
assert len(dates) == 334 and dates[0] == "2025-02-01" and dates[-1] == "2025-12-31"

dispatch = defaultdict(list)
with gzip.open(RUN / f"details/{STRATEGY}_dispatch.csv.gz", "rt", encoding="utf-8", newline="") as file:
    for row in csv.DictReader(file):
        if row["date"] in daily:
            dispatch[row["date"]].append(row)
for date in dates:
    dispatch[date].sort(key=lambda r: int(r["slot"]))
    assert [int(r["slot"]) for r in dispatch[date]] == list(range(144))

versions = {}
with gzip.open(RUN / f"details/{STRATEGY}_plan_versions.jsonl.gz", "rt", encoding="utf-8") as file:
    for line in file:
        row = json.loads(line)
        if row["date"] in daily:
            versions[(row["date"], row["issue_hour"])] = row
assert len(versions) == 334 * 4

headers = [interval(i) for i in range(144)]
plan_rows, adjusted_rows, storage_rows, emergency_rows, fee_rows = [], [], [], [], []
paper1, paper2, paper3 = [], [], []
max_chain_error = max_fee_error = max_execution_error = 0.0

for date in dates:
    rows = dispatch[date]
    prior = None
    adjustment_fee = 0.0
    for hour in [0, 6, 12, 18]:
        version = versions[(date, hour)]
        assert version["start_slot"] == hour * 6 and len(version["new"]) == 144 - hour * 6
        if prior is None:
            assert version["old"] is None
        else:
            assert len(version["old"]) == len(version["new"])
            expected_old = prior["new"][36:]
            max_chain_error = max(max_chain_error,
                                  max(abs(a - b) for a, b in zip(version["old"], expected_old)))
        fee = 0.0 if version["old"] is None else math.fsum(
            0.5 * p * abs(new - old)
            for p, new, old in zip(version["price"], version["new"], version["old"]))
        max_fee_error = max(max_fee_error, abs(fee - version["fee"]))
        adjustment_fee += version["fee"]
        prior = version
    initial = versions[(date, 0)]["new"]
    prices = versions[(date, 0)]["price"]
    final = [float(r["ordinary_kwh"]) for r in rows]
    for row in rows:
        version = versions[(date, int(row["issue_hour"]))]
        expected = version["new"][int(row["slot"]) - version["start_slot"]]
        max_execution_error = max(max_execution_error, abs(float(row["ordinary_kwh"]) - expected))
    initial_cost = math.fsum(p * q for p, q in zip(prices, initial))
    ordinary_cost = math.fsum(float(r["ordinary_cost"]) for r in rows)
    emergency_cost = math.fsum(float(r["emergency_cost"]) for r in rows)
    total_cost = ordinary_cost + adjustment_fee + emergency_cost
    expected = daily[date]
    for value, key in [(ordinary_cost, "ordinary_cost"), (adjustment_fee, "adjustment_cost"),
                       (emergency_cost, "emergency_cost"), (total_cost, "total_cost")]:
        assert abs(value - float(expected[key])) < 1e-6, (date, key)
    plan_rows.append([date] + initial + [math.fsum(initial), initial_cost])
    adjusted_rows.append([date] + final + [math.fsum(final), ordinary_cost + adjustment_fee])
    fee_rows.append([date, initial_cost, ordinary_cost, adjustment_fee, emergency_cost, total_cost,
                     math.fsum(float(r["emergency_kwh"]) for r in rows),
                     math.fsum(float(r["paid_grid_spill_kwh"]) for r in rows),
                     math.fsum(float(r["pv_spill_kwh"]) for r in rows),
                     float(rows[0]["e_start"]), float(rows[-1]["e_end"]),
                     int(expected["updates"]), int(expected["changed_updates"])])
    day_storage = []
    for block in range(6):
        part = rows[block * 24:(block + 1) * 24]
        record = [date, f"{block * 4:02}:00-{(block + 1) * 4:02}:00",
                  math.fsum(float(r["charge_kwh"]) for r in part),
                  math.fsum(float(r["discharge_kwh"]) for r in part),
                  "00:00" if block == 0 else "24:00" if block == 1 else None,
                  float(rows[0]["e_start"]) if block == 0 else
                  float(rows[-1]["e_end"]) if block == 1 else None]
        storage_rows.append(record)
        day_storage.append(record)
    events = [[date, interval(int(r["slot"])), float(r["emergency_kwh"])]
              for r in rows if float(r["emergency_kwh"]) > 1e-6]
    if not events:
        events = [[date, "全天无紧急购电", 0.0]]
    emergency_rows.extend(events)
    if date in PAPER_DATES:
        slots = [60, 72, 84, 96, 108, 120]
        record = [date]
        for slot in slots:
            record += [initial[slot], final[slot]]
        record += [math.fsum(initial), initial_cost, math.fsum(final), ordinary_cost,
                   adjustment_fee, emergency_cost, total_cost]
        paper1.append(record)
        paper2.extend(day_storage)
        paper3.extend(events)

assert max_chain_error < 1e-6 and max_fee_error < 1e-6 and max_execution_error < 1e-6
actual_total = math.fsum(row[5] for row in fee_rows)
assert abs(actual_total - 13852172.808932787) < 1e-6
slot_headers = []
for slot in [60, 72, 84, 96, 108, 120]:
    slot_headers += [headers[slot] + " 原计划（kWh）", headers[slot] + " 调整后（kWh）"]

sheets = {
    "计划购电量": {"headers": ["日期 / 时段（kWh）"] + headers +
                  ["全天计划购电量（kWh）", "0时计划购电费（元）"], "rows": plan_rows},
    "调整购电量": {"headers": ["日期 / 时段（kWh）"] + headers +
                  ["调整后全天购电量（kWh）", "普通购电及调整费（元）"], "rows": adjusted_rows},
    "充放电量": {"headers": ["日期", "时间段", "充电量（kWh）", "放电量（kWh）", "时刻", "储电量（kWh）"],
                  "rows": storage_rows},
    "紧急购电量": {"headers": ["日期", "紧急购电时间段", "购电量（kWh）"], "rows": emergency_rows},
    "费用汇总": {"headers": ["日期", "0时计划购电费（元）", "最终普通购电费（元）", "调整费（元）",
                  "紧急购电费（元）", "实际总费用（元）", "紧急电量（kWh）", "已付未用网电（kWh）",
                  "弃光（kWh）", "日初电量（kWh）", "日末电量（kWh）", "可更新次数", "实际修改次数"],
                  "rows": fee_rows},
    "指定日表1": {"headers": ["日期"] + slot_headers + ["0时计划总量（kWh）", "0时计划费（元）",
                  "调整后总量（kWh）", "最终普通购电费（元）", "调整费（元）", "紧急购电费（元）", "实际总费用（元）"],
                  "rows": paper1},
    "指定日表2": {"headers": ["日期", "时间段", "充电量（kWh）", "放电量（kWh）", "时刻", "储电量（kWh）"],
                  "rows": paper2},
    "指定日表3": {"headers": ["日期", "紧急购电时间段", "购电量（kWh）"], "rows": paper3},
}
TMP.mkdir(parents=True, exist_ok=True)
(TMP / "workbook_data.json").write_text(json.dumps(sheets, ensure_ascii=False, separators=(",", ":")) + "\n",
                                         encoding="utf-8")
(TMP / "payload_audit.json").write_text(json.dumps({
    "pass_": True, "strategy": STRATEGY, "days": len(dates), "dispatch_rows": sum(map(len, dispatch.values())),
    "plan_versions": len(versions), "annual_total_cost": actual_total,
    "max_plan_chain_error": max_chain_error, "max_fee_error": max_fee_error,
    "max_execution_plan_error": max_execution_error,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"sheets": len(sheets), "days": len(dates), "total": actual_total,
                  "emergency_rows": len(emergency_rows)}, ensure_ascii=False))
