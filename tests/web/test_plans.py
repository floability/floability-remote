import tempfile
import unittest
from pathlib import Path
from unittest import mock

from floability_remote.application.audit import AuditRecorder
from floability_remote.application.planning import PlanningStore
from floability_remote.application.runtime import ApplicationRuntime
from floability_remote.deployment.runs import RunManager
from floability_remote.domain.models import RemoteProbe
from floability_remote.remote.connection import ConnectionSnapshot
from floability_remote.services.cluster import ClusterReport

from .support import make_client, requires_web


TARGET = "user@login.example.org"


class ConnectedSession:
    def snapshot(self):
        return ConnectionSnapshot(
            state="connected",
            target=TARGET,
            remote_user="user",
            remote_host="login.example.org",
            connected_at=42.0,
        )

    def session_for(self, target):
        if target != TARGET:
            raise AssertionError(target)
        return object()

    def close(self):
        pass


def cluster_report():
    return ClusterReport(
        remote_user="user",
        remote_host="login.example.org",
        probe=RemoteProbe(
            os_name="Linux",
            architecture="x86_64",
            conda="/opt/conda/bin/conda",
            env_prefix="/opt/conda/envs/floability-remote-managed",
            floability_version="0.3.1",
            git_available=True,
            setsid_available=True,
            downloader="curl",
        ),
        env_name="floability-remote-managed",
        requested_base_dir="~/floability-base-dir",
        resolved_base_dir="/home/user/floability-base-dir",
        storage_path="/home/user",
        total_bytes=1000,
        free_bytes=750,
        quota_status="not-reported",
        quota_summary="",
        available_batch_types=("local", "slurm"),
        issues=(),
    )


@requires_web
class PlanApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.backpack = root / "backpack"
        (self.backpack / "workflow").mkdir(parents=True)
        (self.backpack / "workflow" / "main.py").write_text("print('ok')\n")
        (self.backpack / "README.md").write_text("# Demo\n")
        connections = ConnectedSession()
        self.runtime = ApplicationRuntime(
            connections=connections,
            runs=RunManager(connections),
            audit=AuditRecorder(root / "audit.jsonl"),
            planning=PlanningStore(str(root / "plans")),
        )
        self.client = make_client(services=self.runtime)

    def tearDown(self):
        self.runtime.close()
        self.temporary.cleanup()

    def inspect(self):
        response = self.client.post(
            "/api/v1/backpacks/sources/inspect",
            json={"source_type": "folder", "local_path": str(self.backpack)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_inspect_list_read_check_and_validate_plan(self):
        source = self.inspect()
        self.assertEqual(source["entrypoints"], ["main.py"])

        files = self.client.post(
            "/api/v1/backpacks/sources/files",
            json={"source_id": source["source_id"]},
        )
        self.assertEqual(
            [item["path"] for item in files.json()["files"]],
            ["README.md", "workflow/main.py"],
        )
        read = self.client.post(
            "/api/v1/backpacks/sources/read",
            json={"source_id": source["source_id"], "path": "workflow/main.py"},
        )
        self.assertEqual(read.json()["content"], "print('ok')\n")

        with mock.patch(
            "floability_remote.application.operations.plans.ClusterService.check",
            return_value=cluster_report(),
        ):
            planned = self.client.post(
                "/api/v1/plans/validate",
                json={
                    "source_id": source["source_id"],
                    "mode": "execute",
                    "batch_type": "local",
                },
            )
        self.assertEqual(planned.status_code, 200, planned.text)
        body = planned.json()
        self.assertTrue(body["valid"], body["issues"])
        self.assertTrue(body["plan_id"].startswith("plan-"))
        self.assertEqual(body["entrypoint"], "main.py")
        self.assertIn("--entrypoint main.py", body["command"])
        self.assertEqual(
            self.runtime.planning.plan(body["plan_id"]).source_id, source["source_id"]
        )

    def test_changed_source_is_rejected(self):
        source = self.inspect()
        (self.backpack / "workflow" / "main.py").write_text("print('changed')\n")
        check = self.client.post(
            "/api/v1/backpacks/sources/check",
            json={"source_id": source["source_id"]},
        )
        self.assertFalse(check.json()["fresh"])

        response = self.client.post(
            "/api/v1/plans/validate",
            json={
                "source_id": source["source_id"],
                "mode": "execute",
                "batch_type": "local",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["valid"])
        self.assertIn(
            "source_id", [item["field"] for item in response.json()["issues"]]
        )

    def test_mode_is_derived_from_a_script_entrypoint(self):
        source = self.inspect()
        with mock.patch(
            "floability_remote.application.operations.plans.ClusterService.check",
            return_value=cluster_report(),
        ):
            response = self.client.post(
                "/api/v1/plans/validate",
                json={"source_id": source["source_id"], "batch_type": "local"},
            )

        body = response.json()
        self.assertTrue(body["valid"], body["issues"])
        self.assertEqual(body["mode"], "execute")

    def test_incompatible_explicit_mode_is_rejected(self):
        source = self.inspect()
        response = self.client.post(
            "/api/v1/plans/validate",
            json={
                "source_id": source["source_id"],
                "mode": "run",
                "batch_type": "local",
            },
        )

        body = response.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["issues"][0]["field"], "mode")
        self.assertIn("execute", body["issues"][0]["message"])

    def test_unavailable_batch_type_is_rejected(self):
        source = self.inspect()
        report = cluster_report()
        report = ClusterReport(
            **{**report.__dict__, "available_batch_types": ("local",)}
        )
        with mock.patch(
            "floability_remote.application.operations.plans.ClusterService.check",
            return_value=report,
        ):
            response = self.client.post(
                "/api/v1/plans/validate",
                json={
                    "source_id": source["source_id"],
                    "mode": "execute",
                    "batch_type": "slurm",
                },
            )
        self.assertFalse(response.json()["valid"])
        self.assertIn(
            "batch_type", [item["field"] for item in response.json()["issues"]]
        )


if __name__ == "__main__":
    unittest.main()
