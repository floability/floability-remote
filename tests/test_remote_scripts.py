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

    def test_miniforge_uses_release_digest(self):
        script = remote_scripts.INSTALL_MINIFORGE
        self.assertIn("api.github.com/repos/conda-forge/miniforge/releases/latest", script)
        self.assertIn('sha256sum "$installer_path"', script)
        self.assertNotIn('$base_url/$installer.sha256', script)

    def test_miniforge_reinstall_preserves_previous_install_on_failure(self):
        script = remote_scripts.INSTALL_MINIFORGE
        self.assertIn("reinstall=$2", script)
        self.assertIn('mv -- "$destination" "$backup"', script)
        self.assertIn("restore_previous_installation", script)


if __name__ == "__main__":
    unittest.main()
