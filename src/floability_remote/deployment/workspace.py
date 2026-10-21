"""Remote workspaces holding a cloned or uploaded backpack."""

import json
import time
import uuid
from dataclasses import asdict

from ..domain.config import BackpackSource
from ..domain.errors import RemoteRunError
from ..domain.events import Emitter
from ..domain.models import RemoteWorkspace
from ..remote import scripts as remote_scripts
from ..remote.output import marker_values
from ..remote.ssh import SSHSession
from ..services.backpacks import pack, provenance, upload


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def create_workspace(
    session: SSHSession,
    source: BackpackSource,
    remote_root: str,
    emitter: Emitter,
) -> RemoteWorkspace:
    """Create a fresh remote workspace from Git or a local backpack."""
    run_id = new_run_id()
    if source.source_type == "git":
        result = session.run_script(
            remote_scripts.CLONE_BACKPACK,
            (remote_root, run_id, source.repository, source.ref),
            on_output=emitter.log_block,
            output_in_error=not emitter.logs_visible,
        )
    else:
        stream = pack(source.local_path)
        try:
            run_dir = f"{remote_root.rstrip('/')}/{run_id}"
            result = upload(session, stream, run_dir)
        finally:
            stream.close()
    values = marker_values(result.stdout or "")
    run_dir = values.get("__FLOABILITY_REMOTE_RUN_DIR__")
    backpack_dir = values.get("__FLOABILITY_REMOTE_BACKPACK__")
    if not run_dir or not backpack_dir:
        raise RemoteRunError(
            "Remote workspace creation completed without returning its paths."
        )

    if source.source_type != "git":
        session.run_script(
            remote_scripts.WRITE_RUN_SOURCE,
            (
                run_dir,
                json.dumps(asdict(provenance(source.local_path)), sort_keys=True),
            ),
        )

    emitter.detail(f"Backpack ready: {backpack_dir}", backpack_dir=backpack_dir)
    return RemoteWorkspace(run_id, run_dir, backpack_dir)
