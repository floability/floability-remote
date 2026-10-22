"""Daemon-scoped backpack snapshots and validated deployment plans."""

from dataclasses import dataclass
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
import time
from typing import Dict, Tuple

from ..domain.config import BackpackSource, RunConfig
from ..domain.errors import RemoteRunError
from ..services.backpacks import (
    BackpackInspection,
    clone,
    fingerprint,
    inspect,
    list_files,
    read_text,
)


ENTRYPOINT_SUFFIXES = (".ipynb", ".py", ".sh")


class PlanningError(RemoteRunError):
    """An inspected source or deployment plan is missing or stale."""


@dataclass(frozen=True)
class SourceSnapshot:
    id: str
    requested: BackpackSource
    effective: BackpackSource
    inspection_path: str
    inspection: BackpackInspection
    fingerprint: str
    commit: str
    created_at: float

    @property
    def entrypoints(self) -> Tuple[str, ...]:
        return tuple(
            name
            for name in self.inspection.workflow_files
            if name.lower().endswith(ENTRYPOINT_SUFFIXES)
        )


@dataclass(frozen=True)
class DeploymentPlan:
    id: str
    source_id: str
    config: RunConfig
    source_fingerprint: str
    connection_started_at: float
    created_at: float


class PlanningStore:
    """Own source snapshots and plans for the lifetime of one local daemon."""

    def __init__(self, root: str = ""):
        self.root = Path(root) if root else Path(tempfile.mkdtemp(prefix="fr-plans-"))
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._remove_root = not bool(root)
        self._sources: Dict[str, SourceSnapshot] = {}
        self._plans: Dict[str, DeploymentPlan] = {}
        self._lock = threading.RLock()

    def inspect_source(self, source: BackpackSource) -> SourceSnapshot:
        source_id = f"source-{secrets.token_hex(8)}"
        requested = source
        commit = ""
        if source.source_type == "git":
            destination = self.root / source_id
            result = clone(source.repository, source.ref, destination)
            path = result.path
            commit = result.provenance.commit
            effective = BackpackSource(
                source_type="git",
                repository=source.repository,
                ref=commit or source.ref,
            )
        elif source.source_type in ("folder", "zip"):
            path = str(Path(source.local_path).expanduser().resolve())
            effective = BackpackSource(
                source_type=source.source_type,
                local_path=path,
            )
        else:
            raise PlanningError("Backpack source type must be git, folder, or zip.")

        report = inspect(path)
        if not report.valid:
            if source.source_type == "git":
                shutil.rmtree(path, ignore_errors=True)
            raise PlanningError("Backpack is not valid: " + " ".join(report.problems))
        if (
            report.source_type != effective.source_type
            and effective.source_type != "git"
        ):
            raise PlanningError(
                f"Backpack path is a {report.source_type}, not {effective.source_type}."
            )
        snapshot = SourceSnapshot(
            id=source_id,
            requested=requested,
            effective=effective,
            inspection_path=report.path,
            inspection=report,
            fingerprint=fingerprint(report.path),
            commit=commit,
            created_at=time.time(),
        )
        with self._lock:
            self._sources[source_id] = snapshot
        return snapshot

    def source(self, source_id: str) -> SourceSnapshot:
        with self._lock:
            try:
                return self._sources[source_id]
            except KeyError as error:
                raise PlanningError(
                    "Backpack source was not found. Inspect it again."
                ) from error

    def source_is_fresh(self, source_id: str) -> bool:
        snapshot = self.source(source_id)
        try:
            return fingerprint(snapshot.inspection_path) == snapshot.fingerprint
        except RemoteRunError:
            return False

    def files(self, source_id: str):
        snapshot = self.source(source_id)
        if not self.source_is_fresh(source_id):
            raise PlanningError(
                "Backpack source changed after inspection. Inspect it again."
            )
        return list_files(snapshot.inspection_path)

    def read_file(self, source_id: str, path: str) -> str:
        snapshot = self.source(source_id)
        if not self.source_is_fresh(source_id):
            raise PlanningError(
                "Backpack source changed after inspection. Inspect it again."
            )
        return read_text(snapshot.inspection_path, path)

    def create_plan(
        self,
        source_id: str,
        config: RunConfig,
        connection_started_at: float,
    ) -> DeploymentPlan:
        snapshot = self.source(source_id)
        if not self.source_is_fresh(source_id):
            raise PlanningError(
                "Backpack source changed after inspection. Inspect it again."
            )
        plan = DeploymentPlan(
            id=f"plan-{secrets.token_hex(8)}",
            source_id=source_id,
            config=config,
            source_fingerprint=snapshot.fingerprint,
            connection_started_at=connection_started_at,
            created_at=time.time(),
        )
        with self._lock:
            self._plans[plan.id] = plan
        return plan

    def plan(self, plan_id: str) -> DeploymentPlan:
        with self._lock:
            try:
                return self._plans[plan_id]
            except KeyError as error:
                raise PlanningError(
                    "Deployment plan was not found. Validate it again."
                ) from error

    def require_fresh_plan(
        self, plan_id: str, target: str, connection_started_at: float
    ) -> DeploymentPlan:
        plan = self.plan(plan_id)
        if (
            plan.config.connection.target != target
            or plan.connection_started_at != connection_started_at
        ):
            raise PlanningError(
                "The SSH connection changed after validation. Validate the deployment again."
            )
        if not self.source_is_fresh(plan.source_id):
            raise PlanningError(
                "The backpack changed after validation. Inspect and validate it again."
            )
        return plan

    def close(self) -> None:
        with self._lock:
            self._sources.clear()
            self._plans.clear()
        if self._remove_root:
            shutil.rmtree(self.root, ignore_errors=True)
