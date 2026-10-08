import unittest

from floability_remote.errors import RemoteRunError
from floability_remote.models import JupyterConnection
from floability_remote.output import (
    concise_floability_progress,
    local_jupyter_url,
    marker_values,
    parse_jupyter_connection,
    parse_probe,
)


class OutputParsingTests(unittest.TestCase):
    def test_detected_jupyter_message(self):
        connection = parse_jupyter_connection(
            "[jupyter] Detected JupyterLab URL with port 8889 and token abc123."
        )
        self.assertEqual(connection, JupyterConnection(8889, "abc123"))

    def test_jupyter_url(self):
        connection = parse_jupyter_connection(
            "remote: http://10.2.3.4:8890/lab/?token=a-b_c.12"
        )
        self.assertEqual(connection, JupyterConnection(8890, "a-b_c.12", "/lab/"))

    def test_local_url_rewrites_host_and_port(self):
        connection = JupyterConnection(8888, "abc123", "/lab/")
        self.assertEqual(
            local_jupyter_url(connection, 49172),
            "http://127.0.0.1:49172/lab/?token=abc123",
        )

    def test_unrelated_output(self):
        self.assertIsNone(parse_jupyter_connection("Preparing environment..."))

    def test_floability_milestone_becomes_concise_progress(self):
        self.assertEqual(
            concise_floability_progress(
                "[floability] environment setup (manager & worker)"
            ),
            "Preparing the backpack software environment",
        )

    def test_execute_milestone_becomes_concise_progress(self):
        self.assertEqual(
            concise_floability_progress("[floability] Python script execution"),
            "Executing the Python workflow",
        )

    def test_marker_parsing(self):
        values = marker_values(
            "banner\n__FLOABILITY_REMOTE_CONDA__=/opt/conda/bin/conda\n"
        )
        self.assertEqual(
            values["__FLOABILITY_REMOTE_CONDA__"], "/opt/conda/bin/conda"
        )

    def test_parse_complete_probe(self):
        output = "\n".join(
            [
                "__FLOABILITY_REMOTE_OS__=Linux",
                "__FLOABILITY_REMOTE_ARCH__=x86_64",
                "__FLOABILITY_REMOTE_CONDA__=/opt/conda/bin/conda",
                "__FLOABILITY_REMOTE_ENV_PREFIX__=/opt/conda/envs/floability-remote-managed",
                "__FLOABILITY_REMOTE_VERSION__=0.3.1",
                "__FLOABILITY_REMOTE_GIT__=yes",
                "__FLOABILITY_REMOTE_SETSID__=yes",
                "__FLOABILITY_REMOTE_DOWNLOADER__=curl",
            ]
        )
        probe = parse_probe(output)
        self.assertEqual(probe.architecture, "x86_64")
        self.assertTrue(probe.git_available)
        self.assertEqual(probe.floability_version, "0.3.1")

    def test_probe_requires_os_markers(self):
        with self.assertRaises(RemoteRunError):
            parse_probe("login banner only")


if __name__ == "__main__":
    unittest.main()
