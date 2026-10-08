import contextlib
import io
import unittest

from floability_remote.cli import build_parser, validate_args
from floability_remote.errors import RemoteRunError


class ParserTests(unittest.TestCase):
    def parse(self, command="run", *extra):
        return build_parser().parse_args(
            [
                command,
                "--target",
                "user@login.example.org",
                "--backpack",
                "https://github.com/example/backpack.git",
                "--batch-type",
                "slurm",
                *extra,
            ]
        )

    def test_run_arguments(self):
        args = self.parse("run")
        self.assertEqual(args.command, "run")
        self.assertEqual(args.env_name, "floability-remote-managed")
        self.assertEqual(args.jupyter_port, 8888)
        self.assertFalse(args.verbose)

    def test_execute_arguments(self):
        args = self.parse("execute", "--entrypoint", "workflow.py")
        self.assertEqual(args.command, "execute")
        self.assertEqual(args.entrypoint, "workflow.py")
        self.assertIsNone(args.local_port)

    def test_verbose_mode_is_explicit(self):
        self.assertTrue(self.parse("execute", "--verbose").verbose)

    def test_reinstall_miniforge_mode_is_explicit(self):
        self.assertTrue(
            self.parse("execute", "--reinstall-miniforge").reinstall_miniforge
        )

    def test_reinstall_miniforge_rejects_explicit_conda(self):
        args = self.parse(
            "execute",
            "--reinstall-miniforge",
            "--conda-executable",
            "/opt/conda/bin/conda",
        )
        with self.assertRaises(RemoteRunError):
            validate_args(args)

    def test_invalid_port_is_rejected(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                self.parse("run", "--local-port", "70000")

    def test_target_with_whitespace_is_rejected(self):
        args = self.parse("execute")
        args.target = "bad target"
        with self.assertRaises(RemoteRunError):
            validate_args(args)

    def test_parent_entrypoint_is_rejected(self):
        args = self.parse("execute")
        args.entrypoint = "../workflow.py"
        with self.assertRaises(RemoteRunError):
            validate_args(args)

    def test_missing_identity_file_is_rejected(self):
        args = self.parse("execute")
        args.identity_file = "/definitely/missing/key.pem"
        with self.assertRaises(RemoteRunError):
            validate_args(args)


if __name__ == "__main__":
    unittest.main()
