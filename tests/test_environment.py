import unittest
from unittest import mock

from floability_remote.config import EnvironmentConfig
from floability_remote.environment import MANAGED_CONDA, ensure_environment
from floability_remote.errors import RemoteRunError
from floability_remote.events import Emitter, ListSink
from floability_remote.interaction import INSTALL_MINIFORGE
from floability_remote.models import RemoteProbe
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
    def test_default_conda_creates_managed_environment_when_missing(self):
        before = probe("/opt/conda/bin/conda", None, None)
        after = probe(
            "/opt/conda/bin/conda",
            "/opt/conda/envs/floability-remote-managed",
            "0.3.1",
        )
        session = mock.Mock()
        emitter = Emitter(ListSink())

        with mock.patch(
            "floability_remote.environment.probe_remote",
            side_effect=(before, after),
        ) as probe_remote:
            result = ensure_environment(session, EnvironmentConfig(), emitter)

        self.assertEqual(result, after)
        self.assertEqual(
            session.run_script.call_args.args[:2],
            (
                remote_scripts.PREPARE_ENVIRONMENT,
                (
                    "/opt/conda/bin/conda",
                    "floability-remote-managed",
                    "",
                    "",
                ),
            ),
        )
        self.assertEqual(probe_remote.call_args_list[1].args[2], before.conda)

    def test_default_conda_repairs_managed_environment_without_floability(self):
        prefix = "/opt/conda/envs/floability-remote-managed"
        before = probe("/opt/conda/bin/conda", prefix, None)
        after = probe("/opt/conda/bin/conda", prefix, "0.3.1")
        session = mock.Mock()

        with mock.patch(
            "floability_remote.environment.probe_remote",
            side_effect=(before, after),
        ):
            result = ensure_environment(
                session, EnvironmentConfig(), Emitter(ListSink())
            )

        self.assertEqual(result, after)
        self.assertEqual(
            session.run_script.call_args.args[:2],
            (
                remote_scripts.PREPARE_ENVIRONMENT,
                (
                    "/opt/conda/bin/conda",
                    "floability-remote-managed",
                    "",
                    prefix,
                ),
            ),
        )

    def test_reinstall_uses_managed_miniforge_even_if_conda_exists(self):
        before = probe(
            "/opt/site/bin/conda",
            "/opt/site/envs/floability-remote-managed",
            "0.3.0",
        )
        after = probe(
            "/home/test/.local/share/floability-remote/miniforge/bin/conda",
            "/home/test/.local/share/floability-remote/miniforge/envs/floability-remote-managed",
            "0.3.0",
        )
        session = mock.Mock()
        emitter = Emitter(ListSink())

        with mock.patch(
            "floability_remote.environment.probe_remote",
            side_effect=(before, after),
        ) as probe_remote:
            result = ensure_environment(
                session, EnvironmentConfig(reinstall_miniforge=True), emitter
            )

        self.assertEqual(result, after)
        session.run_script.assert_called_once_with(
            remote_scripts.INSTALL_MINIFORGE,
            ("x86_64", "yes"),
            on_output=emitter.log_block,
            output_in_error=True,
        )
        self.assertEqual(probe_remote.call_args_list[1].args[2], MANAGED_CONDA)

    def test_reinstall_verifies_created_environment_with_managed_conda(self):
        before = probe(
            "/opt/site/bin/conda",
            "/opt/site/envs/floability-remote-managed",
            "0.3.0",
        )
        managed_without_environment = probe(
            "/home/test/.local/share/floability-remote/miniforge/bin/conda",
            None,
            None,
        )
        managed_ready = probe(
            "/home/test/.local/share/floability-remote/miniforge/bin/conda",
            "/home/test/.local/share/floability-remote/miniforge/envs/floability-remote-managed",
            "0.3.1",
        )
        session = mock.Mock()
        emitter = Emitter(ListSink())

        with mock.patch(
            "floability_remote.environment.probe_remote",
            side_effect=(before, managed_without_environment, managed_ready),
        ) as probe_remote:
            result = ensure_environment(
                session, EnvironmentConfig(reinstall_miniforge=True), emitter
            )

        self.assertEqual(result, managed_ready)
        self.assertEqual(probe_remote.call_args_list[1].args[2], MANAGED_CONDA)
        self.assertEqual(
            probe_remote.call_args_list[2].args[2],
            managed_without_environment.conda,
        )
        self.assertEqual(
            session.run_script.call_args_list[1].args[:2],
            (
                remote_scripts.PREPARE_ENVIRONMENT,
                (
                    managed_without_environment.conda,
                    "floability-remote-managed",
                    "",
                    "",
                ),
            ),
        )

    def test_missing_conda_asks_for_confirmation(self):
        missing = probe(None, None, None)
        installed = probe(
            "/home/test/.local/share/floability-remote/miniforge/bin/conda",
            "/home/test/.local/share/floability-remote/miniforge/envs/floability-remote-managed",
            "0.3.1",
        )
        session = mock.Mock()
        requests = []

        def approve(request):
            requests.append(request)
            return True

        with mock.patch(
            "floability_remote.environment.probe_remote",
            side_effect=(missing, installed),
        ):
            result = ensure_environment(
                session, EnvironmentConfig(), Emitter(ListSink()), approve
            )

        self.assertEqual(result, installed)
        self.assertEqual([request.key for request in requests], [INSTALL_MINIFORGE])
        self.assertEqual(
            session.run_script.call_args.args[:2],
            (remote_scripts.INSTALL_MINIFORGE, ("x86_64", "no")),
        )

    def test_declined_confirmation_installs_nothing(self):
        session = mock.Mock()
        with mock.patch(
            "floability_remote.environment.probe_remote",
            return_value=probe(None, None, None),
        ):
            with self.assertRaisesRegex(RemoteRunError, "declined"):
                ensure_environment(
                    session, EnvironmentConfig(), Emitter(ListSink()), lambda _: False
                )
        session.run_script.assert_not_called()

    def test_default_confirmation_declines(self):
        session = mock.Mock()
        with mock.patch(
            "floability_remote.environment.probe_remote",
            return_value=probe(None, None, None),
        ):
            with self.assertRaisesRegex(RemoteRunError, "declined"):
                ensure_environment(session, EnvironmentConfig(), Emitter(ListSink()))
        session.run_script.assert_not_called()

    def test_ready_environment_reports_structured_detail(self):
        sink = ListSink()
        ready = probe(
            "/opt/conda/bin/conda",
            "/opt/conda/envs/floability-remote-managed",
            "0.3.1",
        )
        with mock.patch(
            "floability_remote.environment.probe_remote", return_value=ready
        ):
            ensure_environment(mock.Mock(), EnvironmentConfig(), Emitter(sink))
        self.assertEqual(
            [event.message for event in sink.events],
            [
                "Environment ready: "
                "/opt/conda/envs/floability-remote-managed (0.3.1)"
            ],
        )


if __name__ == "__main__":
    unittest.main()
