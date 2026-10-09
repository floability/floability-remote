import getpass
import time
import unittest

from floability_remote.askpass import PromptKind
from floability_remote.config import ConnectionConfig
from floability_remote.connection import (
    ConnectionConflict,
    ConnectionManager,
    ConnectionState,
)

from fake_ssh import PASSWORD, fake_ssh


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition was not met")


class ConnectionManagerTests(unittest.TestCase):
    """Real SSHSession, AskPass broker, and helper against a fake ssh binary."""

    def setUp(self):
        self.manager = ConnectionManager()
        self.addCleanup(self.manager.close)

    def connect_and_wait_for_prompt(self):
        self.manager.connect(ConnectionConfig("login.example.org"))
        return wait_for(lambda: self.manager.snapshot().prompt)

    def test_password_prompt_then_connected(self):
        with fake_ssh() as state:
            prompt = self.connect_and_wait_for_prompt()
            self.assertEqual(prompt.kind, PromptKind.SECRET)
            self.assertIn("password", prompt.message)
            self.assertTrue(self.manager.answer_prompt(prompt.id, PASSWORD))

            snapshot = wait_for(
                lambda: (s := self.manager.snapshot()).state != ConnectionState.CONNECTING and s
            )
            self.assertEqual(snapshot.state, ConnectionState.CONNECTED, snapshot.error)
            self.assertEqual(snapshot.remote_user, getpass.getuser())
            self.assertTrue(snapshot.remote_host)
            self.assertIsNone(snapshot.prompt)

            calls = (state / "calls.log").read_text()
            self.assertIn("BatchMode=yes", calls)  # reuse never prompts
            self.assertIn("ServerAliveInterval=30", calls)
            self.assertNotIn(PASSWORD, calls)

            session = self.manager.session_for("login.example.org")
            self.assertTrue(session.is_alive())
            with self.assertRaisesRegex(ConnectionConflict, "not other.example.org"):
                self.manager.session_for("other.example.org")
            with self.assertRaises(ConnectionConflict):
                self.manager.connect(ConnectionConfig("login.example.org"))

            self.assertEqual(self.manager.disconnect().state, ConnectionState.DISCONNECTED)
            self.assertFalse((state / "master").exists())

    def test_tunnel_through_connection_reaches_remote_port(self):
        import http.server
        import threading
        import urllib.request

        from floability_remote.ssh import choose_local_port, terminate_process

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"jupyter says hello"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        remote = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=remote.serve_forever, daemon=True).start()
        self.addCleanup(remote.shutdown)

        with fake_ssh() as state:
            prompt = self.connect_and_wait_for_prompt()
            self.manager.answer_prompt(prompt.id, PASSWORD)
            wait_for(lambda: self.manager.snapshot().state == ConnectionState.CONNECTED)
            session = self.manager.session_for("login.example.org")

            local_port = choose_local_port(None)
            remote_port = remote.server_address[1]
            tunnel = session.start_tunnel(local_port, remote_port)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{local_port}/lab") as reply:
                    self.assertEqual(reply.read(), b"jupyter says hello")
            finally:
                terminate_process(tunnel)
                session.cancel_tunnel(local_port, remote_port)

            calls = (state / "calls.log").read_text()
            forward = f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}"
            self.assertIn(f"-N -L {forward}", calls)
            self.assertIn(f"-O cancel -L {forward}", calls)
            self.manager.disconnect()

    def test_wrong_password_fails_with_ssh_message(self):
        with fake_ssh():
            prompt = self.connect_and_wait_for_prompt()
            self.manager.answer_prompt(prompt.id, "wrong")
            snapshot = wait_for(
                lambda: (s := self.manager.snapshot()).state == ConnectionState.FAILED and s
            )
            self.assertIn("Permission denied", snapshot.error)
            self.assertNotIn("wrong", snapshot.error)

    def test_host_key_confirmation_comes_first(self):
        with fake_ssh(host_key_prompt=True):
            prompt = self.connect_and_wait_for_prompt()
            self.assertEqual(prompt.kind, PromptKind.CONFIRM)
            self.assertIn("SHA256:abc123", prompt.message)
            self.manager.answer_prompt(prompt.id, "yes")
            password = wait_for(
                lambda: (p := self.manager.snapshot().prompt) and p.id != prompt.id and p
            )
            self.manager.answer_prompt(password.id, PASSWORD)
            wait_for(lambda: self.manager.snapshot().state == ConnectionState.CONNECTED)
            self.manager.disconnect()

    def test_disconnect_while_prompting_cancels_sign_in(self):
        with fake_ssh():
            self.connect_and_wait_for_prompt()
            snapshot = self.manager.disconnect()
            self.assertEqual(snapshot.state, ConnectionState.DISCONNECTED)
            self.assertIsNone(snapshot.prompt)

    def test_lost_master_is_reported(self):
        with fake_ssh() as state:
            prompt = self.connect_and_wait_for_prompt()
            self.manager.answer_prompt(prompt.id, PASSWORD)
            wait_for(lambda: self.manager.snapshot().state == ConnectionState.CONNECTED)
            (state / "master").unlink()
            snapshot = self.manager.snapshot()
            self.assertEqual(snapshot.state, ConnectionState.FAILED)
            self.assertIn("lost", snapshot.error)
            with self.assertRaises(ConnectionConflict):
                self.manager.session_for("login.example.org")


if __name__ == "__main__":
    unittest.main()
