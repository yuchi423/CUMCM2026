"""One-click entry point for the frozen 334-day Task 4 SAA run."""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def call(python: Path, script: str, *args: str) -> None:
    subprocess.run([str(python),str(ROOT/"scripts"/script),*args],cwd=ROOT,check=True)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",nargs="?",default="experiments/q4-saa-strict-full-20260913-01",type=Path)
    parser.add_argument("--io-python",required=True,type=Path)
    parser.add_argument("--model-python",default=r"D:\Users\python.exe",type=Path)
    args=parser.parse_args(); output=args.output if args.output.is_absolute() else ROOT/args.output
    temporary=ROOT/"experiments"/f".q4-saa-full-price-{os.getpid()}-{time.time_ns()}.json"
    try:
        call(args.io_python,"prepare_q4_price_inputs.py",str(temporary))
        call(args.model_python,"run_q4_saa_full.py",str(output),"--price-input",str(temporary))
    finally:
        temporary.unlink(missing_ok=True)
    call(args.model_python,"audit_q4_saa_full.py",str(output))
    call(args.model_python,"report_q4_saa_full.py",str(output))
    print(f"Q4 SAA full run complete: {output}")


if __name__=="__main__": main()
