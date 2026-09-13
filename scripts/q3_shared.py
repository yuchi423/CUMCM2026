"""Shared Question 3 forecast readers and deterministic helper functions."""
from __future__ import annotations

import csv
import hashlib
from datetime import date, datetime
from pathlib import Path

import numpy as np
import openpyxl

from q3_core import adjustment_fee

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_q3_forecasts(cfg, actual_pv):
    manifest = {}
    with (ROOT / "data/MANIFEST.csv").open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            manifest[row["path_or_uri"]] = row["sha256"]
    rel = "data/raw/附件3.xlsx"
    if sha(ROOT / rel) != manifest[rel]:
        raise AssertionError("Attachment 3 hash mismatch")
    book = openpyxl.load_workbook(ROOT / rel, read_only=True, data_only=True)
    rows = book.active.iter_rows(min_row=2, values_only=True)
    raw = {}
    current = None
    for row in rows:
        if row[0] not in (None, ""):
            value = str(row[0]).split()[0].replace("/", "-")
            current = datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        hour = int(str(row[1]).split(":")[0])
        values = np.asarray(row[2:26], dtype=float)
        if current is None or (current, hour) in raw or len(values) != 24 or not np.all(np.isfinite(values)):
            raise AssertionError((current, hour))
        raw[(current, hour)] = values
    book.close()
    if len(raw) != 365 * 4 or {h for _, h in raw} != {0, 6, 12, 18}:
        raise AssertionError("Unexpected Attachment 3 issue grid")

    dates = sorted({d for d, _ in raw})
    date_index = {d: i for i, d in enumerate(dates)}

    def curve(d, hour, mode="linear"):
        i = date_index[d]
        start = hour * 6
        anchor = (actual_pv[i, start - 1] / cfg["step_hours"] if start else
                  (actual_pv[i - 1, -1] / cfg["step_hours"] if i else 0.0))
        points = np.r_[anchor, raw[(d, hour)]]
        endpoint_hours = np.arange(1, 145 - start) / 6.0
        if mode == "linear":
            power = np.interp(endpoint_hours, np.arange(25), points)
        elif mode == "step":
            power = points[np.ceil(endpoint_hours).astype(int)]
        else:
            raise ValueError(mode)
        return np.maximum(0.0, power) * cfg["step_hours"]

    return raw, curve, sha(ROOT / rel)


def load_forecasts(load, dates):
    result = [None]
    for i in range(1, len(dates)):
        weekday = date.fromisoformat(dates[i]).weekday()
        indices = [j for j in range(max(0, i - 35), i)
                   if date.fromisoformat(dates[j]).weekday() == weekday]
        if len(indices) < 2:
            indices = list(range(max(0, i - 7), i))
        result.append(load[indices].mean(axis=0))
    return result


def hourly_margin(paths, q):
    if not paths:
        return None
    values = np.asarray(paths)
    n = values.shape[1]
    out = np.zeros(n)
    for start in range(0, n, 6):
        out[start:start + 6] = max(0.0, float(np.quantile(
            values[:, start:start + 6], q, method="linear")))
    return out


def fee_checks():
    p = np.asarray([1.0])
    cases = {
        "100_to_80": 80 + adjustment_fee(p, np.asarray([80.]), np.asarray([100.])),
        "100_to_80_to_90": 90 + adjustment_fee(p, np.asarray([80.]), np.asarray([100.])) +
                            adjustment_fee(p, np.asarray([90.]), np.asarray([80.])),
        "100_to_120_to_100": 100 + adjustment_fee(p, np.asarray([120.]), np.asarray([100.])) +
                              adjustment_fee(p, np.asarray([100.]), np.asarray([120.])),
    }
    expected = {"100_to_80": 90.0, "100_to_80_to_90": 105.0, "100_to_120_to_100": 120.0}
    return {"pass_": all(abs(cases[k] - expected[k]) < 1e-9 for k in cases),
            "calculated": cases, "expected": expected}
