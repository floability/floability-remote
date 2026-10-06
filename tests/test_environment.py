import unittest
from types import SimpleNamespace
from unittest import mock

from floability_remote.environment import MANAGED_CONDA, ensure_environment
from floability_remote.models import RemoteProbe
from floability_remote.output import Reporter
from floability_remote import remote_scripts


def probe(conda, prefix, version):
    return RemoteProbe(
        os_name="Linux",
        architecture="x86_64",
        conda=conda,
        env_prefix=prefix,
        floability_version=version,
        git_available=True,
        setsid_available=True,
        downloader="curl",
    )


class EnvironmentTests(unittest.TestCase):
    def test_reinstall_uses_managed_miniforge_even_if_conda_exists(self):
        before = probe(
            "/opt/site/bin/conda",
            "/opt/site/envs/floability-env",
            "0.3.0",
        )
        after = probe(
            "/home/test/.local/share/floability-remote/miniforge/bin/conda",
            "/home/test/.local/share/floability-remote/miniforge/envs/floability-env",
            "0.3.0",
        )
        session = mock.Mock()
        args = SimpleNamespace(
            env_name="floability-env",
            conda_executable="",
            reinstall_miniforge=True,
            floability_version="",
            verbose=False,
            yes=False,
        )

        with mock.patch(
            "floability_remote.environment.probe_remote",
            side_effect=(before, after),
        ) as probe_remote:
            result = ensure_environment(session, args, Reporter())

        self.assertEqual(result, after)
        session.run_script.assert_called_once_with(
            remote_scripts.INSTALL_MINIFORGE,
            ("x86_64", "yes"),
            show_output=False,
        )
        self.assertEqual(probe_remote.call_args_list[1].args[2], MANAGED_CONDA)


if __name__ == "__main__":
    unittest.main()
