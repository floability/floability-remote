"""Safe discovery and single-file download for completed remote runs."""

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional, Tuple

from ..domain.errors import RemoteRunError
from ..remote import scripts as remote_scripts
from ..remote.output import marker_values
from ..remote.ssh import SSHSession


MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_PREVIEW_BYTES = 128 * 1024
MAX_BATCH_DOWNLOAD_BYTES = 500 * 1024 * 1024
GROUP_ORDER = ("command", "backpack", "workflow", "logs", "records")
GROUP_LABELS = {
    "command": "Command log",
    "backpack": "Source backpack",
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


@dataclass(frozen=True)
class FilePreview:
    """Small UTF-8 file content suitable for display or model summarization."""

    file: RemoteFile
    content: str


@dataclass(frozen=True)
class DownloadBatch:
    """Files copied into one newly-created local result directory."""

    files: Tuple[RemoteFile, ...]
    destination: Path

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


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

    def preview(self, run_dir: str, selector: str) -> FilePreview:
        """Read one small UTF-8 file without retaining a local copy."""
        inventory = self.list_files(run_dir)
        item = select_file(inventory, selector)
        if item.size > MAX_PREVIEW_BYTES:
            raise RemoteRunError(
                f"File is larger than the {format_size(MAX_PREVIEW_BYTES)} "
                "text preview limit; download it instead."
            )
        directory = Path(tempfile.mkdtemp(prefix="floability-remote-preview-"))
        temporary = directory / "payload"
        try:
            self._download_item(item, inventory, temporary)
            payload = temporary.read_bytes()
            if b"\x00" in payload:
                raise RemoteRunError(
                    "The selected file appears to be binary and cannot be previewed."
                )
            try:
                content = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise RemoteRunError(
                    "The selected file is not valid UTF-8 and cannot be previewed."
                ) from error
            return FilePreview(file=item, content=content)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def download_many(
        self,
        run_dir: str,
        selectors: Iterable[str],
        destination: Path,
        *,
        all_files: bool = False,
    ) -> DownloadBatch:
        """Copy selected files into a new local directory.

        Logical paths are preserved so files with the same basename never
        overwrite each other. The destination must not already exist.
        """
        inventory = self.list_files(run_dir)
        requested = tuple(selectors)
        if all_files and requested:
            raise RemoteRunError("Choose file IDs or all files, not both.")
        if all_files:
            if inventory.truncated:
                raise RemoteRunError(
                    "The remote inventory was truncated; select explicit file IDs "
                    "instead of requesting all files."
                )
            items = tuple(item for item in inventory.files if item.downloadable)
        else:
            if not requested:
                raise RemoteRunError("Select at least one file to download.")
            selected = []
            seen = set()
            for selector in requested:
                item = select_file(inventory, selector)
                if item.id not in seen:
                    selected.append(item)
                    seen.add(item.id)
            items = tuple(selected)
        if not items:
            raise RemoteRunError("This run has no files within the download limit.")
        total_bytes = sum(item.size for item in items)
        if total_bytes > MAX_BATCH_DOWNLOAD_BYTES:
            raise RemoteRunError(
                f"Selected files exceed the {format_size(MAX_BATCH_DOWNLOAD_BYTES)} "
                "batch download limit. Choose a smaller set."
            )

        destination = Path(destination).expanduser()
        if destination.exists():
            raise RemoteRunError(f"Local destination already exists: {destination}")
        if not destination.parent.is_dir():
            raise RemoteRunError(
                f"Local destination directory does not exist: {destination.parent}"
            )
        staging = destination.with_name(
            f".{destination.name}.floability-remote-{uuid.uuid4().hex}.part"
        )
        destination_created = False
        try:
            staging.mkdir(mode=0o700)
            for item in items:
                relative = PurePosixPath(item.path)
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._download_item(item, inventory, target)
            destination.mkdir(mode=0o700)
            destination_created = True
            for child in staging.iterdir():
                os.replace(child, destination / child.name)
        except Exception:
            if destination_created:
                shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return DownloadBatch(files=items, destination=destination)

    def _download_item(
        self, item: RemoteFile, inventory: FileInventory, destination: Path
    ) -> None:
        """Download an already inventoried item to a non-existent path."""
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
