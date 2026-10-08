import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from floability_remote import remote_scripts


UTIL_LINUX_SETSID = f"""#!{sys.executable}
import os, sys
# util-linux setsid forks when its caller leads a process group.
if os.getpgrp() == os.getpid() and os.fork() > 0:
    os._exit(0)
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
"""

STAND_IN_FLOABILITY = f"""#!{sys.executable}
import os, signal, sys, time
handler = signal.getsignal(signal.SIGINT)
state = "default" if handler is signal.default_int_handler else "ignored"
print(f"started pid={{os.getpid()}} sigint={{state}} args={{sys.argv[1:]}}", flush=True)
try:
    while True:
        time.sleep(0.05)
except KeyboardInterrupt:
    print("interrupted: cleaning up", flush=True)
    sys.exit(130)
"""


def executable(path, content):
    path.write_text(content)
    path.chmod(0o755)


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

    def test_probe_resolves_conda_shell_function_to_absolute_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conda = root / "bin" / "conda"
            conda.parent.mkdir()
            executable(
                conda,
                "#!/bin/sh\n"
                "if [ \"$1 $2\" = \"info --base\" ]; then\n"
                f"  printf '%s\\n' '{root}'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )
            wrapper = f"""
unset CONDA_EXE
conda() {{ "{conda}" "$@"; }}
export -f conda
bash -s -- floability-remote-managed ""
"""
            result = subprocess.run(
                ["bash", "-c", wrapper],
                input=remote_scripts.PROBE,
                text=True,
                capture_output=True,
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            f"__FLOABILITY_REMOTE_CONDA__={conda}",
            result.stdout,
        )

    def test_probe_discovers_environment_without_conda_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conda = root / "bin" / "conda"
            prefix = root / "envs" / "floability-remote-managed"
            conda.parent.mkdir()
            (prefix / "bin").mkdir(parents=True)
            executable(
                conda,
                "#!/bin/sh\n"
                "if [ \"$1 $2\" = \"env list\" ]; then\n"
                f"  printf '%s  %s\\n' floability-remote-managed '{prefix}'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )
            executable(prefix / "bin" / "python", "#!/bin/sh\nprintf '0.3.1\\n'\n")
            executable(prefix / "bin" / "floability", "#!/bin/sh\nexit 0\n")

            result = subprocess.run(
                [
                    "bash",
                    "-s",
                    "--",
                    "floability-remote-managed",
                    str(conda),
                ],
                input=remote_scripts.PROBE,
                text=True,
                capture_output=True,
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"__FLOABILITY_REMOTE_ENV_PREFIX__={prefix}", result.stdout)
        self.assertIn("__FLOABILITY_REMOTE_VERSION__=0.3.1", result.stdout)
        self.assertNotIn(" run -n ", remote_scripts.PROBE)

    def test_probe_discovers_unique_prefix_environment_by_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conda = root / "bin" / "conda"
            prefix = root / "custom" / "floability-remote-managed"
            conda.parent.mkdir()
            (prefix / "bin").mkdir(parents=True)
            executable(
                conda,
                "#!/bin/sh\n"
                "if [ \"$1 $2\" = \"env list\" ]; then\n"
                f"  printf '                       %s\\n' '{prefix}'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )
            executable(prefix / "bin" / "python", "#!/bin/sh\nprintf '0.3.1\\n'\n")
            executable(prefix / "bin" / "floability", "#!/bin/sh\nexit 0\n")

            result = subprocess.run(
                [
                    "bash",
                    "-s",
                    "--",
                    "floability-remote-managed",
                    str(conda),
                ],
                input=remote_scripts.PROBE,
                text=True,
                capture_output=True,
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"__FLOABILITY_REMOTE_ENV_PREFIX__={prefix}", result.stdout)

    def test_probe_does_not_guess_between_duplicate_prefix_environments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conda = root / "bin" / "conda"
            first = root / "one" / "floability-remote-managed"
            second = root / "two" / "floability-remote-managed"
            conda.parent.mkdir()
            executable(
                conda,
                "#!/bin/sh\n"
                "if [ \"$1 $2\" = \"env list\" ]; then\n"
                f"  printf '                       %s\\n' '{first}'\n"
                f"  printf '                       %s\\n' '{second}'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )

            result = subprocess.run(
                [
                    "bash",
                    "-s",
                    "--",
                    "floability-remote-managed",
                    str(conda),
                ],
                input=remote_scripts.PROBE,
                text=True,
                capture_output=True,
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("__FLOABILITY_REMOTE_ENV_PREFIX__=\n", result.stdout)

    def test_stop_interrupts_the_launched_floability(self):
        """Run the real launch and stop scripts against a stand-in Floability.

        The stand-in `setsid` behaves like util-linux: it forks when called
        by a process-group leader. The recorded PID must still be
        Floability's, and SIGINT must reach it as KeyboardInterrupt.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools, prefix = root / "tools", root / "env"
            run_dir, backpack = root / "run", root / "run" / "backpack"
            for path in (tools, prefix / "bin", backpack):
                path.mkdir(parents=True)
            executable(tools / "setsid", UTIL_LINUX_SETSID)
            executable(prefix / "bin" / "floability", STAND_IN_FLOABILITY)
            (prefix / "bin" / "python").symlink_to(sys.executable)

            launch = subprocess.Popen(
                [
                    "bash", "-s", "--", "conda", str(prefix), str(run_dir),
                    str(backpack), "execute", "local", "8888", "",
                    "~", "~/data cache",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env={
                    **os.environ,
                    "HOME": str(root / "home"),
                    "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
                },
            )
            self.addCleanup(lambda: launch.poll() is None and launch.kill())
            launch.stdin.write(remote_scripts.LAUNCH_FLOABILITY)
            launch.stdin.close()

            started = ""
            while "started" not in started:
                started = launch.stdout.readline()
                self.assertTrue(started, "the launcher exited before Floability started")
            recorded = (run_dir / "floability.pid").read_text().strip()
            self.assertIn(f"pid={recorded} ", started)
            self.assertIn("sigint=default", started)
            self.assertIn(f"'--base-dir', '{root / 'home'}'", started)
            self.assertIn(
                f"'--data-cache-dir', '{root / 'home' / 'data cache'}'",
                started,
            )

            stop = subprocess.run(
                ["bash", "-s", "--", str(run_dir), "INT", "10"],
                input=remote_scripts.STOP_FLOABILITY,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(stop.returncode, 0, stop.stdout + stop.stderr)
            remaining = launch.stdout.read()
            self.assertIn("interrupted", remaining)
            self.assertEqual(launch.wait(timeout=30), 130)
            self.assertFalse((run_dir / "floability.pid").exists())

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
