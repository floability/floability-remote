import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from .support import make_client, requires_web


@requires_web
class BackpackApiTests(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_inspect_local_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workflow").mkdir()
            (root / "workflow" / "main.py").write_text("print('ok')")
            response = self.client.post("/api/v1/backpacks/inspect", json={"path": str(root)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["valid"])
        self.assertEqual(response.json()["file_count"], 1)

    def test_stage_selected_folder_files(self):
        created = self.client.post("/api/v1/backpacks/staging").json()
        upload_id = created["upload_id"]
        response = self.client.put(
            f"/api/v1/backpacks/staging/{upload_id}/files?path=workflow%2Fmain.py",
            content=b"print('ok')\n",
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 204)
        report = self.client.post(
            f"/api/v1/backpacks/staging/{upload_id}/complete"
        ).json()
        self.assertTrue(report["valid"], report["problems"])

    def test_staging_rejects_parent_path(self):
        upload_id = self.client.post("/api/v1/backpacks/staging").json()["upload_id"]
        response = self.client.put(
            f"/api/v1/backpacks/staging/{upload_id}/files?path=..%2Foutside",
            content=b"bad",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsafe_path")

    def test_stage_zip(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("project/workflow/main.py", "print('ok')")
        response = self.client.post(
            "/api/v1/backpacks/zip?filename=project.zip",
            content=archive.getvalue(),
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["valid"], response.json()["problems"])


if __name__ == "__main__":
    unittest.main()
