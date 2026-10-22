import subprocess
import tempfile
import unittest
from pathlib import Path

from floability_remote.application.planning import PlanningError, PlanningStore
from floability_remote.domain.config import BackpackSource, ConnectionConfig, RunConfig


class PlanningStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backpack = self.root / "backpack"
        (self.backpack / "workflow").mkdir(parents=True)
        (self.backpack / "workflow" / "main.py").write_text("print('ok')\n")
        self.store = PlanningStore(str(self.root / "plans"))

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_local_snapshot_lists_reads_and_detects_changes(self):
        source = self.store.inspect_source(
            BackpackSource(source_type="folder", local_path=str(self.backpack))
        )
        self.assertEqual(source.entrypoints, ("main.py",))
        self.assertEqual(
            [item.path for item in self.store.files(source.id)],
            ["workflow/main.py"],
        )
        self.assertEqual(
            self.store.read_file(source.id, "workflow/main.py"), "print('ok')\n"
        )

        (self.backpack / "workflow" / "main.py").write_text("print('new')\n")
        self.assertFalse(self.store.source_is_fresh(source.id))
        with self.assertRaisesRegex(PlanningError, "changed"):
            self.store.files(source.id)

    def test_git_snapshot_is_pinned_to_commit(self):
        repository = self.root / "origin"
        (repository / "workflow").mkdir(parents=True)
        (repository / "workflow" / "job.py").write_text("print('git')\n")
        subprocess.run(
            ["git", "init", str(repository)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.email", "test@example.org"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", "initial"],
            check=True,
            capture_output=True,
        )

        source = self.store.inspect_source(
            BackpackSource(source_type="git", repository=str(repository))
        )
        self.assertTrue(source.commit)
        self.assertEqual(source.effective.ref, source.commit)
        self.assertEqual(source.entrypoints, ("job.py",))

    def test_plan_is_bound_to_source_and_connection(self):
        source = self.store.inspect_source(
            BackpackSource(source_type="folder", local_path=str(self.backpack))
        )
        config = RunConfig(
            mode="execute",
            connection=ConnectionConfig("user@login.example.org"),
            backpack=source.effective,
            batch_type="local",
            entrypoint="main.py",
        )
        plan = self.store.create_plan(source.id, config, 42.0)
        self.assertEqual(
            self.store.require_fresh_plan(plan.id, "user@login.example.org", 42.0),
            plan,
        )
        with self.assertRaisesRegex(PlanningError, "connection changed"):
            self.store.require_fresh_plan(plan.id, "other.example.org", 42.0)


if __name__ == "__main__":
    unittest.main()
