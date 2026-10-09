import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from floability_remote import remote_scripts
from floability_remote.errors import RemoteRunError
from floability_remote.files import FileService, MAX_DOWNLOAD_BYTES


class LocalScriptSession:
    """Execute the remote scripts locally against temporary directories."""

    def run_script(self, script, arguments=(), **kwargs):
        result = subprocess.run(
            ["bash", "-s", "--", *arguments],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode:
            raise RemoteRunError(result.stdout.strip())
        return result

    def run_script_to_file(self, script, arguments, destination):
        try:
            with destination.open("xb") as output:
                result = subprocess.run(
                    ["bash", "-s", "--", *arguments],
                    input=script,
                    text=True,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    check=False,
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if result.returncode:
            destination.unlink(missing_ok=True)
            raise RemoteRunError(result.stderr.strip())


class FileServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_dir = self.root / "remote run"
        self.instance = self.root / "instance"
        self.run_dir.mkdir()
        self.instance.mkdir()
        (self.run_dir / ".floability-remote-run").write_text("run-id\n")
        (self.run_dir / "execute-command.log").write_text(
            "starting\n"
            f"[floability] Created instance structure at: {self.instance}\n"
            f"  Python executable: {sys.executable}\n"
            "finished\n"
        )

        workflow = self.instance / "workflow"
        workflow.mkdir()
        (workflow / "result.csv").write_bytes(b"answer,42\n")
        (workflow / "nested").mkdir()
        (workflow / "nested" / "plot.png").write_bytes(b"png")
        (workflow / "vine-run-info" / "run" / "staging").mkdir(parents=True)
        (workflow / "vine-run-info" / "run" / "staging" / "arguments").write_text(
            "internal"
        )
        (workflow / ".ipynb_checkpoints").mkdir()
        (workflow / ".ipynb_checkpoints" / "workflow-checkpoint.ipynb").write_text(
            "checkpoint"
        )
        (workflow / "cached-input.csv").symlink_to(self.root / "cache.csv")
        (self.root / "linked-directory").mkdir()
        (self.root / "linked-directory" / "hidden.txt").write_text("hidden")
        (workflow / "linked").symlink_to(self.root / "linked-directory")

        logs = self.instance / "logs"
        logs.mkdir()
        (logs / "workflow.log").write_text("workflow output\n")
        (logs / "vine_factory.stdout").write_text("worker output\n")
        (logs / "vine_factory_scratch").mkdir()
        (logs / "vine_factory_scratch" / "vine_worker").write_text("binary")

        metadata = self.instance / "metadata"
        metadata.mkdir()
        (metadata / "run.json").write_text("{}\n")
        metrics = self.instance / "metrics"
        metrics.mkdir()
        (metrics / "summary.json").write_text("{}\n")
        (self.instance / "catalog_update.json").write_text("{}\n")
        pyuser = self.instance / "pyuser"
        pyuser.mkdir()
        (pyuser / "package.py").write_text("environment")

        self.service = FileService(LocalScriptSession())

    def test_inventory_includes_approved_files_and_excludes_generated_infrastructure(
        self,
    ):
        inventory = self.service.list_files(str(self.run_dir))
        paths = [item.path for item in inventory.files]

        self.assertTrue(inventory.instance_found)
        self.assertFalse(inventory.truncated)
        self.assertEqual(inventory.python_path, sys.executable)
        self.assertEqual(
            paths,
            [
                "execute-command.log",
                "logs/vine_factory.stdout",
                "logs/workflow.log",
                "catalog_update.json",
                "metadata/run.json",
                "metrics/summary.json",
                "workflow/nested/plot.png",
                "workflow/result.csv",
            ],
        )
        self.assertNotIn("workflow/cached-input.csv", paths)
        self.assertFalse(any("vine_factory_scratch" in path for path in paths))
        self.assertFalse(any("vine-run-info" in path for path in paths))
        self.assertFalse(any(".ipynb_checkpoints" in path for path in paths))
        self.assertFalse(any("pyuser" in path for path in paths))
        self.assertFalse(any("hidden.txt" in path for path in paths))

    def test_download_reinventories_and_copies_exact_binary_content(self):
        destination = self.root / "downloaded.csv"
        item = self.service.download(
            str(self.run_dir), "workflow/result.csv", destination
        )

        self.assertEqual(item.path, "workflow/result.csv")
        self.assertEqual(destination.read_bytes(), b"answer,42\n")
        self.assertFalse(any(self.root.glob("*.part")))

    def test_existing_local_file_is_not_overwritten(self):
        destination = self.root / "downloaded.csv"
        destination.write_text("keep")
        with self.assertRaisesRegex(RemoteRunError, "already exists"):
            self.service.download(str(self.run_dir), "workflow/result.csv", destination)
        self.assertEqual(destination.read_text(), "keep")

    def test_unknown_and_symlink_files_cannot_be_downloaded(self):
        with self.assertRaisesRegex(RemoteRunError, "not found"):
            self.service.find_file(str(self.run_dir), "workflow/cached-input.csv")

    def test_only_command_log_is_available_when_instance_was_not_created(self):
        (self.run_dir / "execute-command.log").write_text("failed early\n")
        inventory = self.service.list_files(str(self.run_dir))
        self.assertFalse(inventory.instance_found)
        self.assertEqual(
            [item.path for item in inventory.files], ["execute-command.log"]
        )

    def test_oversized_file_is_listed_but_not_downloadable(self):
        result = self.instance / "workflow" / "large.bin"
        with result.open("wb") as stream:
            stream.truncate(MAX_DOWNLOAD_BYTES + 1)
        item = next(
            item
            for item in self.service.list_files(str(self.run_dir)).files
            if item.path == "workflow/large.bin"
        )
        self.assertFalse(item.downloadable)
        with self.assertRaisesRegex(RemoteRunError, "download limit"):
            self.service.download(str(self.run_dir), item.id, self.root / "large.bin")

    def test_non_run_directory_is_rejected(self):
        with self.assertRaisesRegex(RemoteRunError, "not a Floability Remote"):
            self.service.list_files(str(self.instance))


class DownloadScriptTests(unittest.TestCase):
    def test_download_script_rejects_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            link = root / "link"
            destination = root / "destination"
            target.write_text("secret")
            link.symlink_to(target)

            with self.assertRaises(RemoteRunError):
                LocalScriptSession().run_script_to_file(
                    remote_scripts.DOWNLOAD_FILE,
                    (str(link), str(MAX_DOWNLOAD_BYTES), sys.executable),
                    destination,
                )
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
