"""Architecture contracts introduced before MCP and agent adapters."""

import ast
import json
import os
import stat
import tempfile
import time
import unittest
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

from floability_remote.application.audit import AuditRecorder
from floability_remote.application.runtime import ApplicationRuntime
from floability_remote.cli import build_parser
from floability_remote.daemon.app import WebSettings, create_app
from floability_remote.daemon.session import DaemonDescriptor, SessionStore
from floability_remote.daemon.server import _stop_when_idle


class AuditTests(unittest.TestCase):
    def test_record_is_private_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            recorder = AuditRecorder(path)
            with recorder.operation(
                "connection.answer_prompt",
                client="test",
                session_id="session-1",
                target="user@example",
                inputs={"answer": "secret", "nested": {"api_token": "also-secret"}},
            ):
                pass

            record = json.loads(path.read_text())
            self.assertEqual(record["operation"], "connection.answer_prompt")
            self.assertEqual(record["inputs"]["answer"], "<redacted>")
            self.assertEqual(record["inputs"]["nested"]["api_token"], "<redacted>")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_daemon_operation_records_client_metadata(self):
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            runtime = ApplicationRuntime.create(audit=AuditRecorder(path))
            app = create_app(WebSettings(port=8123, session_token="session"), runtime=runtime)
            with TestClient(
                app,
                base_url="http://127.0.0.1:8123",
                headers={
                    "Authorization": "Bearer session",
                    "X-Floability-Client": "contract-test",
                    "X-Floability-Client-Session": "client-1",
                },
            ) as client:
                response = client.get("/api/v1/connection")
            self.assertEqual(response.status_code, 200)
            record = json.loads(path.read_text())
            self.assertEqual(record["client"], "contract-test")
            self.assertEqual(record["client_session"], "client-1")
            self.assertEqual(record["operation"], "connection.get")


class SessionDescriptorTests(unittest.TestCase):
    def test_descriptor_round_trip_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daemon.json"
            store = SessionStore(path)
            descriptor = DaemonDescriptor(123, 8123, "private-token", 42.0)
            store.write(descriptor)
            self.assertEqual(store.read(), descriptor)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            store.remove_if_owner(123, "private-token")
            self.assertFalse(path.exists())

    @unittest.skipIf(os.name == "nt", "POSIX mode check")
    def test_insecure_descriptor_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daemon.json"
            store = SessionStore(path)
            store.write(DaemonDescriptor(123, 8123, "token", 42.0))
            path.chmod(0o644)
            self.assertIsNone(store.read())


class IdleShutdownTests(unittest.TestCase):
    def test_idle_daemon_stops_when_no_connection_or_run_exists(self):
        server = SimpleNamespace(should_exit=False)
        runtime = SimpleNamespace(
            connections=SimpleNamespace(
                snapshot=lambda: SimpleNamespace(state="disconnected")
            ),
            runs=SimpleNamespace(has_active_run=lambda: False),
        )
        app = SimpleNamespace(
            state=SimpleNamespace(
                runtime=runtime,
                last_activity=time.monotonic() - 1,
            )
        )
        _stop_when_idle(server, app, 0.01)
        self.assertTrue(server.should_exit)


class CliContractTests(unittest.TestCase):
    def test_daemon_lifecycle_commands_parse(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["daemon", "start"]).daemon_command, "start")
        self.assertEqual(parser.parse_args(["daemon", "status"]).daemon_command, "status")
        self.assertEqual(parser.parse_args(["daemon", "stop"]).daemon_command, "stop")


class PackageAndImportBoundaryTests(unittest.TestCase):
    def test_package_root_contains_only_entrypoints(self):
        package = Path(__file__).parents[1] / "src" / "floability_remote"
        root_modules = sorted(path.name for path in package.glob("*.py"))
        self.assertEqual(root_modules, ["__init__.py", "__main__.py"])

    def test_browser_assets_remain_packaged_under_web(self):
        root = resources.files("floability_remote.web") / "static"
        self.assertTrue((root / "index.html").is_file())
        self.assertTrue((root / "js" / "api.js").is_file())

    def test_low_level_and_application_code_do_not_import_interfaces(self):
        package = Path(__file__).parents[1] / "src" / "floability_remote"
        roots = [
            package / name
            for name in ("application", "deployment", "domain", "remote", "services")
        ]
        forbidden = ("argparse", "fastapi", "mcp", "pydantic_ai")
        interface_parts = {"cli", "daemon", "web", "agent", "mcp"}
        violations = []
        files = []
        for root in roots:
            files.extend(root.rglob("*.py") if root.is_dir() else [root])
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    parts = set(name.split("."))
                    if name.startswith(forbidden) or (
                        name.startswith("floability_remote") and parts & interface_parts
                    ):
                        violations.append(f"{path.name}: {name}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
