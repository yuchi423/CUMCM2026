"""Exercise recovery-critical failures without modifying the real task state."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_state", ROOT / "scripts/check_state.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class StateValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ["AGENTS.md", "prompts/SYSTEM_PROMPT.md", "memory/PROJECT.md", "memory/STATUS.md"]:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture", encoding="utf-8")
        self.path = self.root / "memory/tasks/T-001.json"
        self.path.parent.mkdir(parents=True)
        self.task = {
            "id": "T-001", "title": "Example", "status": "todo",
            "owner": None, "branch": None, "updated_at": "2026-09-10T12:00:00+08:00",
            "depends_on": [], "goal": "Example goal", "acceptance": ["Example check"],
            "artifacts": [], "checks": [], "decisions": [], "latest_handoff": None,
            "next_action": "Run example", "blockers": [],
        }

    def result(self):
        self.path.write_text(json.dumps(self.task), encoding="utf-8")
        return checker.validate(self.root)

    def test_initial_state(self):
        self.assertEqual(self.result(), [])

    def test_handoff_requires_record(self):
        self.task.update(status="handoff", owner="alice", branch="codex/T-001-alice")
        self.assertTrue(any("latest_handoff" in x for x in self.result()))

    def test_completed_requires_evidence(self):
        self.task.update(status="done", owner="alice", branch="codex/T-001-alice")
        self.assertTrue(any("artifacts and checks" in x for x in self.result()))

    def test_missing_file_and_traversal(self):
        self.task["artifacts"] = ["missing.csv", "../outside.csv"]
        errors = self.result()
        self.assertTrue(any("missing.csv" in x for x in errors))
        self.assertTrue(any("outside.csv" in x for x in errors))

    def test_broken_dependency(self):
        self.task["depends_on"] = ["T-does-not-exist"]
        self.assertTrue(any("invalid dependency" in x for x in self.result()))

    def test_malformed_types_do_not_crash(self):
        self.task.update(status=[], artifacts={})
        self.assertTrue(self.result())


if __name__ == "__main__":
    unittest.main()
