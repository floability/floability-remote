"""OpenSSH transport and local tunnel management."""

import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from .errors import RemoteRunError
from .output import non_marker_output


def shell_command(program: str, arguments: Sequence[str]) -> str:
    """Safely quote the one command string OpenSSH sends to the remote shell."""
    return " ".join(shlex.quote(item) for item in (program, *arguments))


def choose_local_port(requested: Optional[int]) -> int:
    if requested is not None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", requested))
            except OSError as error:
                raise RemoteRunError(
                    f"Local port {requested} is unavailable: {error}"
                ) from error
        return requested

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def terminate_process(process: Optional[subprocess.Popen]) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


PROMPTLESS_MASTER_OPTIONS = (
    "ServerAliveInterval=30",
    "ServerAliveCountMax=4",
    "ConnectTimeout=20",
)


class SSHSession:
    """One authenticated OpenSSH master reused by commands and tunnels.

    By default ssh prompts on the user's terminal, as the CLI expects. When
    `askpass_env` is given (see `askpass.AskPassBroker.env`), every prompt is
    sent to that AskPass helper instead, the master is detached from the
    terminal, keepalives are enabled, and commands that reuse the master run
    with BatchMode so they can never fall back to an interactive login.
    """

    def __init__(
        self,
        target: str,
        identity_file: Optional[str] = None,
        ssh_options: Sequence[str] = (),
        *,
        askpass_env: Optional[Mapping[str, str]] = None,
        control_persist: str = "300",
    ):
        ssh = shutil.which("ssh")
        if not ssh:
            raise RemoteRunError("OpenSSH client 'ssh' was not found in PATH.")

        self.ssh = ssh
        self.target = target
        self.identity_file = identity_file
        self.ssh_options = tuple(ssh_options)
        self.askpass_env = dict(askpass_env) if askpass_env is not None else None
        self.control_persist = control_persist
        socket_name = f"fr-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"
        self.control_path = str(Path(tempfile.gettempdir()) / socket_name)
        self.started = False

    @property
    def promptless(self) -> bool:
        return self.askpass_env is not None

    def _options(self) -> list:
        options = []
        if self.identity_file:
            options.extend(["-i", self.identity_file])
        for option in self.ssh_options:
            options.extend(["-o", option])
        return options

    def _base(self) -> list:
        base = [self.ssh, *self._options(), "-S", self.control_path]
        if self.promptless:
            base.extend(["-o", "BatchMode=yes"])
        return base

    def start(self) -> None:
        command = [
            self.ssh,
            *self._options(),
            "-o",
            f"ControlPath={self.control_path}",
            "-o",
            "ControlMaster=yes",
            "-o",
            f"ControlPersist={self.control_persist}",
            "-o",
            "ExitOnForwardFailure=yes",
        ]
        if not self.promptless:
            result = subprocess.run([*command, "-fN", self.target], check=False)
            detail = ""
        else:
            for option in PROMPTLESS_MASTER_OPTIONS:
                command.extend(["-o", option])
            # The backgrounded master inherits stderr, so capture it in a file
            # rather than a pipe that would never reach end-of-file.
            with tempfile.TemporaryFile("w+") as errors:
                result = subprocess.run(
                    [*command, "-fN", self.target],
                    env={**os.environ, **self.askpass_env},
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                    start_new_session=True,
                    check=False,
                )
                errors.seek(0)
                detail = errors.read().strip()
        if result.returncode != 0:
            suffix = f"\n{detail}" if detail else ""
            raise RemoteRunError(
                f"SSH connection to {self.target} failed with status "
                f"{result.returncode}.{suffix}"
            )
        self.started = True

    def is_alive(self) -> bool:
        """Return whether the control master is still connected."""
        if not self.started:
            return False
        result = subprocess.run(
            [*self._base(), "-O", "check", self.target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def run_script(
        self,
        script: str,
        arguments: Sequence[str] = (),
        *,
        check: bool = True,
        on_output: Optional[Callable[[str], None]] = None,
        output_in_error: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a Bash program remotely and capture its combined output.

        Visible (non-marker) output is passed to `on_output` when provided.
        On failure the error message includes that output unless
        `output_in_error` is false, which callers use when the output has
        already been shown to the user.
        """
        remote_command = shell_command("bash", ("-s", "--", *arguments))
        result = subprocess.run(
            [*self._base(), self.target, remote_command],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=self.promptless,
            check=False,
        )

        visible = non_marker_output(result.stdout or "")
        if on_output and visible:
            on_output(visible)
        if check and result.returncode != 0:
            detail = "" if not output_in_error or not visible else f"\n{visible}"
            raise RemoteRunError(
                f"Remote command failed with status {result.returncode}.{detail}"
            )
        return result

    def start_script(self, script: str, arguments: Sequence[str]) -> subprocess.Popen:
        remote_command = shell_command("bash", ("-s", "--", *arguments))
        process = subprocess.Popen(
            [*self._base(), self.target, remote_command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            # A web server's Ctrl+C must not kill remote work before cleanup.
            start_new_session=self.promptless,
        )
        if process.stdin is None:
            raise RemoteRunError("Could not open standard input for the SSH process.")
        process.stdin.write(script)
        process.stdin.close()
        return process

    def run_script_to_file(
        self,
        script: str,
        arguments: Sequence[str],
        destination: Path,
    ) -> None:
        """Run a remote Bash program and write its binary stdout to `destination`.

        Transfer scripts must reserve stdout for file bytes and write diagnostics
        to stderr. A failed transfer removes the incomplete local file.
        """
        remote_command = shell_command("bash", ("-s", "--", *arguments))
        try:
            with destination.open("xb") as output:
                result = subprocess.run(
                    [*self._base(), self.target, remote_command],
                    input=script,
                    text=True,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    start_new_session=self.promptless,
                    check=False,
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            detail = (result.stderr or "").strip()
            suffix = f"\n{detail}" if detail else ""
            raise RemoteRunError(
                f"Remote file download failed with status {result.returncode}.{suffix}"
            )

    def start_tunnel(self, local_port: int, remote_port: int) -> subprocess.Popen:
        forwarding = f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}"
        process = subprocess.Popen(
            [
                *self._base(),
                "-o",
                "ExitOnForwardFailure=yes",
                "-N",
                "-L",
                forwarding,
                self.target,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=self.promptless,
        )
        self._wait_for_tunnel(process, local_port)
        return process

    def cancel_tunnel(self, local_port: int, remote_port: int) -> None:
        """Remove a forward from the control master.

        A multiplexed client may leave its forward registered on the master
        after it exits, so long-lived connections cancel forwards explicitly.
        """
        if not self.started:
            return
        forwarding = f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}"
        subprocess.run(
            [*self._base(), "-O", "cancel", "-L", forwarding, self.target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=self.promptless,
            check=False,
        )

    @staticmethod
    def _wait_for_tunnel(process: subprocess.Popen, local_port: int) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.25):
                    return
            except OSError:
                pass
            status = process.poll()
            # Status 0 can mean the master now owns the forward; keep checking
            # the port. Any other status is a failure.
            if status is not None and status != 0:
                stderr = process.stderr.read().strip() if process.stderr else ""
                suffix = f": {stderr}" if stderr else ""
                raise RemoteRunError(f"SSH tunnel exited with status {status}{suffix}")
            time.sleep(0.1)

        process.terminate()
        raise RemoteRunError(f"SSH tunnel did not open local port {local_port}.")

    def close(self) -> None:
        if not self.started:
            return
        subprocess.run(
            [*self._base(), "-O", "exit", self.target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        self.started = False
        try:
            Path(self.control_path).unlink(missing_ok=True)
        except OSError:
            pass
