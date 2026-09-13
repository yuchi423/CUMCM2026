"""One-click entry for the selected Question 4-3 direction-3 model."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def call(parts):
    print("+", " ".join(map(str, parts)), flush=True)
    subprocess.run([str(x) for x in parts], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="new directory under experiments/")
    parser.add_argument("--price-input", type=Path,
        default=ROOT / "experiments/q4-saa-full-20260912-01/input_prices.json")
    parser.add_argument("--node", default="node", help="Node.js executable for workbook export")
    parser.add_argument("--report-python", help="Python executable containing Pillow and ReportLab")
    parser.add_argument("--node-modules", type=Path,
        help="existing @oai/artifact-tool node_modules; a temporary junction is created")
    parser.add_argument("--compute-only", action="store_true")
    args = parser.parse_args()
    py = sys.executable
    report_py = args.report_python or py
    if not args.report_python:
        probe = subprocess.run([report_py, "-c", "import PIL,reportlab,numpy"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode:
            candidate = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
            if candidate.exists(): report_py = str(candidate)
    node = args.node
    if node == "node" and shutil.which(node) is None:
        candidate = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        if candidate.exists(): node = str(candidate)
    node_modules = args.node_modules
    if node_modules is None:
        candidate = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
        if candidate.exists(): node_modules = candidate
    call([py, ROOT / "scripts/run_q4_3_scenario_full.py", args.run_dir,
          "--price-input", args.price_input])
    call([py, ROOT / "scripts/audit_q4_3_scenario_full.py", args.run_dir])
    call([report_py, ROOT / "scripts/report_q4_3_scenario_full.py", args.run_dir])
    call([py, ROOT / "scripts/build_q4_3_workbook_data.py", args.run_dir])
    if not args.compute_only:
        builder = ROOT / "final_run/q4_3_scenario/workbook.mjs"
        if node_modules:
            stage = Path(tempfile.mkdtemp(prefix="cumcm-q4-3-xlsx-"))
            shutil.copyfile(builder, stage / "workbook.mjs")
            builder = stage / "workbook.mjs"
            quoted = lambda value: "'" + str(value).replace("'", "''") + "'"
            subprocess.run(["powershell", "-NoProfile", "-Command",
                "New-Item -ItemType Junction -Path " + quoted(stage / "node_modules") +
                " -Target " + quoted(node_modules.resolve())], check=True)
        call([node, builder, ROOT / args.run_dir])
        call([py, ROOT / "scripts/check_q4_3_workbook.py", args.run_dir])


if __name__ == "__main__":
    main()
