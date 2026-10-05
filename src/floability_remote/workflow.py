"""Shared orchestration for remote `floability run` and `floability execute`."""

import subprocess
import sys
import time
import uuid
from typing import Optional

from . import remote_scripts
from .environment import ensure_environment
from .errors import RemoteRunError
from .models import RemoteProbe, RemoteWorkspace
from .output import (
    Reporter,
    concise_floability_progress,
    local_jupyter_url,
    marker_values,
    parse_jupyter_connection,
)
from .ssh import SSHSession, choose_local_port, terminate_process


class RemoteWorkflow:
    """Prepare a remote workspace and run one Floability command in it."""

    def __init__(self, args):
        self.args = args
        self.reporter = Reporter(args.verbose)
        self.session = SSHSession(
            target=args.target,
            identity_file=args.identity_file,
            ssh_options=args.ssh_option,
        )
        self.workspace: Optional[RemoteWorkspace] = None
        self.remote_process: Optional[subprocess.Popen] = None
        self.tunnel_process: Optional[subprocess.Popen] = None

    @property
    def total_steps(self) -> int:
        return 6 if self.args.command == "run" else 5

    def start(self) -> int:
        interrupted = False
        try:
            self._connect()
            probe = self._prepare_environment()
            self.workspace = self._clone_backpack()
            self._launch(probe)
            status = self._monitor()
            if status != 0:
                raise RemoteRunError(
                    f"Remote Floability {self.args.command} exited with status {status}. "
                    f"Review {self._remote_log_path()}."
                )
            self._report_success()
            return 0
        except KeyboardInterrupt:
            interrupted = True
            print("\n[remote] Interrupted. Asking remote Floability to clean up...")
            self._cleanup_remote_process()
            return 130
        except Exception:
            self._cleanup_remote_process()
            raise
        finally:
            terminate_process(self.tunnel_process)
            self._finish_local_remote_process(interrupted)
            self.session.close()
            self._report_retained_workspace()

    def _connect(self) -> None:
        self.reporter.step(1, self.total_steps, f"Connecting to {self.args.target}...")
        self.session.start()
        self.reporter.detail("SSH connection established.")

    def _prepare_environment(self) -> RemoteProbe:
        self.reporter.step(
            2,
            self.total_steps,
            "Checking remote tools and Floability environment...",
        )
        return ensure_environment(self.session, self.args, self.reporter)

    def _clone_backpack(self) -> RemoteWorkspace:
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.reporter.step(
            3,
            self.total_steps,
            f"Cloning backpack from {self.args.backpack}...",
        )
        result = self.session.run_script(
            remote_scripts.CLONE_BACKPACK,
            (self.args.remote_root, run_id, self.args.backpack, self.args.ref),
            show_output=self.args.verbose,
        )
        values = marker_values(result.stdout or "")
        run_dir = values.get("__FLOABILITY_REMOTE_RUN_DIR__")
        backpack_dir = values.get("__FLOABILITY_REMOTE_BACKPACK__")
        if not run_dir or not backpack_dir:
            raise RemoteRunError("Remote clone completed without returning its paths.")

        self.reporter.detail(f"Backpack ready: {backpack_dir}")
        return RemoteWorkspace(run_id, run_dir, backpack_dir)

    def _launch(self, probe: RemoteProbe) -> None:
        if not probe.conda or not probe.env_prefix or not self.workspace:
            raise RemoteRunError("The remote execution environment is incomplete.")

        self.reporter.step(
            4,
            self.total_steps,
            f"Starting Floability {self.args.command} with {self.args.batch_type}...",
        )
        self.remote_process = self.session.start_script(
            remote_scripts.LAUNCH_FLOABILITY,
            (
                probe.conda,
                probe.env_prefix,
                self.workspace.run_dir,
                self.workspace.backpack_dir,
                self.args.command,
                self.args.batch_type,
                str(self.args.jupyter_port),
                self.args.entrypoint,
            ),
        )

    def _monitor(self) -> int:
        if not self.remote_process or self.remote_process.stdout is None:
            raise RemoteRunError("The remote Floability process did not start correctly.")

        jupyter_found = False
        local_port = None
        if self.args.command == "run":
            local_port = choose_local_port(self.args.local_port)

        for line in self.remote_process.stdout:
            self.reporter.raw(line)
            if not self.args.verbose:
                progress = concise_floability_progress(line)
                if progress:
                    self.reporter.detail_once(f"{progress}...")

            if self.args.command != "run" or jupyter_found:
                continue
            connection = parse_jupyter_connection(line)
            if not connection:
                continue

            jupyter_found = True
            assert local_port is not None
            self.reporter.step(
                5,
                self.total_steps,
                f"Jupyter is ready; opening the SSH tunnel on "
                f"127.0.0.1:{local_port}...",
            )
            self.tunnel_process = self.session.start_tunnel(
                local_port, connection.remote_port
            )
            self.reporter.ready(local_jupyter_url(connection, local_port))

        status = self.remote_process.wait()
        if self.args.command == "run" and status == 0 and not jupyter_found:
            raise RemoteRunError(
                "Remote Floability ended without reporting a Jupyter connection."
            )
        return status

    def _report_success(self) -> None:
        if self.args.command == "run":
            message = "Remote Floability run finished cleanly."
        else:
            message = "Remote Floability execution completed successfully."
        self.reporter.step(self.total_steps, self.total_steps, message)

    def _cleanup_remote_process(self) -> None:
        if not self.workspace or not self._remote_process_is_running():
            return
        if not stop_remote_floability(
            self.session,
            self.workspace.run_dir,
            verbose=self.args.verbose,
        ):
            print(
                f"[remote] WARNING: cleanup was not confirmed. Inspect "
                f"{self.workspace.run_dir} on {self.args.target}.",
                file=sys.stderr,
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
        self.reporter.detail(f"Remote run directory retained: {self.workspace.run_dir}")
        self.reporter.detail(f"Remote command log: {self._remote_log_path()}")
        if self.args.command == "execute":
            self.reporter.detail(
                f"Remote backpack and outputs: {self.workspace.backpack_dir}"
            )

    def _remote_log_path(self) -> str:
        if not self.workspace:
            return "the remote command log"
        return f"{self.workspace.run_dir}/{self.args.command}-command.log"


def stop_remote_floability(
    session: SSHSession, run_dir: str, *, verbose: bool = False
) -> bool:
    """Ask Floability to clean up, escalating from SIGINT to SIGTERM."""
    for signal_name, wait_seconds in (("INT", 45), ("TERM", 15)):
        result = session.run_script(
            remote_scripts.STOP_FLOABILITY,
            (run_dir, signal_name, str(wait_seconds)),
            check=False,
            show_output=verbose,
        )
        if result.returncode == 0:
            return True
        if result.returncode != 3:
            return False
    return False
