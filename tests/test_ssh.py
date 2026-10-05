import socket
import unittest

from floability_remote.ssh import choose_local_port, shell_command


class SSHUtilityTests(unittest.TestCase):
    def test_remote_arguments_are_shell_quoted(self):
        command = shell_command("bash", ("-s", "--", "value with spaces", "$(unsafe)"))
        self.assertEqual(command, "bash -s -- 'value with spaces' '$(unsafe)'")

    def test_selected_local_port_is_bindable(self):
        port = choose_local_port(None)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()

