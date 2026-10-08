"""Shared orchestration for remote `floability run` and `floability execute`."""

import subprocess
import threading
from typing import Optional, Tuple

from . import remote_scripts
from .config import RunConfig, floability_option_arguments
from .environment import ensure_environment
from .errors import RemoteRunError
from .events import Emitter, EventKind, EventSink, Redactor
from .interaction import CancelToken, Confirm, decline
from .models import RemoteProbe, RemoteWorkspace
from .output import concise_floability_progress, local_jupyter_url, parse_jupyter_connection
from .ssh import SSHSession, choose_local_port, terminate_process
from .workspace import create_workspace


INTERRUPTED_MESSAGE = "Interrupted. Asking remote Floability to clean up..."
CANCEL_MESSAGE = "Cancellation requested. Asking remote Floability to clean up..."
STOP_SESSION_MESSAGE = "Stopping the interactive session. Asking remote Floability to clean up..."


class _Cancelled(Exception):
    """Internal signal that the cancel token stopped the workflow."""


class RemoteWorkflow:
    """Prepare a remote workspace and run one Floability command in it.

    The CLI and web API both drive this class. It reports progress only through
    `sink`, asks questions only through `confirm`, and can be stopped from
    another thread through `cancel_token`.
    """

    def __init__(
        self,
        config: RunConfig,
        sink: EventSink,
        *,
        logs_visible: bool = False,
        confirm: Confirm = decline,
        cancel_token: Optional[CancelToken] = None,
        redactor: Optional[Redactor] = None,
        session: Optional[SSHSession] = None,
    ):
        """`session`, when given, is an already-authenticated connection owned
        by the caller; the workflow reuses it and leaves it open afterwards."""
        self.config = config
        self.emitter = Emitter(sink, logs_visible=logs_visible)
        self.confirm = confirm
        self.cancel_token = cancel_token or CancelToken()
        self.redactor = redactor or Redactor()
        self._owns_session = session is None
        self.session = session or SSHSession(
            target=config.connection.target,
            identity_file=config.connection.identity_file,
            ssh_options=config.connection.ssh_options,
        )
        self.workspace: Optional[RemoteWorkspace] = None
        self.remote_process: Optional[subprocess.Popen] = None
        self.tunnel_process: Optional[subprocess.Popen] = None
        self.jupyter_url: Optional[str] = None
        self._tunnel_ports: Optional[Tuple[int, int]] = None
        self._lock = threading.Lock()

    @property
    def total_steps(self) -> int:
        return 6 if self.config.mode == "run" else 5

    def start(self) -> int:
        outcome = EventKind.FAILED
        error: Optional[BaseException] = None
        self.cancel_token.on_cancel(self._on_cancel)
        try:
            self._connect()
            probe = self._prepare_environment()
            self.workspace = self._clone_backpack()
            self._launch(probe)
            status = self._monitor()
            self._checkpoint()
            if status != 0:
                raise RemoteRunError(
                    f"Remote Floability {self.config.mode} exited with status {status}. "
                    f"Review {self._remote_log_path()}."
                )
            self._report_success()
            outcome = EventKind.COMPLETED
            return 0
        except KeyboardInterrupt:
            outcome = EventKind.CANCELLED
            self.emitter.emit(EventKind.CANCELLING, INTERRUPTED_MESSAGE)
            self._cleanup_remote_process()
            return 130
        except _Cancelled:
            outcome = EventKind.CANCELLED
            return 130
        except Exception as caught:
            if self.cancel_token.cancelled:
                outcome = EventKind.CANCELLED
                return 130
            error = caught
            self._cleanup_remote_process()
            raise
        finally:
            terminate_process(self.tunnel_process)
            self._finish_local_remote_process(outcome == EventKind.CANCELLED)
            if self._owns_session:
                self.session.close()
            elif self._tunnel_ports is not None:
                # The shared master outlives this run; release its forward.
                self.session.cancel_tunnel(*self._tunnel_ports)
            self._report_retained_workspace()
            self._report_outcome(outcome, error)

    def cancel(self) -> None:
        """Request cancellation; safe to call from any thread."""
        self.cancel_token.cancel()

    def _checkpoint(self) -> None:
        if self.cancel_token.cancelled:
            raise _Cancelled()

    def _on_cancel(self) -> None:
        message = STOP_SESSION_MESSAGE if self.jupyter_url else CANCEL_MESSAGE
        self.emitter.emit(EventKind.CANCELLING, message)
        self._cleanup_remote_process()

    def _connect(self) -> None:
        self._checkpoint()
        self.emitter.step(1, self.total_steps, f"Connecting to {self.config.connection.target}...")
        if self.session.started:
            self.emitter.detail("Reusing the open SSH connection.")
            return
        self.session.start()
        self.emitter.detail("SSH connection established.")

    def _prepare_environment(self) -> RemoteProbe:
        self._checkpoint()
        self.emitter.step(
            2,
            self.total_steps,
            "Checking remote tools and Floability environment...",
        )
        return ensure_environment(
            self.session, self.config.environment, self.emitter, self.confirm
        )

    def _clone_backpack(self) -> RemoteWorkspace:
        self._checkpoint()
        self.emitter.step(
            3,
            self.total_steps,
            f"Cloning backpack from {self.config.backpack.repository}...",
        )
        return create_workspace(
            self.session, self.config.backpack, self.config.remote_root, self.emitter
        )

    def _launch(self, probe: RemoteProbe) -> None:
        if not probe.conda or not probe.env_prefix or not self.workspace:
            raise RemoteRunError("The remote execution environment is incomplete.")

        with self._lock:
            self._checkpoint()
            self.emitter.step(
                4,
                self.total_steps,
                f"Starting Floability {self.config.mode} with {self.config.batch_type}...",
            )
            self.remote_process = self.session.start_script(
                remote_scripts.LAUNCH_FLOABILITY,
                (
                    probe.conda,
                    probe.env_prefix,
                    self.workspace.run_dir,
                    self.workspace.backpack_dir,
                    self.config.mode,
                    self.config.batch_type,
                    str(self.config.jupyter_port),
                    self.config.entrypoint,
                    self.config.base_dir,
                    self.config.data_cache_dir,
                    *floability_option_arguments(self.config.floability_options),
                ),
            )

    def _monitor(self) -> int:
        if not self.remote_process or self.remote_process.stdout is None:
            raise RemoteRunError("The remote Floability process did not start correctly.")

        interactive = self.config.mode == "run"
        jupyter_found = False
        local_port = None
        if interactive:
            local_port = choose_local_port(self.config.local_port)

        for line in self.remote_process.stdout:
            connection = None
            if interactive and not jupyter_found:
                connection = parse_jupyter_connection(line)
                if connection:
                    # Register the token before the line reaches any sink.
                    self.redactor.add(connection.token)

            self.emitter.log(line)
            progress = concise_floability_progress(line)
            if progress:
                self.emitter.progress(f"{progress}...")

            if not connection:
                continue

            jupyter_found = True
            assert local_port is not None
            self.emitter.step(
                5,
                self.total_steps,
                f"Jupyter is ready; opening the SSH tunnel on "
                f"127.0.0.1:{local_port}...",
            )
            self._tunnel_ports = (local_port, connection.remote_port)
            self.tunnel_process = self.session.start_tunnel(
                local_port, connection.remote_port
            )
            self.jupyter_url = local_jupyter_url(connection, local_port)
            self.emitter.emit(
                EventKind.READY,
                "Jupyter is ready.",
                url=self.jupyter_url,
                local_port=local_port,
            )

        status = self.remote_process.wait()
        if (
            interactive
            and status == 0
            and not jupyter_found
            and not self.cancel_token.cancelled
        ):
            raise RemoteRunError(
                "Remote Floability ended without reporting a Jupyter connection."
            )
        return status

    def _report_success(self) -> None:
        if self.config.mode == "run":
            message = "Remote Floability run finished cleanly."
        else:
            message = "Remote Floability execution completed successfully."
        self.emitter.step(self.total_steps, self.total_steps, message)

    def _cleanup_remote_process(self) -> None:
        with self._lock:
            workspace = self.workspace
            running = self._remote_process_is_running()
        if not workspace or not running:
            return
        if not stop_remote_floability(self.session, workspace.run_dir, self.emitter):
            self.emitter.warning(
                f"cleanup was not confirmed. Inspect "
                f"{workspace.run_dir} on {self.config.connection.target}.",
                run_dir=workspace.run_dir,
            )

    def _remote_process_is_running(self) -> bool:
        return self.remote_process is not None and self.remote_process.poll() is None

    def _finish_local_remote_process(self, interrupted: bool) -> None:
        if not self.remote_process or self.remote_process.poll() is not None:
            return
        try:
            self.remote_process.wait(timeout=5 if interrupted else 1)
        except subprocess.TimeoutExpired:
            terminate_process(self.remote_process)

    def _report_retained_workspace(self) -> None:
        if not self.workspace:
            return
        self.emitter.detail(
            f"Remote run directory retained: {self.workspace.run_dir}",
            run_dir=self.workspace.run_dir,
        )
        self.emitter.detail(
            f"Remote command log: {self._remote_log_path()}",
            log_path=self._remote_log_path(),
        )
        if self.config.mode == "execute":
            self.emitter.detail(
                f"Remote backpack and outputs: {self.workspace.backpack_dir}",
                backpack_dir=self.workspace.backpack_dir,
            )

    def _report_outcome(self, outcome: str, error: Optional[BaseException]) -> None:
        data = {}
        if self.workspace:
            data = {
                "run_dir": self.workspace.run_dir,
                "backpack_dir": self.workspace.backpack_dir,
                "log_path": self._remote_log_path(),
            }
        if outcome == EventKind.COMPLETED:
            message = f"Remote Floability {self.config.mode} completed."
        elif outcome == EventKind.CANCELLED and self.jupyter_url:
            message = "The interactive session was stopped."
        elif outcome == EventKind.CANCELLED:
            message = f"Remote Floability {self.config.mode} was cancelled."
        else:
            message = str(error) if error else f"Remote Floability {self.config.mode} failed."
        self.emitter.emit(outcome, message, **data)

    def _remote_log_path(self) -> str:
        if not self.workspace:
            return "the remote command log"
        return f"{self.workspace.run_dir}/{self.config.mode}-command.log"


def stop_remote_floability(session: SSHSession, run_dir: str, emitter: Emitter) -> bool:
    """Ask Floability to clean up, escalating from SIGINT to SIGTERM."""
    for signal_name, wait_seconds in (("INT", 45), ("TERM", 15)):
        result = session.run_script(
            remote_scripts.STOP_FLOABILITY,
            (run_dir, signal_name, str(wait_seconds)),
            check=False,
            on_output=emitter.log_block,
        )
        if result.returncode == 0:
            return True
        if result.returncode != 3:
            return False
    return False
