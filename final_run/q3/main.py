"""One-command Question 3 run: models, independent audit, then report."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if len(sys.argv) != 2:
    raise SystemExit("usage: python final_run/q3/main.py experiments/<unique-run-id>")
target = sys.argv[1]
for script in ["scripts/run_q3_models.py", "scripts/audit_q3_models.py", "scripts/report_q3_models.py"]:
    subprocess.run([sys.executable, str(ROOT / script), target], cwd=ROOT, check=True)
