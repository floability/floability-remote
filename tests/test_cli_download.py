import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from floability_remote.cli import build_parser, run_download
from floability_remote.files import FileInventory, RemoteFile


FILE = RemoteFile(
    id="file-id",
    group="workflow",
    path="workflow/result.csv",
    size=9,
    remote_path="/remote/instance/workflow/result.csv",
)
INVENTORY = FileInventory(
    files=(FILE,), instance_found=True, truncated=False, python_path="/usr/bin/python3"
)


class FakeSession:
    instance = None

    def __init__(self, *args, **kwargs):
        self.started = False
        self.closed = False
        FakeSession.instance = self

    def start(self):
        self.started = True

    def close(self):
        self.closed = True


class FakeFileService:
    downloaded = None

    def __init__(self, session):
        self.session = session

    def list_files(self, run_dir):
        return INVENTORY

    def find_file(self, run_dir, selector):
        if selector not in (FILE.id, FILE.path):
            raise AssertionError(selector)
        return FILE

    def download(self, run_dir, selector, destination):
        destination.write_bytes(b"value,42\n")
        FakeFileService.downloaded = (run_dir, selector, destination)
        return FILE


class DownloadCliTests(unittest.TestCase):
    def parse(self, *extra):
        return build_parser().parse_args(
            [
                "download",
                "--target",
                "user@login.example.org",
                "--run-dir",
                "~/.cache/floability-remote/runs/run-id",
                *extra,
            ]
        )

    def setUp(self):
        FakeFileService.downloaded = None

    def test_parser_accepts_noninteractive_download_options(self):
        args = self.parse("--file", "workflow/result.csv", "--output", "./result.csv")
        self.assertEqual(args.command, "download")
        self.assertEqual(args.file, "workflow/result.csv")
        self.assertEqual(args.output, "./result.csv")

    def test_noninteractive_file_download_uses_shared_service(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "saved.csv"
            with (
                mock.patch("floability_remote.cli.SSHSession", FakeSession),
                mock.patch("floability_remote.cli.FileService", FakeFileService),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                status = run_download(
                    self.parse(
                        "--file",
                        "workflow/result.csv",
                        "--output",
                        str(destination),
                    )
                )

            self.assertEqual(status, 0)
            self.assertEqual(destination.read_bytes(), b"value,42\n")
            self.assertEqual(FakeFileService.downloaded[1], FILE.id)
            self.assertTrue(FakeSession.instance.started)
            self.assertTrue(FakeSession.instance.closed)
            self.assertIn("Downloaded workflow/result.csv", stdout.getvalue())

    def test_interactive_selection_downloads_by_displayed_number(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch("floability_remote.cli.SSHSession", FakeSession),
                mock.patch("floability_remote.cli.FileService", FakeFileService),
                mock.patch("floability_remote.cli.sys.stdin.isatty", return_value=True),
                mock.patch("builtins.input", return_value="1"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                status = run_download(self.parse("--output", directory))

            self.assertEqual(status, 0)
            self.assertEqual(
                (Path(directory) / "result.csv").read_bytes(), b"value,42\n"
            )

    def test_list_only_does_not_download(self):
        with (
            mock.patch("floability_remote.cli.SSHSession", FakeSession),
            mock.patch("floability_remote.cli.FileService", FakeFileService),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            status = run_download(self.parse("--list-only"))

        self.assertEqual(status, 0)
        self.assertIsNone(FakeFileService.downloaded)
        self.assertIn("workflow/result.csv", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
