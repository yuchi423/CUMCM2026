"""Portable entry point; pass --output experiments/<unique-id>."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/run_q1_tests.py'), run_name='__main__')
