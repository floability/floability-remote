import os
import subprocess
import threading
import time
import unittest

from floability_remote.askpass import (
    TOKEN_ENV,
    AskPassBroker,
    PromptKind,
    classify,
)


def wait_for(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition was not met")


class AskPassTests(unittest.TestCase):
    def setUp(self):
        self.prompts = []
        self.broker = AskPassBroker(self.prompts.append)
        self.addCleanup(self.broker.close)

    def run_helper(self, prompt, env=None):
        """Run the helper as ssh would and return (status, stdout)."""
        result = {}

        def target():
            completed = subprocess.run(
                [self.broker.helper_path, prompt],
                env={**os.environ, **self.broker.env(), **(env or {})},
                capture_output=True,
                text=True,
                timeout=20,
            )
            result["status"] = completed.returncode
            result["stdout"] = completed.stdout

        thread = threading.Thread(target=target)
        thread.start()
        return thread, result

    def test_answer_reaches_ssh(self):
        thread, result = self.run_helper("user@host's password: ")
        prompt = wait_for(lambda: self.broker.pending)
        self.assertEqual(prompt.kind, PromptKind.SECRET)
        self.assertEqual(prompt.message, "user@host's password:")
        self.assertTrue(self.broker.answer(prompt.id, "s3cret"))
        thread.join(timeout=20)
        self.assertEqual(result, {"status": 0, "stdout": "s3cret\n"})
        self.assertEqual(self.prompts[-1], None)
        self.assertIsNone(self.broker.pending)

    def test_cancel_fails_the_helper(self):
        thread, result = self.run_helper("Verification code: ")
        prompt = wait_for(lambda: self.broker.pending)
        self.assertTrue(self.broker.answer(prompt.id, None))
        thread.join(timeout=20)
        self.assertEqual(result["status"], 1)
        self.assertEqual(result["stdout"], "")

    def test_stale_answer_is_rejected(self):
        self.assertFalse(self.broker.answer("missing", "x"))

    def test_wrong_token_is_refused_without_prompting(self):
        thread, result = self.run_helper("password: ", env={TOKEN_ENV: "forged"})
        thread.join(timeout=20)
        self.assertEqual(result["status"], 1)
        self.assertEqual(self.prompts, [])

    def test_close_cancels_pending_prompt_and_removes_files(self):
        thread, result = self.run_helper("password: ")
        wait_for(lambda: self.broker.pending)
        self.broker.close()
        thread.join(timeout=20)
        self.assertEqual(result["status"], 1)
        self.assertFalse(os.path.exists(self.broker.helper_path))

    def test_environment_forces_askpass(self):
        env = self.broker.env()
        self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "force")
        self.assertEqual(env["SSH_ASKPASS"], self.broker.helper_path)
        mode = os.stat(os.path.dirname(self.broker.helper_path)).st_mode & 0o777
        self.assertEqual(mode, 0o700)


class ClassifyTests(unittest.TestCase):
    def test_host_key_question_is_a_confirmation(self):
        self.assertEqual(
            classify("Are you sure you want to continue connecting (yes/no/[fingerprint])?"),
            PromptKind.CONFIRM,
        )

    def test_passwords_and_codes_are_secret(self):
        for prompt in ("user@host's password:", "Duo passcode:", "Enter passphrase for key"):
            with self.subTest(prompt=prompt):
                self.assertEqual(classify(prompt), PromptKind.SECRET)


if __name__ == "__main__":
    unittest.main()
