import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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

    def test_inspection_lists_workflow_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workflow" / "steps").mkdir(parents=True)
            (root / "workflow" / "main.py").write_text("print('ok')")
            (root / "workflow" / "steps" / "prepare.sh").write_text("true")
            report = self.client.post(
                "/api/v1/backpacks/inspect", json={"path": str(root)}
            ).json()
        self.assertEqual(report["workflow_files"], ["main.py", "steps/prepare.sh"])

    def test_browse_marks_backpack_folders_and_hides_dot_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "analysis" / "workflow").mkdir(parents=True)
            (root / "notes").mkdir()
            (root / ".hidden").mkdir()
            listing = self.client.post(
                "/api/v1/backpacks/browse", json={"path": str(root)}
            ).json()
        self.assertEqual(listing["path"], str(root))
        self.assertEqual(listing["parent"], str(root.parent))
        self.assertFalse(listing["is_backpack"])
        self.assertEqual(
            [(item["name"], item["is_backpack"]) for item in listing["directories"]],
            [("analysis", True), ("notes", False)],
        )

    def test_browse_missing_folder_is_reported(self):
        response = self.client.post(
            "/api/v1/backpacks/browse", json={"path": "/definitely/missing/folder"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "browse_failed")

    def upload(self, archive_bytes, filename="project.zip"):
        return self.client.post(
            f"/api/v1/backpacks/zip?filename={filename}",
            content=archive_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )

    def test_zip_is_kept_unchanged_and_inspected(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("project/", "")
            zipped.writestr("project/workflow/main.py", "print('ok')")
        response = self.upload(archive.getvalue(), "My Project.zip")
        self.assertEqual(response.status_code, 201, response.text)
        report = response.json()
        stored = Path(report["path"])
        self.addCleanup(shutil.rmtree, stored.parent, True)
        self.assertTrue(report["valid"], report["problems"])
        self.assertEqual(report["source_type"], "zip")
        self.assertEqual(report["workflow_files"], ["main.py"])
        self.assertEqual(stored.name, "My-Project.zip")
        self.assertEqual(stored.read_bytes(), archive.getvalue())

    def test_invalid_zip_is_reported_and_not_kept(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("README.md", "no workflow")
        response = self.upload(archive.getvalue(), "bad.zip")
        self.assertEqual(response.status_code, 201)
        report = response.json()
        self.assertFalse(report["valid"])
        self.assertIn("Backpack must contain a workflow/ folder.", report["problems"])
        self.assertFalse(Path(report["path"]).exists())

    def test_non_zip_upload_is_rejected(self):
        response = self.upload(b"data", "notes.txt")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "not_zip")

    def test_per_file_staging_routes_are_gone(self):
        self.assertEqual(self.client.post("/api/v1/backpacks/staging").status_code, 404)

if __name__ == "__main__":
    unittest.main()
