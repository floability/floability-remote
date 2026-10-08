import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from floability_remote.config import (
    BackpackSource,
    ConfigError,
    ConnectionConfig,
    EnvironmentConfig,
    RunConfig,
    run_config_issues,
    validate_run_config,
)


def config(**overrides):
    base = RunConfig(
        mode="execute",
        connection=ConnectionConfig("user@login.example.org"),
        backpack=BackpackSource("https://github.com/example/backpack.git"),
        batch_type="slurm",
    )
    return replace(base, **overrides)


def fields(run_config):
    return [issue.field for issue in run_config_issues(run_config)]


class RunConfigValidationTests(unittest.TestCase):
    def test_valid_config_has_no_issues(self):
        self.assertEqual(run_config_issues(config()), [])

    def test_mode_and_batch_type_must_be_supported(self):
        self.assertEqual(
            fields(config(mode="debug", batch_type="pbs")), ["mode", "batch_type"]
        )

    def test_target_rules(self):
        for target in ("", "-oProxyCommand=x", "bad target", "host\n"):
            with self.subTest(target=target):
                self.assertEqual(
                    fields(config(connection=ConnectionConfig(target))),
                    ["connection.target"],
                )

    def test_backpack_and_ref_rules(self):
        self.assertEqual(
            fields(config(backpack=BackpackSource("--upload-pack=x", "-b"))),
            ["backpack.repository", "backpack.ref"],
        )

    def test_entrypoint_must_stay_inside_workflow(self):
        for entrypoint in ("../a.py", "/etc/passwd", "-x"):
            with self.subTest(entrypoint=entrypoint):
                self.assertEqual(fields(config(entrypoint=entrypoint)), ["entrypoint"])
        self.assertEqual(fields(config(entrypoint="nested/run.py")), [])

    def test_environment_rules(self):
        environment = EnvironmentConfig(
            env_name="bad name",
            floability_version="1.0;rm",
            conda_executable="/opt/conda/bin/conda",
            reinstall_miniforge=True,
        )
        self.assertEqual(
            fields(config(environment=environment)),
            [
                "environment.env_name",
                "environment.floability_version",
                "environment.reinstall_miniforge",
            ],
        )

    def test_remote_root_and_ports(self):
        self.assertEqual(
            fields(config(remote_root=" ", jupyter_port=0, local_port=70000)),
            ["remote_root", "jupyter_port", "local_port"],
        )

    def test_floability_cache_paths_reject_options_and_control_characters(self):
        self.assertEqual(
            fields(config(base_dir="-x", data_cache_dir="cache\nother")),
            ["base_dir", "data_cache_dir"],
        )
        self.assertEqual(
            fields(config(base_dir="~/floability base", data_cache_dir="/scratch/data")),
            [],
        )

    def test_all_issues_are_reported_together(self):
        with self.assertRaises(ConfigError) as caught:
            validate_run_config(
                config(connection=ConnectionConfig("-x"), entrypoint="../a")
            )
        self.assertEqual(
            [issue.cli_message for issue in caught.exception.issues],
            [
                "--target must be an SSH host or user@host, not an option.",
                "--entrypoint must be a relative filename inside workflow/.",
            ],
        )

    def test_missing_identity_file_message_has_no_flag(self):
        issues = run_config_issues(
            config(connection=ConnectionConfig("host", "/missing/key"))
        )
        self.assertEqual(
            issues[0].cli_message, "SSH identity file does not exist: /missing/key"
        )

    def test_identity_file_is_expanded(self):
        with tempfile.TemporaryDirectory() as home:
            key = Path(home) / "id_test"
            key.write_text("key")
            with mock.patch.dict(os.environ, {"HOME": home}):
                result = validate_run_config(
                    config(connection=ConnectionConfig("host", "~/id_test"))
                )
        self.assertEqual(result.connection.identity_file, str(key))


if __name__ == "__main__":
    unittest.main()
