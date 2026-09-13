"""Check that the revised Q3/Q4 paper is bound to frozen local results."""
from __future__ import annotations

import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper/微网电力调控_第三四问本地结果修订稿.tex"


def one(path: Path, key: str, value: str) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    found = [row for row in rows if row.get(key) == value]
    if len(found) != 1:
        raise AssertionError((path, key, value, len(found)))
    return found[0]


text = PAPER.read_text(encoding="utf-8")
q3 = one(ROOT / "deliverables/q3/summary_tables.csv", "strategy", "rolling_scenario")
q42 = one(
    ROOT / "experiments/q4-saa-strict-full-20260913-01/results/summary_tables.csv",
    "method",
    "mean7",
)
q43 = one(ROOT / "deliverables/q4-3/summary_tables.csv", "strategy", "rolling_scenario_mean14")

expected = {
    "Q3 total": float(q3["total_cost"]),
    "Q3 emergency": float(q3["emergency_kwh"]),
    "Q4-2 total": float(q42["total_cost"]),
    "Q4-2 emergency": float(q42["emergency_kwh"]),
    "Q4-3 total": float(q43["total_cost"]),
    "Q4-3 emergency": float(q43["emergency_kwh"]),
    "Q4-3 CVaR90": float(q43["daily_cvar90"]),
}
for label, number in expected.items():
    rendered = f"{number:,.2f}"
    if rendered not in text:
        raise AssertionError(f"{label}={rendered} is absent from paper")

banned = [
    "13,448,507.10",
    "14,728,634.65",
    "13,982,459.05",
    "采用因果预测、$k$-medoids场景压缩",
    "每天在48小时窗口",
    "问题三 & 风险压降25\\%",
]
for value in banned:
    if value in text:
        raise AssertionError(f"stale or unsupported claim remains: {value}")

for relative in [
    "../scripts/q3_shared.py",
    "../scripts/q3_core.py",
    "../scripts/run_q3_models.py",
    "../scripts/run_q4_saa_full.py",
    "../scripts/run_q4_3_scenario_full.py",
]:
    if relative not in text or not (PAPER.parent / relative).resolve().exists():
        raise AssertionError(f"missing appendix source: {relative}")

# Lightweight syntax checks for environments, labels and braces outside listings/comments.
syntax = re.sub(
    r"\\begin\{lstlisting\}.*?\\end\{lstlisting\}",
    "",
    text,
    flags=re.S,
)
syntax = "\n".join(re.sub(r"(?<!\\)%.*$", "", line) for line in syntax.splitlines())
stack: list[str] = []
for match in re.finditer(r"\\(begin|end)\{([^}]+)\}", syntax):
    action, environment = match.groups()
    if action == "begin":
        stack.append(environment)
    elif not stack or stack.pop() != environment:
        raise AssertionError(f"unbalanced environment near {match.group(0)}; stack={stack[-5:]}")
if stack:
    raise AssertionError(f"unclosed environments: {stack}")

depth = 0
for index, char in enumerate(syntax):
    if char in "{}" and (index == 0 or syntax[index - 1] != "\\"):
        depth += 1 if char == "{" else -1
        if depth < 0:
            raise AssertionError("closing brace without opening brace")
if depth:
    raise AssertionError(f"unbalanced braces: {depth}")

labels = re.findall(r"\\label\{([^}]+)\}", syntax)
if len(labels) != len(set(labels)):
    raise AssertionError("duplicate LaTeX labels")
if text.count(r"\end{document}") != 1:
    raise AssertionError("document must have exactly one end marker")

print("Q3/Q4 paper evidence check: PASS")
for label, number in expected.items():
    print(f"{label}: {number:.6f}")
