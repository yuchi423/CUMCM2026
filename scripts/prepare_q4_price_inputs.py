"""Validate Attachment 4 and export a solver-neutral JSON price snapshot."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
from datetime import date, datetime, time
from pathlib import Path

import openpyxl


ROOT = Path(__file__).resolve().parents[1]


def date_label(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def minute_value(value) -> int:
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    text = str(value).strip()
    if text == "0:00+1":
        return 1440
    hour, minute = text.split(":")[:2]
    return int(hour) * 60 + int(minute)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    relative = "data/raw/附件4.xlsx"
    source = ROOT / relative
    with (ROOT / "data/MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
        manifest = {row["path_or_uri"]: row["sha256"] for row in csv.DictReader(stream)}
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != manifest[relative]:
        raise ValueError("附件4 hash mismatch")
    book = openpyxl.load_workbook(source, read_only=True, data_only=True)
    sheet = book["Sheet1"]
    rows = iter(sheet.iter_rows(values_only=True))
    header = next(rows)
    if len(header) != 145 or [minute_value(value) for value in header[1:]] != list(range(10, 1441, 10)):
        raise ValueError("附件4 time axis mismatch")
    dates, prices = [], []
    for raw in rows:
        label = date_label(raw[0])
        curve = raw[1:145]
        if len(curve) != 144 or not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0 for value in curve
        ):
            raise ValueError(f"Invalid price row {label}")
        dates.append(label)
        prices.append([float(value) for value in curve])
    book.close()
    if len(dates) != 365 or len(set(dates)) != 365:
        raise ValueError("附件4 must contain 365 unique dates")
    flat = [value for row in prices for value in row]
    result = {
        "source": relative,
        "source_sha256": digest,
        "python": platform.python_version(),
        "openpyxl": openpyxl.__version__,
        "dates": dates,
        "headers": [str(value) for value in header[1:]],
        "prices": prices,
        "audit": {
            "days": len(dates),
            "slots_per_day": 144,
            "missing": 0,
            "minimum": min(flat),
            "maximum": max(flat),
            "mean": sum(flat) / len(flat),
            "unique_day_curves": len({tuple(row) for row in prices}),
        },
    }
    args.output.resolve().write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
