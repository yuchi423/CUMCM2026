"""One-click entry for the approved Task 4 causal-price Cheap PoC."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(script: str, output: Path, extra: list[str] | None = None, python: Path | None = None) -> None:
    command = [str(python or Path(sys.executable)), str(ROOT / "scripts" / script), str(output)]
    subprocess.run(command + (extra or []), cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default="experiments/q4-price-poc-20260912-01", type=Path)
    parser.add_argument("--io-python", required=True, type=Path,
                        help="Python with openpyxl, used only to read Attachment 4")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    temporary = ROOT / "experiments" / f".q4-price-input-{os.getpid()}-{time.time_ns()}.json"
    try:
        run("prepare_q4_price_inputs.py", temporary, python=args.io_python)
        run("run_q4_price_poc.py", output, ["--price-input", str(temporary)])
    finally:
        temporary.unlink(missing_ok=True)
    run("audit_q4_price_poc.py", output)
    run("report_q4_price_poc.py", output)
    print(f"Task 4 PoC complete: {output}")


if __name__ == "__main__":
    main()
