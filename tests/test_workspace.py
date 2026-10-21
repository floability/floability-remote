import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from floability_remote.domain.config import BackpackSource
from floability_remote.domain.events import Emitter, ListSink
from floability_remote.deployment.workspace import create_workspace


class Session:
    def __init__(self):
        self.stream_calls = []
        self.script_calls = []

    def run_stream(self, script, arguments, stream):
        self.stream_calls.append((script, arguments, stream.read()))
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "__FLOABILITY_REMOTE_RUN_DIR__=/remote/run\n"
                "__FLOABILITY_REMOTE_BACKPACK__=/remote/run/backpack\n"
            ),
        )

    def run_script(self, script, arguments=(), **kwargs):
        self.script_calls.append((script, arguments))
        return SimpleNamespace(returncode=0, stdout="")


class WorkspaceTests(unittest.TestCase):
    def test_local_workspace_uploads_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workflow").mkdir()
            (root / "workflow" / "main.py").write_text("print('ok')")
            session = Session()
            workspace = create_workspace(
                session,
                BackpackSource(source_type="folder", local_path=str(root)),
                "~/runs",
                Emitter(ListSink()),
            )
        self.assertEqual(workspace.run_dir, "/remote/run")
        self.assertEqual(len(session.stream_calls), 1)
        self.assertGreater(len(session.stream_calls[0][2]), 0)
        self.assertEqual(len(session.script_calls), 1)
        self.assertIn('"local_path"', session.script_calls[0][1][1])


if __name__ == "__main__":
    unittest.main()
