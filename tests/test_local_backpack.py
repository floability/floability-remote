import io
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import subprocess

from floability_remote import remote_scripts
from floability_remote.errors import RemoteRunError
from floability_remote.local_backpack import (
    clone,
    discard_changes,
    inspect,
    list_directories,
    pack,
    provenance,
    upload,
)


class LocalBackpackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "example"
        (self.root / "workflow").mkdir(parents=True)
        (self.root / "workflow" / "main.py").write_text("print('ok')\n")

    def tearDown(self):
        self.temporary.cleanup()

    def test_inspection_lists_workflow_files_sorted(self):
        (self.root / "workflow" / "b.ipynb").write_text("{}")
        (self.root / "workflow" / "__pycache__").mkdir()
        (self.root / "workflow" / "__pycache__" / "x.pyc").write_bytes(b"x")
        self.assertEqual(inspect(self.root).workflow_files, ("b.ipynb", "main.py"))

    def test_list_directories_skips_hidden_and_symlinked_folders(self):
        parent = Path(self.temporary.name).resolve()
        (parent / ".cache").mkdir()
        (parent / "plain").mkdir()
        (parent / "link").symlink_to(self.root, target_is_directory=True)
        listing = list_directories(parent)
        self.assertEqual(
            [(entry.name, entry.is_backpack) for entry in listing.directories],
            [("example", True), ("plain", False)],
        )
        self.assertTrue(list_directories(self.root).is_backpack)

    def test_inspects_and_packs_folder_while_skipping_generated_files(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("secret")
        (self.root / "workflow" / "__pycache__").mkdir()
        (self.root / "workflow" / "__pycache__" / "main.pyc").write_bytes(b"x")
        report = inspect(self.root)
        self.assertTrue(report.valid)
        self.assertEqual(report.file_count, 1)
        self.assertTrue(report.workflow_found)
        with pack(self.root) as stream:
            with tarfile.open(fileobj=stream, mode="r") as archive:
                self.assertEqual(archive.getnames(), ["workflow", "workflow/main.py"])

    def test_rejects_folder_symlink(self):
        os.symlink(self.root / "workflow" / "main.py", self.root / "link.py")
        report = inspect(self.root)
        self.assertFalse(report.valid)
        self.assertIn("Symlinks are not allowed", " ".join(report.problems))

    def test_requires_workflow_folder(self):
        (self.root / "workflow" / "main.py").unlink()
        (self.root / "workflow").rmdir()
        (self.root / "README.md").write_text("hello")
        self.assertIn("workflow/", " ".join(inspect(self.root).problems))

    def test_rejects_unsafe_zip_path(self):
        archive = Path(self.temporary.name) / "bad.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("../workflow/main.py", "bad")
        self.assertIn("Unsafe ZIP path", " ".join(inspect(archive).problems))

    def test_strips_single_zip_wrapper_directory(self):
        archive = Path(self.temporary.name) / "wrapped.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("project-main/workflow/main.py", "print('ok')")
            zipped.writestr("project-main/.git/config", "skip")
        report = inspect(archive)
        self.assertTrue(report.valid, report.problems)
        with pack(archive) as stream:
            with tarfile.open(fileobj=stream, mode="r") as packed:
                self.assertEqual(packed.getnames(), ["workflow", "workflow/main.py"])

    def test_strips_wrapper_when_zip_lists_directory_entries(self):
        # Finder, `zip -r`, and GitHub archives include "project/" entries.
        archive = Path(self.temporary.name) / "finder.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("project/", "")
            zipped.writestr("project/workflow/", "")
            zipped.writestr("project/workflow/main.py", "print('ok')")
        report = inspect(archive)
        self.assertTrue(report.valid, report.problems)
        self.assertEqual(report.workflow_files, ("main.py",))
        with pack(archive) as stream:
            with tarfile.open(fileobj=stream, mode="r") as packed:
                self.assertEqual(packed.getnames(), ["workflow", "workflow/main.py"])

    def test_upload_uses_binary_stream_and_remote_run(self):
        calls = []

        class Session:
            def run_stream(self, script, arguments, stream):
                calls.append((script, arguments, stream.read()))
                return SimpleNamespace(returncode=0, stdout="ok")

        stream = io.BytesIO(b"tar bytes")
        result = upload(Session(), stream, "~/runs/run-1")
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(calls[0][1], ("~/runs/run-1",))
        self.assertEqual(calls[0][2], b"tar bytes")

    def test_pack_rechecks_source(self):
        (self.root / "workflow").rename(self.root / "removed")
        with self.assertRaises(RemoteRunError):
            pack(self.root)

    def test_remote_upload_script_unpacks_fresh_backpack(self):
        remote_root = Path(self.temporary.name) / "remote"
        with pack(self.root) as stream:
            result = subprocess.run(
                ["bash", "-c", remote_scripts.UPLOAD_BACKPACK, "--", str(remote_root / "run-1")],
                stdin=stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout.decode())
        self.assertEqual(
            (remote_root / "run-1" / "backpack" / "workflow" / "main.py").read_text(),
            "print('ok')\n",
        )

    def test_file_and_size_limits_are_reported(self):
        with mock.patch("floability_remote.local_backpack.MAX_FILES", 0):
            self.assertIn("more than", " ".join(inspect(self.root).problems))
        with mock.patch("floability_remote.local_backpack.MAX_BYTES", 1):
            self.assertIn("larger than", " ".join(inspect(self.root).problems))


class LocalCloneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "origin"
        (self.repository / "workflow").mkdir(parents=True)
        (self.repository / "workflow" / "main.py").write_text("print('one')\n")
        subprocess.run(["git", "init", str(self.repository)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repository), "config", "user.email", "test@example.org"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repository), "commit", "-m", "initial"], check=True, capture_output=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_clone_records_origin_without_dirtying_clone(self):
        destination = self.root / "copy"
        result = clone(str(self.repository), destination=destination)
        self.assertEqual(result.path, str(destination.resolve()))
        self.assertEqual(result.provenance.original_repository, str(self.repository))
        self.assertTrue(result.provenance.commit)
        self.assertEqual(result.provenance.uncommitted_changes, 0)
        self.assertTrue(inspect(destination).valid)

    def test_status_and_discard_tracked_changes(self):
        destination = self.root / "copy"
        clone(str(self.repository), destination=destination)
        workflow = destination / "workflow" / "main.py"
        workflow.write_text("print('edited')\n")
        self.assertEqual(provenance(destination).uncommitted_changes, 1)
        status = discard_changes(destination)
        self.assertEqual(status.uncommitted_changes, 0)
        self.assertEqual(workflow.read_text(), "print('one')\n")

    def test_existing_destination_is_not_overwritten(self):
        destination = self.root / "existing"
        destination.mkdir()
        with self.assertRaises(RemoteRunError):
            clone(str(self.repository), destination=destination)

    def test_public_github_zip_fallback_when_git_is_unavailable(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("demo-main/workflow/main.py", "print('ok')\n")

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        destination = self.root / "downloaded"
        with mock.patch("floability_remote.local_backpack.shutil.which", return_value=None):
            with mock.patch(
                "floability_remote.local_backpack.urllib.request.urlopen",
                return_value=Response(archive_bytes.getvalue()),
            ):
                result = clone(
                    "https://github.com/example/demo.git",
                    destination=destination,
                )
        self.assertEqual(result.provenance.original_repository, "https://github.com/example/demo.git")
        self.assertEqual((destination / "workflow" / "main.py").read_text(), "print('ok')\n")


if __name__ == "__main__":
    unittest.main()
