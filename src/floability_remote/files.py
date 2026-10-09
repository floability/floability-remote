"""Safe discovery and single-file download for completed remote runs."""

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple

from . import remote_scripts
from .errors import RemoteRunError
from .output import marker_values
from .ssh import SSHSession


MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
GROUP_ORDER = ("command", "workflow", "logs", "records")
GROUP_LABELS = {
    "command": "Run command",
    "workflow": "Workflow and results",
    "logs": "Logs",
    "records": "Run records",
}


@dataclass(frozen=True)
class RemoteFile:
    """One regular, non-symlink file approved by the remote inventory."""

    id: str
    group: str
    path: str
    size: int
    remote_path: str

    @property
    def downloadable(self) -> bool:
        return self.size <= MAX_DOWNLOAD_BYTES

    @property
    def reason(self) -> Optional[str]:
        if self.downloadable:
            return None
        return f"Larger than the {format_size(MAX_DOWNLOAD_BYTES)} download limit."

    @property
    def filename(self) -> str:
        name = PurePosixPath(self.path).name
        cleaned = "".join(
            "_" if character == "\\" or ord(character) < 32 else character
            for character in name
        )
        return cleaned or "download"


@dataclass(frozen=True)
class FileInventory:
    files: Tuple[RemoteFile, ...]
    instance_found: bool
    truncated: bool
    python_path: str


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover


def _file_id(group: str, path: str) -> str:
    value = f"{group}\0{path}".encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(value).hexdigest()[:24]


def select_file(inventory: FileInventory, selector: str) -> RemoteFile:
    """Select one downloadable item by stable ID or displayed logical path."""
    matches = [
        item for item in inventory.files if item.id == selector or item.path == selector
    ]
    if not matches:
        raise RemoteRunError(f"Downloadable file was not found: {selector}")
    if len(matches) > 1:
        raise RemoteRunError(
            f"More than one downloadable file is named {selector}; use its file ID."
        )
    item = matches[0]
    if not item.downloadable:
        raise RemoteRunError(item.reason or "The selected file cannot be downloaded.")
    return item


class FileService:
    """List and download approved files through one authenticated SSH session."""

    def __init__(self, session: SSHSession):
        self.session = session

    def list_files(self, run_dir: str) -> FileInventory:
        result = self.session.run_script(
            remote_scripts.LIST_DOWNLOAD_FILES,
            (run_dir,),
        )
        encoded = marker_values(result.stdout or "").get("__FLOABILITY_REMOTE_FILES__")
        if not encoded:
            raise RemoteRunError("The remote file inventory did not return a result.")
        try:
            payload = json.loads(encoded)
            raw_files = payload["files"]
            python_path = payload["python_path"]
            files = tuple(
                RemoteFile(
                    id=_file_id(item["group"], item["path"]),
                    group=item["group"],
                    path=item["path"],
                    size=int(item["size"]),
                    remote_path=item["remote_path"],
                )
                for item in raw_files
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RemoteRunError("The remote file inventory was malformed.") from error

        allowed_groups = set(GROUP_ORDER)
        for item in files:
            logical = PurePosixPath(item.path)
            if (
                item.group not in allowed_groups
                or not item.path
                or logical.is_absolute()
                or ".." in logical.parts
                or any(ord(character) < 32 for character in item.path)
                or item.size < 0
                or "\x00" in item.remote_path
            ):
                raise RemoteRunError(
                    "The remote file inventory contained an invalid item."
                )
        if not isinstance(python_path, str) or not python_path:
            raise RemoteRunError(
                "The remote file inventory omitted its Python executable."
            )
        return FileInventory(
            files=files,
            instance_found=bool(payload.get("instance_found")),
            truncated=bool(payload.get("truncated")),
            python_path=python_path,
        )

    def find_file(self, run_dir: str, selector: str) -> RemoteFile:
        return select_file(self.list_files(run_dir), selector)

    def download(self, run_dir: str, selector: str, destination: Path) -> RemoteFile:
        """Re-inventory, validate, and atomically download one file."""
        inventory = self.list_files(run_dir)
        item = select_file(inventory, selector)
        destination = Path(destination).expanduser()
        if destination.exists():
            raise RemoteRunError(f"Local destination already exists: {destination}")
        if not destination.parent.is_dir():
            raise RemoteRunError(
                f"Local destination directory does not exist: {destination.parent}"
            )

        temporary = destination.with_name(
            f".{destination.name}.floability-remote-{uuid.uuid4().hex}.part"
        )
        try:
            self.session.run_script_to_file(
                remote_scripts.DOWNLOAD_FILE,
                (
                    item.remote_path,
                    str(MAX_DOWNLOAD_BYTES),
                    inventory.python_path,
                ),
                temporary,
            )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return item

    def download_temporary(
        self, run_dir: str, selector: str
    ) -> Tuple[RemoteFile, Path]:
        """Download to a private temporary file for an HTTP response."""
        directory = Path(tempfile.mkdtemp(prefix="floability-remote-download-"))
        temporary = directory / "payload"
        try:
            item = self.download(run_dir, selector, temporary)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return item, temporary
