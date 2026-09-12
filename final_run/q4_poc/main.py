"""One-click entry for the approved Task 4 causal-price Cheap PoC."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(script: str, output: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), str(output)], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default="experiments/q4-price-poc-20260912-01", type=Path)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    run("run_q4_price_poc.py", output)
    run("audit_q4_price_poc.py", output)
    run("report_q4_price_poc.py", output)
    print(f"Task 4 PoC complete: {output}")


if __name__ == "__main__":
    main()
