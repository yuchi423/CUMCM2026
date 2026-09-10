"""Validate shared task records using only the Python standard library."""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATES = {"todo", "in_progress", "blocked", "handoff", "review", "done", "cancelled"}
REQUIRED = {"id", "title", "status", "owner", "branch", "updated_at", "depends_on",
            "goal", "acceptance", "artifacts", "checks", "decisions",
            "latest_handoff", "next_action", "blockers"}


def validate(root):
    errors = []

    def check_path(value, label):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: expected nonempty relative path")
            return
        path = (root / value).resolve()
        if Path(value).is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append(f"{label}: missing or unsafe file: {value}")

    for value in ["AGENTS.md", "prompts/SYSTEM_PROMPT.md", "memory/PROJECT.md", "memory/STATUS.md"]:
        check_path(value, "required")
    records = {}
    for file in sorted((root / "memory/tasks").glob("*.json")):
        label = file.name
        try:
            task = json.loads(file.read_text(encoding="utf-8-sig"))
            if not isinstance(task, dict):
                raise ValueError("task must be an object")
        except (ValueError, OSError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        missing = REQUIRED - task.keys()
        if missing:
            errors.append(f"{label}: missing fields {sorted(missing)}")
            continue
        if task["id"] != file.stem:
            errors.append(f"{label}: id must match filename")
        records[file.stem] = task
        status = task["status"]
        if not isinstance(status, str) or status not in STATES:
            errors.append(f"{label}: invalid status")
        for key in ["title", "goal", "next_action"]:
            if not isinstance(task[key], str) or not task[key].strip():
                errors.append(f"{label}: {key} must be nonempty text")
        try:
            stamp = datetime.fromisoformat(task["updated_at"])
            if stamp.tzinfo is None:
                raise ValueError("timezone required")
        except (TypeError, ValueError):
            errors.append(f"{label}: updated_at must be ISO date-time with timezone")
        valid_lists = True
        for key in ["depends_on", "acceptance", "artifacts", "checks", "decisions", "blockers"]:
            if not isinstance(task[key], list) or not all(isinstance(x, str) and x.strip() for x in task[key]):
                errors.append(f"{label}: {key} must be a list of nonempty strings")
                valid_lists = False
        if not valid_lists:
            continue
        if not task["acceptance"]:
            errors.append(f"{label}: acceptance criteria required")
        if status in ("in_progress", "handoff", "review", "done"):
            for key in ["owner", "branch"]:
                if not isinstance(task[key], str) or not task[key].strip():
                    errors.append(f"{label}: {key} required for {status}")
        if status == "blocked" and not task["blockers"]:
            errors.append(f"{label}: blocked requires a reason")
        if status == "handoff" and not task["latest_handoff"]:
            errors.append(f"{label}: handoff requires latest_handoff")
        if status in ("review", "done") and (not task["artifacts"] or not task["checks"]):
            errors.append(f"{label}: {status} requires artifacts and checks")
        for key in ["artifacts", "decisions"]:
            for value in task[key]:
                check_path(value, f"{label}/{key}")
        if task["latest_handoff"] is not None:
            check_path(task["latest_handoff"], f"{label}/latest_handoff")
    if not records:
        errors.append("no task records found")
    for task_id, task in records.items():
        dependencies = task.get("depends_on")
        if not isinstance(dependencies, list):
            continue
        for dep in dependencies:
            if not isinstance(dep, str) or dep not in records or dep == task_id:
                errors.append(f"{task_id}: invalid dependency {dep!r}")
    return errors


if __name__ == "__main__":
    failures = validate(ROOT)
    for failure in failures:
        print(f"ERROR: {failure}")
    print(f"Shared state: {'FAIL' if failures else 'PASS'}")
    sys.exit(bool(failures))
