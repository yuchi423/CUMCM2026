"""One-command entry for the approved Question 4-3 Cheap PoC."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if len(sys.argv) != 2:
    raise SystemExit("usage: python final_run/q4_3_poc/main.py experiments/<unique-run-id>")
run = sys.argv[1]
price = ROOT / "experiments/q4-saa-full-20260912-01/input_prices.json"
commands = [
    [sys.executable, str(ROOT / "scripts/run_q4_3_poc.py"), run, "--price-input", str(price)],
    [sys.executable, str(ROOT / "scripts/audit_q4_3_poc.py"), run],
    [sys.executable, str(ROOT / "scripts/report_q4_3_poc.py"), run]
]
for command in commands:
    subprocess.run(command, cwd=ROOT, check=True)
