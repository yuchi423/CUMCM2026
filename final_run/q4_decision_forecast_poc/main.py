"""One-click runner for the approved decision-focused Q4 price PoC."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def call(python: Path, script: str, *arguments: str) -> None:
    subprocess.run([str(python), str(ROOT / "scripts" / script), *arguments],
                   cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?",
                        default="experiments/q4-decision-forecast-poc-20260912-01",
                        type=Path)
    parser.add_argument("--io-python", required=True, type=Path)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    temporary = ROOT / "experiments" / (
        f".q4-decision-price-{os.getpid()}-{time.time_ns()}.json")
    try:
        call(args.io_python, "prepare_q4_price_inputs.py", str(temporary))
        call(Path(sys.executable), "run_q4_decision_forecast_poc.py", str(output),
             "--price-input", str(temporary))
    finally:
        temporary.unlink(missing_ok=True)
    call(Path(sys.executable), "audit_q4_decision_forecast_poc.py", str(output))
    call(Path(sys.executable), "report_q4_decision_forecast_poc.py", str(output))
    print(f"Q4 decision-focused price PoC complete: {output}")


if __name__ == "__main__":
    main()
