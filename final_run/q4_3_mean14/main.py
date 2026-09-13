"""One-command local entry point for the audited Question 4-3 delivery."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(args: list[str]) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--with-report"):
        raise SystemExit(
            "Usage: python final_run/q4_3_mean14/main.py "
            "experiments/<new-run-id> [--with-report]"
        )
    target = sys.argv[1]
    run(["scripts/run_q4_3_full.py", target])
    run(["scripts/audit_q4_3_full.py", target])
    if len(sys.argv) == 3:
        run(["scripts/report_q4_3_full.py", target])
