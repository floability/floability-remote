import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from floability_remote import remote_scripts
from floability_remote.cluster import ClusterService, validate_cluster_settings
from floability_remote.config import EnvironmentConfig
from floability_remote.errors import RemoteRunError


PROBE_OUTPUT = """\
__FLOABILITY_REMOTE_OS__=Linux
__FLOABILITY_REMOTE_ARCH__=x86_64
__FLOABILITY_REMOTE_CONDA__=/opt/conda/bin/conda
__FLOABILITY_REMOTE_ENV_PREFIX__=/opt/conda/envs/floability-remote-managed
__FLOABILITY_REMOTE_VERSION__=0.3.1
__FLOABILITY_REMOTE_GIT__=yes
__FLOABILITY_REMOTE_SETSID__=yes
__FLOABILITY_REMOTE_DOWNLOADER__=curl
"""

STORAGE_OUTPUT = """\
__FLOABILITY_REMOTE_CLUSTER_USER__=alice
__FLOABILITY_REMOTE_CLUSTER_HOST__=login.example.org
__FLOABILITY_REMOTE_CLUSTER_BASE_DIR__=/home/alice/floability-base-dir
__FLOABILITY_REMOTE_CLUSTER_STORAGE_PATH__=/home/alice
__FLOABILITY_REMOTE_CLUSTER_TOTAL_BYTES__=1000000
__FLOABILITY_REMOTE_CLUSTER_FREE_BYTES__=750000
__FLOABILITY_REMOTE_CLUSTER_QUOTA_STATUS__=reported
__FLOABILITY_REMOTE_CLUSTER_QUOTA_SUMMARY__=100M used of 10G
"""


class ClusterServiceTests(unittest.TestCase):
    def test_ready_report_combines_environment_and_storage(self):
        session = mock.Mock()
        session.run_script.side_effect = (
            SimpleNamespace(stdout=PROBE_OUTPUT),
            SimpleNamespace(stdout=STORAGE_OUTPUT),
        )

        report = ClusterService(session).check(EnvironmentConfig())

        self.assertTrue(report.ready)
        self.assertEqual(report.remote_user, "alice")
        self.assertEqual(report.probe.floability_version, "0.3.1")
        self.assertEqual(report.free_bytes, 750000)
        self.assertEqual(report.requested_base_dir, "~/floability-base-dir")
        self.assertEqual(
            session.run_script.call_args_list[1].args,
            (remote_scripts.CHECK_CLUSTER_STORAGE, ("~/floability-base-dir",)),
        )

    def test_missing_environment_is_reported_without_modifying_it(self):
        missing = PROBE_OUTPUT.replace(
            "__FLOABILITY_REMOTE_ENV_PREFIX__=/opt/conda/envs/floability-remote-managed",
            "__FLOABILITY_REMOTE_ENV_PREFIX__=",
        ).replace(
            "__FLOABILITY_REMOTE_VERSION__=0.3.1", "__FLOABILITY_REMOTE_VERSION__="
        )
        session = mock.Mock()
        session.run_script.side_effect = (
            SimpleNamespace(stdout=missing),
            SimpleNamespace(stdout=STORAGE_OUTPUT),
        )

        report = ClusterService(session).check(EnvironmentConfig())

        self.assertFalse(report.ready)
        self.assertIn("was not found", report.issues[0])
        self.assertEqual(session.run_script.call_count, 2)

    def test_requested_version_mismatch_requires_setup(self):
        session = mock.Mock()
        session.run_script.side_effect = (
            SimpleNamespace(stdout=PROBE_OUTPUT),
            SimpleNamespace(stdout=STORAGE_OUTPUT),
        )

        report = ClusterService(session).check(
            EnvironmentConfig(floability_version="0.4.0")
        )

        self.assertFalse(report.ready)
        self.assertIn("does not match requested 0.4.0", report.issues[0])

    def test_invalid_settings_are_rejected_before_ssh(self):
        with self.assertRaisesRegex(RemoteRunError, "env-name"):
            validate_cluster_settings(EnvironmentConfig(env_name="bad name"), "")
        with self.assertRaisesRegex(RemoteRunError, "base-dir"):
            validate_cluster_settings(EnvironmentConfig(), "-bad")

    def test_storage_script_uses_existing_parent_without_creating_base_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            requested = Path(directory) / "missing" / "floability-base-dir"
            result = subprocess.run(
                ["bash", "-s", "--", str(requested)],
                input=remote_scripts.CHECK_CLUSTER_STORAGE,
                text=True,
                capture_output=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(requested.exists())
            self.assertIn(
                f"__FLOABILITY_REMOTE_CLUSTER_BASE_DIR__={requested}", result.stdout
            )
            self.assertIn(
                f"__FLOABILITY_REMOTE_CLUSTER_STORAGE_PATH__={directory}", result.stdout
            )
            self.assertRegex(
                result.stdout, r"__FLOABILITY_REMOTE_CLUSTER_FREE_BYTES__=\d+"
            )


if __name__ == "__main__":
    unittest.main()
