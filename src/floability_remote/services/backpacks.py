"""Inspect, pack, upload, and clone backpacks stored on this computer."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from ..domain.errors import RemoteRunError


MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 5_000
SKIPPED_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".ipynb_checkpoints",
    ".DS_Store",
    ".floability-remote-source.json",
}
SOURCE_RECORD = ".floability-remote-source.json"
DEFAULT_LOCAL_ROOT = "~/floability-backpacks"
MAX_WORKFLOW_FILES = 200
MAX_LISTED_DIRECTORIES = 500
MAX_READ_BYTES = 128 * 1024


@dataclass(frozen=True)
class BackpackInspection:
    path: str
    name: str
    source_type: str
    file_count: int
    total_bytes: int
    workflow_found: bool
    problems: Tuple[str, ...] = ()
    # Files inside workflow/, relative to it; candidates for the entrypoint.
    workflow_files: Tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class BackpackProvenance:
    local_path: str
    original_repository: str = ""
    original_ref: str = ""
    commit: str = ""
    uncommitted_changes: int = 0


@dataclass(frozen=True)
class CloneResult:
    path: str
    provenance: BackpackProvenance


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    path: str
    is_backpack: bool


@dataclass(frozen=True)
class DirectoryListing:
    """One folder on this computer and its visible subfolders."""

    path: str
    parent: Optional[str]
    is_backpack: bool
    directories: Tuple[DirectoryEntry, ...]
    truncated: bool


@dataclass(frozen=True)
class BackpackFile:
    path: str
    size: int


def _skipped(parts: Iterable[str]) -> bool:
    return any(part in SKIPPED_NAMES for part in parts)


def _safe_archive_path(name: str) -> Optional[PurePosixPath]:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or (len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":")
    ):
        return None
    parts = tuple(part for part in path.parts if part not in ("", "."))
    return PurePosixPath(*parts) if parts else None


def _directory_entries(root: Path) -> Iterator[Tuple[Path, PurePosixPath, int]]:
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for name in directories:
            candidate = current_path / name
            relative = candidate.relative_to(root)
            if _skipped(relative.parts):
                continue
            if candidate.is_symlink():
                raise RemoteRunError(f"Symlinks are not allowed: {relative}")
            kept_directories.append(name)
        directories[:] = sorted(kept_directories)
        for name in sorted(files):
            candidate = current_path / name
            relative = candidate.relative_to(root)
            if _skipped(relative.parts):
                continue
            if candidate.is_symlink():
                raise RemoteRunError(f"Symlinks are not allowed: {relative}")
            if not candidate.is_file():
                raise RemoteRunError(f"Only regular files are allowed: {relative}")
            yield candidate, PurePosixPath(*relative.parts), candidate.stat().st_size


def _zip_root(infos: List[zipfile.ZipInfo]) -> PurePosixPath:
    """Strip the single wrapper directory commonly used by GitHub ZIP files."""
    # Decide from files only: ZIPs made by Finder, `zip`, and GitHub also list
    # the wrapper directory itself ("project/"), which has a single part.
    paths = [
        path
        for info in infos
        if not info.is_dir()
        and (path := _safe_archive_path(info.filename)) is not None
        and not _skipped(path.parts)
    ]
    if not paths:
        return PurePosixPath()
    first = paths[0].parts[0]
    if all(len(path.parts) > 1 and path.parts[0] == first for path in paths):
        return PurePosixPath(first)
    return PurePosixPath()


def _zip_entries(
    archive: zipfile.ZipFile,
) -> Iterator[Tuple[zipfile.ZipInfo, PurePosixPath, int]]:
    infos = archive.infolist()
    wrapper = _zip_root(infos)
    for info in infos:
        path = _safe_archive_path(info.filename)
        if path is None:
            if info.filename:
                raise RemoteRunError(f"Unsafe ZIP path: {info.filename}")
            continue
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise RemoteRunError(f"Symlinks are not allowed: {path}")
        if info.is_dir():
            continue
        parts = path.parts[1:] if wrapper.parts else path.parts
        if not parts or _skipped(parts):
            continue
        yield info, PurePosixPath(*parts), info.file_size


def _inspection(path: Path) -> BackpackInspection:
    source_type = "folder" if path.is_dir() else "zip"
    problems: List[str] = []
    file_count = 0
    total_bytes = 0
    workflow_found = False
    workflow_files: List[str] = []
    try:
        if path.is_symlink():
            raise RemoteRunError(f"Symlinks are not allowed: {path}")
        if path.is_dir():
            workflow_found = (path / "workflow").is_dir()
            entries = (
                (relative, size) for _, relative, size in _directory_entries(path)
            )
        elif path.is_file() and path.suffix.lower() == ".zip":
            archive = zipfile.ZipFile(path)
            wrapper = _zip_root(archive.infolist())
            for info in archive.infolist():
                member = _safe_archive_path(info.filename)
                if member is None:
                    continue
                parts = member.parts[1:] if wrapper.parts else member.parts
                if parts and parts[0] == "workflow" and not _skipped(parts):
                    workflow_found = True
                    break
            entries = ((relative, size) for _, relative, size in _zip_entries(archive))
        elif path.exists():
            problems.append("The local backpack must be a folder or ZIP file.")
            entries = iter(())
        else:
            problems.append(f"Local backpack does not exist: {path}")
            entries = iter(())

        try:
            for relative, size in entries:
                file_count += 1
                total_bytes += size
                in_workflow = bool(relative.parts) and relative.parts[0] == "workflow"
                workflow_found = workflow_found or in_workflow
                if (
                    in_workflow
                    and len(relative.parts) > 1
                    and len(workflow_files) < MAX_WORKFLOW_FILES
                ):
                    workflow_files.append("/".join(relative.parts[1:]))
                if file_count > MAX_FILES:
                    problems.append(f"Backpack contains more than {MAX_FILES:,} files.")
                    break
                if total_bytes > MAX_BYTES:
                    problems.append("Backpack is larger than 200 MB.")
                    break
        finally:
            if (
                path.is_file()
                and path.suffix.lower() == ".zip"
                and "archive" in locals()
            ):
                archive.close()
    except (OSError, zipfile.BadZipFile, RemoteRunError) as error:
        problems.append(str(error))

    if not workflow_found and not any("does not exist" in item for item in problems):
        problems.append("Backpack must contain a workflow/ folder.")
    return BackpackInspection(
        path=str(path),
        name=path.stem if path.suffix.lower() == ".zip" else path.name,
        source_type=source_type,
        file_count=file_count,
        total_bytes=total_bytes,
        workflow_found=workflow_found,
        problems=tuple(dict.fromkeys(problems)),
        workflow_files=tuple(sorted(workflow_files)),
    )


def inspect(path: Union[str, Path]) -> BackpackInspection:
    """Return a local backpack summary and every problem found."""
    return _inspection(Path(path).expanduser().resolve())


def list_files(path: Union[str, Path]) -> Tuple[BackpackFile, ...]:
    """List every included regular file using backpack-relative POSIX paths."""
    resolved, report = _require_valid(path)
    if report.source_type == "folder":
        entries = (
            BackpackFile(relative.as_posix(), size)
            for _, relative, size in _directory_entries(resolved)
        )
        return tuple(entries)
    with zipfile.ZipFile(resolved) as archive:
        return tuple(
            BackpackFile(relative.as_posix(), size)
            for _, relative, size in _zip_entries(archive)
        )


def read_text(path: Union[str, Path], relative_path: str) -> str:
    """Read one small UTF-8 backpack file after applying archive safety rules."""
    requested = _safe_archive_path(relative_path)
    if requested is None or requested.as_posix() != relative_path.replace("\\", "/"):
        raise RemoteRunError("File path must be relative and cannot contain '..'.")
    resolved, report = _require_valid(path)

    if report.source_type == "folder":
        for source, relative, size in _directory_entries(resolved):
            if relative != requested:
                continue
            if size > MAX_READ_BYTES:
                raise RemoteRunError("File is larger than the 128 KB preview limit.")
            data = source.read_bytes()
            break
        else:
            raise RemoteRunError(f"Backpack file was not found: {relative_path}")
    else:
        with zipfile.ZipFile(resolved) as archive:
            for info, relative, size in _zip_entries(archive):
                if relative != requested:
                    continue
                if size > MAX_READ_BYTES:
                    raise RemoteRunError(
                        "File is larger than the 128 KB preview limit."
                    )
                data = archive.read(info)
                break
            else:
                raise RemoteRunError(f"Backpack file was not found: {relative_path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RemoteRunError(
            "File is not UTF-8 text and cannot be previewed."
        ) from error


def fingerprint(path: Union[str, Path]) -> str:
    """Hash all included paths and contents to detect changes after inspection."""
    resolved, report = _require_valid(path)
    digest = hashlib.sha256()

    def update(relative: PurePosixPath, size: int, stream: BinaryIO) -> None:
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

    if report.source_type == "folder":
        for source, relative, size in _directory_entries(resolved):
            with source.open("rb") as stream:
                update(relative, size, stream)
    else:
        with zipfile.ZipFile(resolved) as archive:
            for info, relative, size in _zip_entries(archive):
                with archive.open(info) as stream:
                    update(relative, size, stream)
    return digest.hexdigest()


def _require_valid(path: Union[str, Path]) -> Tuple[Path, BackpackInspection]:
    resolved = Path(path).expanduser().resolve()
    report = _inspection(resolved)
    if not report.valid:
        raise RemoteRunError(" ".join(report.problems))
    return resolved, report


def pack(path: Union[str, Path]) -> BinaryIO:
    """Create a validated tar stream positioned at byte zero."""
    resolved, report = _require_valid(path)
    stream = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b")
    with tarfile.open(fileobj=stream, mode="w") as archive:
        workflow = tarfile.TarInfo("workflow")
        workflow.type = tarfile.DIRTYPE
        workflow.mode = 0o755
        archive.addfile(workflow)
        if report.source_type == "folder":
            for source, relative, _ in _directory_entries(resolved):
                archive.add(source, arcname=relative.as_posix(), recursive=False)
        else:
            with zipfile.ZipFile(resolved) as zipped:
                for info, relative, _ in _zip_entries(zipped):
                    tar_info = tarfile.TarInfo(relative.as_posix())
                    tar_info.size = info.file_size
                    tar_info.mode = (
                        0o755 if (info.external_attr >> 16) & 0o111 else 0o644
                    )
                    with zipped.open(info) as source:
                        archive.addfile(tar_info, source)
    stream.seek(0)
    return stream


def upload(session, stream: BinaryIO, run_dir: str):
    """Upload one tar stream into a fresh remote run directory."""
    from ..remote import scripts as remote_scripts

    return session.run_stream(
        remote_scripts.UPLOAD_BACKPACK,
        (run_dir,),
        stream,
    )


def provenance(path: Union[str, Path]) -> BackpackProvenance:
    resolved = Path(path).expanduser().resolve()
    record: Dict[str, object] = {}
    record_paths = (
        [resolved / ".git" / SOURCE_RECORD, resolved / SOURCE_RECORD]
        if resolved.is_dir()
        else []
    )
    for record_path in record_paths:
        if record_path.is_file():
            try:
                record = json.loads(record_path.read_text())
            except (OSError, ValueError):
                record = {}
            break
    commit = ""
    edits = 0
    if (resolved / ".git").exists():
        commit_result = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
        if commit_result.returncode == 0:
            commit = commit_result.stdout.strip()
        status_result = subprocess.run(
            ["git", "-C", str(resolved), "status", "--porcelain"],
            text=True,
            capture_output=True,
            check=False,
        )
        if status_result.returncode == 0:
            edits = len([line for line in status_result.stdout.splitlines() if line])
    return BackpackProvenance(
        local_path=str(resolved),
        original_repository=str(record.get("original_repository", "")),
        original_ref=str(record.get("original_ref", "")),
        commit=commit,
        uncommitted_changes=edits,
    )


def _unique_destination(root: Path, name: str) -> Path:
    candidate = root / name
    number = 2
    while candidate.exists():
        candidate = root / f"{name}-{number}"
        number += 1
    return candidate


def _repository_name(repository: str) -> str:
    path = urllib.parse.urlparse(repository).path.rstrip("/")
    name = Path(path).name
    if name.endswith(".git"):
        name = name[:-4]
    if not name or name in (".", ".."):
        raise RemoteRunError(
            "Could not determine a folder name from the repository URL."
        )
    return name


def _write_source_record(path: Path, repository: str, ref: str) -> None:
    parent = path / ".git" if (path / ".git").is_dir() else path
    record = {"original_repository": repository, "original_ref": ref}
    (parent / SOURCE_RECORD).write_text(json.dumps(record, indent=2) + "\n")


def _extract_into(source: Path, destination: Path) -> None:
    """Write a validated backpack's files into an existing empty folder."""
    with pack(source) as stream:
        with tarfile.open(fileobj=stream, mode="r") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                output = destination.joinpath(*PurePosixPath(member.name).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                data = archive.extractfile(member)
                if data is None:
                    raise RemoteRunError(
                        f"Could not read {member.name} from the archive."
                    )
                with data, output.open("xb") as target:
                    shutil.copyfileobj(data, target)
                if member.mode & 0o111:
                    output.chmod(0o755)


def _github_archive(repository: str, ref: str, destination: Path) -> None:
    parsed = urllib.parse.urlparse(repository)
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if parsed.hostname not in {"github.com", "www.github.com"} or len(parts) != 2:
        raise RemoteRunError(
            "Git is unavailable; ZIP fallback supports public GitHub repositories only."
        )
    refs = [ref] if ref else ["main", "master"]
    last_error: Optional[Exception] = None
    for candidate_ref in refs:
        url = f"https://github.com/{parts[0]}/{parts[1]}/archive/{urllib.parse.quote(candidate_ref, safe='')}.zip"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise RemoteRunError("Repository archive is larger than 200 MB.")
            with tempfile.NamedTemporaryFile(suffix=".zip") as temporary:
                temporary.write(data)
                temporary.flush()
                _extract_into(Path(temporary.name), destination)
            return
        except Exception as error:  # try main, then master
            last_error = error
    raise RemoteRunError(f"Could not download the GitHub repository: {last_error}")


def clone(
    repository: str, ref: str = "", destination: Union[str, Path] = ""
) -> CloneResult:
    """Clone/download a repository locally and record its origin."""
    if (
        not repository.strip()
        or repository.startswith("-")
        or any(ord(character) < 32 for character in repository)
    ):
        raise RemoteRunError("Repository is not a valid Git repository URL.")
    if ref.startswith("-") or any(ord(character) < 32 for character in ref):
        raise RemoteRunError(
            "Git ref cannot begin with '-' or contain control characters."
        )
    name = _repository_name(repository)
    if destination:
        target = Path(destination).expanduser().resolve()
        if target.exists():
            raise RemoteRunError(f"Destination already exists: {target}")
    else:
        root = Path(DEFAULT_LOCAL_ROOT).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        target = _unique_destination(root, name)
    target.parent.mkdir(parents=True, exist_ok=True)

    git = shutil.which("git")
    try:
        if git:
            result = subprocess.run(
                [git, "clone", "--", repository, str(target)],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise RemoteRunError(result.stderr.strip() or "Local git clone failed.")
            if ref:
                result = subprocess.run(
                    [git, "-C", str(target), "checkout", "--detach", ref],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    raise RemoteRunError(
                        result.stderr.strip() or f"Could not check out {ref}."
                    )
        else:
            target.mkdir()
            _github_archive(repository, ref, target)
        _write_source_record(target, repository, ref)
        report = inspect(target)
        if not report.valid:
            raise RemoteRunError(
                "Cloned repository is not a valid backpack: "
                + " ".join(report.problems)
            )
        return CloneResult(str(target), provenance(target))
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _looks_like_backpack(path: Path) -> bool:
    try:
        workflow = path / "workflow"
        return workflow.is_dir() and not workflow.is_symlink()
    except OSError:
        return False


def list_directories(path: Union[str, Path] = "") -> DirectoryListing:
    """List visible subfolders of `path` (home by default) for folder picking.

    Hidden folders, generated folders, and symlinks are left out. Folders that
    contain a workflow/ directory are marked as backpacks.
    """
    current = (Path(path).expanduser() if str(path).strip() else Path.home()).resolve()
    if not current.is_dir():
        raise RemoteRunError(f"Folder does not exist: {current}")
    try:
        children = sorted(current.iterdir(), key=lambda item: item.name.lower())
    except OSError as error:
        raise RemoteRunError(f"Cannot open folder {current}: {error.strerror or error}")

    entries: List[DirectoryEntry] = []
    truncated = False
    for child in children:
        if child.name.startswith(".") or child.name in SKIPPED_NAMES:
            continue
        try:
            if child.is_symlink() or not child.is_dir():
                continue
        except OSError:
            continue
        if len(entries) >= MAX_LISTED_DIRECTORIES:
            truncated = True
            break
        entries.append(
            DirectoryEntry(child.name, str(child), _looks_like_backpack(child))
        )
    return DirectoryListing(
        path=str(current),
        parent=str(current.parent) if current.parent != current else None,
        is_backpack=_looks_like_backpack(current),
        directories=tuple(entries),
        truncated=truncated,
    )


def discard_changes(path: Union[str, Path]) -> BackpackProvenance:
    """Discard modifications to tracked files in one local Git clone."""
    resolved = Path(path).expanduser().resolve()
    if not (resolved / ".git").exists():
        raise RemoteRunError("This local backpack is not a Git clone.")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(resolved),
            "restore",
            "--source",
            "HEAD",
            "--staged",
            "--worktree",
            "--",
            ".",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RemoteRunError(result.stderr.strip() or "Could not discard changes.")
    return provenance(resolved)


def vscode_available() -> bool:
    """Whether VS Code's `code` command is on this process's PATH."""
    return shutil.which("code") is not None


def open_local(path: Union[str, Path], vscode: bool = False) -> None:
    """Open a local backpack in the platform file browser or VS Code."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise RemoteRunError(f"Local backpack folder does not exist: {resolved}")
    if vscode:
        command = shutil.which("code")
        if not command:
            raise RemoteRunError("The VS Code 'code' command was not found in PATH.")
        arguments = [command, str(resolved)]
    elif os.name == "nt":
        os.startfile(str(resolved))  # type: ignore[attr-defined]
        return
    elif shutil.which("open"):
        arguments = [shutil.which("open"), str(resolved)]
    elif shutil.which("xdg-open"):
        arguments = [shutil.which("xdg-open"), str(resolved)]
    else:
        raise RemoteRunError("No desktop folder opener was found.")
    subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
