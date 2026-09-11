"""Independent Q2 ledger audit: no imports from the optimizer or executor."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from prepare_q2_inputs import read_sources

ROOT = Path(__file__).resolve().parents[1]


def audit(run):
    metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
    config = metadata["config"]
    source = read_sources(config)  # Reopen original workbooks, independently of the solver snapshot.
    snapshot = json.loads((run / "inputs.json").read_text(encoding="utf-8"))
    assert source == snapshot
    assert hashlib.sha256((run/"inputs.json").read_bytes()).hexdigest() == metadata["snapshot_sha256"]
    with (run / "dispatch.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    with (run / "daily_summary.csv").open(encoding="utf-8-sig", newline="") as file:
        summaries = list(csv.DictReader(file))
    schedules = json.loads((run/"plans.json").read_text(encoding="utf-8"))
    plans = {(r["strategy"],r["date"]):r for r in schedules}
    dates = {d:i for i,d in enumerate(source["dates"])}
    cap = config["power_kw"]*config["step_hours"]
    eta, ed = config["charge_efficiency"], config["discharge_efficiency"]
    last = {}; grouped = {}; seen = set()
    maxima = {k:0. for k in ["balance_kwh","state_kwh","continuity_kwh","bound_kwh","power_kw",
                           "cost_yuan","pv_priority_kwh","source_allocation_kwh","plan_change_kwh","raw_input_kwh",
                           "negative_kwh","emergency_use_kwh"]}
    mutual = 0
    for raw in rows:
        strategy, date = raw["strategy"], raw["date"]
        slot = int(raw["slot"]); key = (strategy,date,slot)
        assert key not in seen; seen.add(key)
        r = {k:float(v) for k,v in raw.items() if k not in ["strategy","date","history_last_date"] and v != ""}
        i = dates[date]; p = source["prices"][slot]
        expected_initial = config["initial_energy"] if strategy == "warmup" else metadata["shared_evaluation_initial_energy"]
        previous = last.get(strategy, expected_initial)
        maxima["continuity_kwh"] = max(maxima["continuity_kwh"], abs(r["e_start"]-previous))
        last[strategy] = r["e_end"]
        actual_l, actual_v = source["load"][i][slot], source["pv"][i][slot]
        maxima["raw_input_kwh"] = max(maxima["raw_input_kwh"], abs(r["load_kwh"]-actual_l), abs(r["pv_kwh"]-actual_v))
        maxima["plan_change_kwh"] = max(maxima["plan_change_kwh"], abs(r["plan_kwh"]-plans[(strategy,date)]["g"][slot]))
        g,b,v,d,l,c,sp,sg = [r[k] for k in ["plan_kwh","emergency_kwh","pv_kwh","discharge_kwh",
                                          "load_kwh","charge_kwh","pv_spill_kwh","paid_grid_spill_kwh"]]
        maxima["balance_kwh"] = max(maxima["balance_kwh"], abs(g+b+v+d-l-c-sp-sg))
        maxima["state_kwh"] = max(maxima["state_kwh"], abs(r["e_end"]-(r["e_start"]+eta*c-d/ed)))
        maxima["bound_kwh"] = max(maxima["bound_kwh"], config["energy_min"]-r["e_end"], r["e_end"]-config["energy_max"])
        maxima["power_kw"] = max(maxima["power_kw"], (max(c,d)-cap)/config["step_hours"])
        maxima["negative_kwh"] = max(maxima["negative_kwh"], -min(g,b,v,d,l,c,sp,sg))
        mutual += c > 1e-6 and d > 1e-6
        expected_pv_load = min(v,l)
        expected_pv_charge = min(max(0.,v-l),cap,max(0.,(config["energy_max"]-r["e_start"])/eta))
        maxima["pv_priority_kwh"] = max(maxima["pv_priority_kwh"],abs(r["pv_load_kwh"]-expected_pv_load),
                                         abs(r["pv_charge_kwh"]-expected_pv_charge))
        source_error = max(abs(c-r["pv_charge_kwh"]-r["grid_charge_kwh"]),
                           abs(v-r["pv_load_kwh"]-r["pv_charge_kwh"]-sp),
                           abs(g-r["grid_load_kwh"]-r["grid_charge_kwh"]-sg))
        maxima["source_allocation_kwh"] = max(maxima["source_allocation_kwh"], source_error)
        maxima["emergency_use_kwh"] = max(maxima["emergency_use_kwh"], c if b>1e-6 else 0,
                                          abs(b-max(0.,l-v-g-d)))
        expected_cost = p*g+config["emergency_price_multiple"]*p*b
        maxima["cost_yuan"] = max(maxima["cost_yuan"],abs(r["price"]-p),abs(r["total_cost"]-expected_cost),
                                  abs(r["plan_cost"]-p*g),abs(r["emergency_cost"]-5*p*b))
        assert raw["history_last_date"] == (source["dates"][i-1] if i else "NONE")
        grouped.setdefault((strategy,date), []).append(r)
    assert all(len(group)==144 and [int(r["slot"]) for r in group]==list(range(144)) for group in grouped.values())
    for summary in summaries:
        group = grouped[(summary["strategy"],summary["date"])]
        for key in ["plan_cost","emergency_cost","total_cost","plan_kwh","emergency_kwh","load_kwh",
                    "pv_spill_kwh","paid_grid_spill_kwh","charge_kwh","discharge_kwh"]:
            assert abs(float(summary[key])-math.fsum(r[key] for r in group))<1e-6, key
        assert abs(float(summary["e_start"])-group[0]["e_start"])<1e-6
        assert abs(float(summary["e_end"])-group[-1]["e_end"])<1e-6
    assert abs(last["warmup"]-metadata["shared_evaluation_initial_energy"])<1e-6
    warm_days = sum(s=="warmup" for s,d in grouped)
    expected_days = metadata["evaluation_days"]
    assert warm_days==31 and all(sum(s==name for s,d in grouped)==expected_days for name in ["baseline","q80_margin"])
    tests = json.loads((run/"small_checks.json").read_text(encoding="utf-8"))
    info_tests = json.loads((run/"information_checks.json").read_text(encoding="utf-8"))
    assert all(r["pass"] for r in tests+info_tests)
    passed = mutual==0 and all(v <= (0.01 if k=="cost_yuan" else 1e-5 if k=="power_kw" else 1e-6) for k,v in maxima.items())
    result = {"pass":passed,"independence":"No optimizer/executor imports; source workbook re-read; ledger equations recomputed",
              "max_errors":maxima, "mutual_violation_count":int(mutual), "audited_rows":len(rows),
              "warmup_days":warm_days, "evaluation_days_per_strategy":expected_days,
              "analytical_checks":len(tests), "information_checks":len(info_tests),
              "full_year_delivery":"PARTIAL", "scientific_validity":"NOT REQUIRED"}
    (run/"independent_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    assert passed, result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",required=True,type=Path)
    args=parser.parse_args(); audit(args.run.resolve())
