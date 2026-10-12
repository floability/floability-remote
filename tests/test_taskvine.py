import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from floability_remote import remote_scripts
from floability_remote.cli import build_parser, print_taskvine_status
from floability_remote.errors import RemoteRunError
from floability_remote.events import Event, EventKind
from floability_remote.runs import Run
from floability_remote.taskvine import TaskVineService, parse_status

from taskvine_sample import make_run
from test_runs import config


class LocalScriptSession:
    """Runs remote scripts locally with bash, like the cluster would."""

    def __init__(self):
        self.calls = []

    def run_script(self, script, arguments=(), **kwargs):
        self.calls.append(tuple(arguments))
        completed = subprocess.run(
            ["bash", "-s", "--", *arguments], input=script, text=True,
            capture_output=True, timeout=60,
        )
        if completed.returncode != 0:
            raise RemoteRunError(completed.stderr)
        return SimpleNamespace(stdout=completed.stdout, returncode=0)


class TaskVineStatusScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def status(self, **sample):
        run_dir = make_run(self.root, **sample)
        return TaskVineService(LocalScriptSession()).status(str(run_dir))

    def test_counts_manager_workers_and_tasks_from_the_transactions_log(self):
        status = self.status()
        self.assertTrue(status.instance_found)
        self.assertTrue(status.log_found)
        self.assertEqual(status.manager_state, "running")
        self.assertEqual(status.manager_started_at, 1791800100.0)
        # worker-aa idled out; only worker-bb is still connected.
        self.assertEqual((status.workers_connected, status.workers_seen), (1, 2))
        self.assertEqual(
            (status.tasks_waiting, status.tasks_running, status.tasks_done, status.tasks_failed),
            (1, 1, 1, 1),
        )

    def test_uses_the_factorys_latest_status_line(self):
        factory = self.status().factory
        self.assertEqual(
            (factory.at, factory.submitted, factory.waiting_connection, factory.requested),
            ("2026/10/12 10:15:30", 3, 1, 4),
        )

    def test_recent_events_are_readable_and_newest_last(self):
        texts = [event.text for event in self.status().recent]
        self.assertEqual(len(texts), 8)
        self.assertEqual(texts[-1], "Worker 10.1.2.3:40001 disconnected (idle out)")
        self.assertIn("Task 2 failed (signal)", texts)
        self.assertIn("Task 3 started on 10.1.2.4:40002", texts)

    def test_before_the_manager_starts(self):
        status = self.status(transactions=False)
        self.assertTrue(status.instance_found)
        self.assertFalse(status.log_found)
        self.assertEqual(status.manager_state, "not_started")
        self.assertEqual(status.factory.submitted, 3)

    def test_empty_log_does_not_count_as_taskvine(self):
        run_dir = make_run(self.root)
        log = next(self.root.glob("instance/workflow/vine-run-info/*/vine-logs/transactions"))
        log.write_text("# header only\n")
        status = TaskVineService(LocalScriptSession()).status(str(run_dir))
        self.assertTrue(status.log_found)
        self.assertFalse(status.uses_taskvine)

    def test_before_floability_creates_its_instance(self):
        status = self.status(instance=False)
        self.assertFalse(status.instance_found)
        self.assertFalse(status.log_found)
        self.assertIsNone(status.factory)

    def test_rejects_a_path_that_is_not_a_run_directory(self):
        with self.assertRaises(RemoteRunError):
            TaskVineService(LocalScriptSession()).status(str(self.root))

    def test_manager_end_is_reported(self):
        run_dir = make_run(self.root)
        log = next(self.root.glob("instance/workflow/vine-run-info/*/vine-logs/transactions"))
        log.write_text(log.read_text() + "1791800200000000 4242 MANAGER 4242 END 100\n")
        status = TaskVineService(LocalScriptSession()).status(str(run_dir))
        self.assertEqual(status.manager_state, "ended")
        self.assertEqual(status.manager_ended_at, 1791800200.0)
        # Workers leave with the manager even without logged disconnections.
        self.assertEqual((status.workers_connected, status.workers_seen), (0, 2))


class ParseStatusTests(unittest.TestCase):
    def test_rejects_invalid_counts(self):
        with self.assertRaises(ValueError):
            parse_status({"instance_found": True, "log_found": True, "tasks": {"done": -1}})

    def test_rejects_unknown_manager_state(self):
        with self.assertRaises(ValueError):
            parse_status({"manager": {"state": "exploded"}})


class RunDirectoryTrackingTests(unittest.TestCase):
    def test_run_directory_is_known_once_the_backpack_is_ready(self):
        run = Run(config())
        self.assertIsNone(run.remote_run_dir)
        run.emit(Event(EventKind.DETAIL, "Backpack ready", data={"backpack_dir": "/r/2026-x/backpack"}))
        self.assertEqual(run.remote_run_dir, "/r/2026-x")


class TaskVineCliTests(unittest.TestCase):
    def test_non_taskvine_run_is_explained(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = make_run(Path(directory), transactions=False)
            status = TaskVineService(LocalScriptSession()).status(str(run_dir))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_taskvine_status(status)
        self.assertIn("No TaskVine manager has started", output.getvalue())
        self.assertNotIn("Workers", output.getvalue())

    def test_parser(self):
        args = build_parser().parse_args(["taskvine", "--target", "h", "--run-dir", "/r"])
        self.assertEqual((args.command, args.run_dir), ("taskvine", "/r"))

    def test_printed_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = make_run(Path(directory))
            status = TaskVineService(LocalScriptSession()).status(str(run_dir))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_taskvine_status(status)
        text = output.getvalue()
        self.assertIn("Manager   running since", text)
        self.assertIn("Workers   1 connected · 3 submitted to the batch system, 1 waiting to connect", text)
        self.assertIn("Tasks     1 waiting · 1 running · 1 done · 1 failed", text)
        self.assertIn("Recent", text)


if __name__ == "__main__":
    unittest.main()
