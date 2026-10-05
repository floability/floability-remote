import subprocess
import unittest

from floability_remote import remote_scripts


class RemoteScriptTests(unittest.TestCase):
    def test_all_embedded_scripts_are_valid_bash(self):
        for script in remote_scripts.ALL:
            with self.subTest(first_line=script.splitlines()[1:2]):
                result = subprocess.run(
                    ["bash", "-n"], input=script, text=True, capture_output=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_supports_run_and_execute(self):
        self.assertIn("run|execute", remote_scripts.LAUNCH_FLOABILITY)
        self.assertIn('"$env_prefix/bin/floability"', remote_scripts.LAUNCH_FLOABILITY)
        self.assertIn('"$mode"', remote_scripts.LAUNCH_FLOABILITY)


if __name__ == "__main__":
    unittest.main()

