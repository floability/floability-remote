import io
import socket
import unittest
from unittest import mock

from floability_remote.domain.errors import RemoteRunError
from floability_remote.remote.ssh import SSHSession, choose_local_port, shell_command


class ExitedProcess:
    def __init__(self, status, stderr=""):
        self.status = status
        self.stderr = io.StringIO(stderr)
        self.terminated = False

    def poll(self):
        return self.status

    def terminate(self):
        self.terminated = True


class TunnelWaitTests(unittest.TestCase):
    def test_open_port_counts_even_if_client_exited_cleanly(self):
        # A multiplexed client may exit 0 once the master owns the forward.
        with socket.create_server(("127.0.0.1", 0)) as listener:
            port = listener.getsockname()[1]
            SSHSession._wait_for_tunnel(ExitedProcess(0), port)

    def test_failed_client_reports_ssh_error(self):
        port = choose_local_port(None)
        with self.assertRaisesRegex(RemoteRunError, "status 255: bind failed"):
            SSHSession._wait_for_tunnel(ExitedProcess(255, "bind failed"), port)


class SSHUtilityTests(unittest.TestCase):
    def test_remote_arguments_are_shell_quoted(self):
        command = shell_command("bash", ("-s", "--", "value with spaces", "$(unsafe)"))
        self.assertEqual(command, "bash -s -- 'value with spaces' '$(unsafe)'")

    def test_selected_local_port_is_bindable(self):
        port = choose_local_port(None)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))

    def test_binary_stream_is_sent_to_remote_bash_command(self):
        session = object.__new__(SSHSession)
        session.ssh = "ssh"
        session.target = "user@host"
        session.identity_file = None
        session.ssh_options = ()
        session.askpass_env = None
        session.control_path = "/tmp/control"
        source = io.BytesIO(b"tar data")
        completed = mock.Mock(returncode=0, stdout=b"uploaded\n")
        with mock.patch("floability_remote.remote.ssh.subprocess.run", return_value=completed) as run:
            result = session.run_stream("cat >/tmp/archive", ("one",), source)
        self.assertEqual(result.stdout, "uploaded\n")
        self.assertIs(run.call_args.kwargs["stdin"], source)
        command = run.call_args.args[0][-1]
        self.assertIn("bash -c", command)
        self.assertIn("cat >/tmp/archive", command)


if __name__ == "__main__":
    unittest.main()

