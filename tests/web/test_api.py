import shlex
import unittest

from floability_remote import __version__
from floability_remote.cli import build_parser, cli_arguments, validate_args
from floability_remote.config import SUPPORTED_BATCH_TYPES, SUPPORTED_MODES

from .support import make_client, requires_web, run_request


@requires_web
class SystemEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_health(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "version": __version__})

    def test_meta_uses_shared_configuration(self):
        meta = self.client.get("/api/v1/meta").json()
        self.assertEqual(meta["api_version"], "v1")
        self.assertEqual(meta["modes"], list(SUPPORTED_MODES))
        self.assertEqual(meta["batch_types"], list(SUPPORTED_BATCH_TYPES))
        self.assertEqual(meta["defaults"]["env_name"], "floability-remote-managed")
        for available in ("validate", "connect", "execute", "cancel", "run"):
            self.assertTrue(meta["features"][available]["available"], available)
        self.assertTrue(meta["features"]["transfer"]["available"])
        self.assertEqual(meta["features"]["transfer"]["milestone"], "M5")

    def test_openapi_is_versioned(self):
        schema = self.client.get("/api/v1/openapi.json").json()
        self.assertIn("/api/v1/runs/validate", schema["paths"])
        self.assertIn("/api/v1/cluster/check", schema["paths"])
        self.assertIn("/api/v1/runs/{run_id}/files", schema["paths"])
        self.assertEqual(self.client.get("/docs").status_code, 404)

    def test_unknown_route_uses_error_envelope(self):
        response = self.client.get("/api/v1/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")


@requires_web
class ValidateRunTests(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def validate(self, body):
        return self.client.post("/api/v1/runs/validate", json=body)

    def test_valid_configuration_returns_equivalent_cli_command(self):
        response = self.validate(run_request(entrypoint="main.py"))
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["valid"])
        self.assertEqual(
            result["command"],
            "floability-remote execute --target user@login.example.org "
            "--backpack https://github.com/example/backpack.git "
            "--batch-type slurm --entrypoint main.py",
        )

    def test_floability_options_reach_the_cli_command(self):
        result = self.validate(
            run_request(
                floability_options=[
                    {"name": "--workers", "value": "2"},
                    {"name": "verbose"},
                ]
            )
        ).json()
        self.assertTrue(result["valid"], result["issues"])
        self.assertTrue(
            result["command"].endswith(
                "--floability-option workers=2 --floability-option verbose"
            )
        )

    def test_managed_floability_option_is_reported_on_its_row(self):
        result = self.validate(
            run_request(
                floability_options=[
                    {"name": "workers", "value": "2"},
                    {"name": "backpack", "value": "elsewhere"},
                ]
            )
        ).json()
        self.assertFalse(result["valid"])
        self.assertEqual(
            [issue["field"] for issue in result["issues"]],
            ["floability_options.1.name"],
        )

    def test_issues_match_cli_validation(self):
        body = run_request(
            connection={"target": "-oProxyCommand=x"},
            entrypoint="../escape.py",
            environment={"env_name": "bad name"},
        )
        result = self.validate(body).json()
        self.assertFalse(result["valid"])
        self.assertIsNone(result["command"])
        self.assertEqual(
            [(issue["field"], issue["flag"]) for issue in result["issues"]],
            [
                ("connection.target", "--target"),
                ("entrypoint", "--entrypoint"),
                ("environment.env_name", "--env-name"),
            ],
        )
        self.assertEqual(
            result["issues"][0]["message"],
            "must be an SSH host or user@host, not an option.",
        )

    def test_malformed_body_uses_error_envelope_with_field_paths(self):
        response = self.validate({"mode": "execute", "connection": {}})
        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid_request")
        fields = {issue["field"] for issue in error["issues"]}
        self.assertIn("connection.target", fields)
        self.assertIn("backpack", fields)

    def test_cli_only_ssh_options_are_rejected(self):
        body = run_request(
            connection={"target": "host", "ssh_options": ["ProxyCommand=evil"]}
        )
        response = self.validate(body)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["issues"][0]["field"], "connection.ssh_options"
        )


class CliArgumentRoundTripTests(unittest.TestCase):
    """The command shown by the web UI must parse back to the same config."""

    def round_trip(self, *arguments):
        config = validate_args(build_parser().parse_args(list(arguments)))
        reparsed = validate_args(build_parser().parse_args(cli_arguments(config)))
        self.assertEqual(reparsed, config)

    def test_minimal_execute(self):
        self.round_trip(
            "execute",
            "--target",
            "h",
            "--backpack",
            "https://x/y.git",
            "--batch-type",
            "local",
        )

    def test_every_option(self):
        self.round_trip(
            "run",
            "--target",
            "user@h",
            "--backpack",
            "https://x/y.git",
            "--batch-type",
            "condor",
            "--ref",
            "v1.2",
            "--entrypoint",
            "main.ipynb",
            "--env-name",
            "custom-env",
            "--floability-version",
            "0.4.0",
            "--remote-root",
            "/scratch/runs",
            "--base-dir",
            "/scratch/floability",
            "--data-cache-dir",
            "/scratch/floability-data",
            "--ssh-option",
            "ServerAliveInterval=30",
            "--jupyter-port",
            "8999",
            "--local-port",
            "49000",
            "--reinstall-miniforge",
            "--floability-option",
            "workers=2",
            "--floability-option",
            "label=two words",
            "--floability-option",
            "verbose",
        )

    def test_quoting(self):
        config = validate_args(
            build_parser().parse_args(
                [
                    "execute",
                    "--target",
                    "h",
                    "--backpack",
                    "https://x/y.git",
                    "--batch-type",
                    "local",
                    "--remote-root",
                    "~/my runs",
                ]
            )
        )
        from floability_remote.cli import cli_command

        self.assertEqual(shlex.split(cli_command(config))[1:], cli_arguments(config))


if __name__ == "__main__":
    unittest.main()
