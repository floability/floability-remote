"""Remote workspaces: a unique run directory holding a cloned backpack."""

import time
import uuid

from . import remote_scripts
from .config import BackpackSource
from .errors import RemoteRunError
from .events import Emitter
from .models import RemoteWorkspace
from .output import marker_values
from .ssh import SSHSession


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def create_workspace(
    session: SSHSession,
    source: BackpackSource,
    remote_root: str,
    emitter: Emitter,
) -> RemoteWorkspace:
    """Clone `source` into a new directory under `remote_root`."""
    run_id = new_run_id()
    result = session.run_script(
        remote_scripts.CLONE_BACKPACK,
        (remote_root, run_id, source.repository, source.ref),
        on_output=emitter.log_block,
        output_in_error=not emitter.logs_visible,
    )
    values = marker_values(result.stdout or "")
    run_dir = values.get("__FLOABILITY_REMOTE_RUN_DIR__")
    backpack_dir = values.get("__FLOABILITY_REMOTE_BACKPACK__")
    if not run_dir or not backpack_dir:
        raise RemoteRunError("Remote clone completed without returning its paths.")

    emitter.detail(f"Backpack ready: {backpack_dir}", backpack_dir=backpack_dir)
    return RemoteWorkspace(run_id, run_dir, backpack_dir)
