"""Add forecast-mapping and scenario-choice evidence to an audited Q3 run."""
from __future__ import annotations

import csv
import gzip
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_q2_inputs import read_sources
from run_q3_models import read_q3_forecasts


def csvout(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def metrics(error, actual, predicted):
    daylight = (actual > 1.0) | (predicted > 1.0)
    return {"samples": len(error), "mae_kw": float(np.mean(np.abs(error))),
            "rmse_kw": float(np.sqrt(np.mean(error ** 2))), "bias_kw": float(np.mean(error)),
            "daylight_mae_kw": float(np.mean(np.abs(error[daylight]))) if np.any(daylight) else 0.0,
            "energy_bias_kwh": float(np.sum(error) / 6.0)}


def main(run_arg):
    run = ROOT / run_arg
    cfg = json.loads((run / "run.json").read_text(encoding="utf-8"))["config"]
    source = read_sources(cfg)
    dates = source["dates"]
    pv = np.asarray(source["pv"])
    _, curve, _ = read_q3_forecasts(cfg, pv)
    start_day = dates.index(cfg["evaluation_start"])
    output = []
    for mode in ["linear", "step"]:
        all_error, all_actual, all_predicted = [], [], []
        for hour in cfg["issue_hours"]:
            ee, aa, pp = [], [], []
            start = hour * 6
            for i in range(start_day, len(dates)):
                actual = pv[i, start:] / cfg["step_hours"]
                predicted = curve(dates[i], hour, mode) / cfg["step_hours"]
                ee.append(predicted - actual)
                aa.append(actual)
                pp.append(predicted)
            error, actual, predicted = map(np.concatenate, (ee, aa, pp))
            output.append({"mapping": mode, "issue_hour": hour, **metrics(error, actual, predicted)})
            all_error.append(error); all_actual.append(actual); all_predicted.append(predicted)
        output.append({"mapping": mode, "issue_hour": "all", **metrics(
            np.concatenate(all_error), np.concatenate(all_actual), np.concatenate(all_predicted))})
    csvout(run / "results/forecast_mapping.csv", output)

    counts = Counter()
    path = run / "details/rolling_scenario_updates.csv.gz"
    with gzip.open(path, "rt", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            if row["date"] >= cfg["evaluation_start"]:
                counts[(int(row["issue_hour"]), row["chosen_source"])] += 1
    choice_rows = [{"issue_hour": hour, "chosen_source": source, "count": count}
                   for (hour, source), count in sorted(counts.items())]
    csvout(run / "results/scenario_choices.csv", choice_rows)
    print(json.dumps({"mapping": [r for r in output if r["issue_hour"] == "all"],
                      "scenario_choice_rows": len(choice_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
