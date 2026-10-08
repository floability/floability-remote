"""A stand-in `ssh` executable for tests that exercise real subprocesses.

Master connections (`-fN`) ask for a password through `$SSH_ASKPASS`, the
same way OpenSSH does with `SSH_ASKPASS_REQUIRE=force`, optionally after a
host-key confirmation. Commands sent through the "master" run locally with
bash, and `-O check` / `-O exit` report and end the fake master.
"""

import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

PASSWORD = "correct horse"
HOST_KEY_PROMPT = (
    "The authenticity of host 'login.example.org' can't be established.\n"
    "ED25519 key fingerprint is SHA256:abc123.\n"
    "Are you sure you want to continue connecting (yes/no/[fingerprint])?"
)

SCRIPT = r"""#!/bin/bash
mode=command
control=""
previous=""
for argument in "$@"; do
    case "$previous" in -O) control="$argument" ;; esac
    case "$argument" in -fN) mode=master ;; -O) mode=control ;; esac
    previous="$argument"
done
printf '%s\n' "$*" >> "$FAKE_SSH_DIR/calls.log"

case "$mode" in
master)
    if [ -n "${FAKE_SSH_HOST_KEY:-}" ]; then
        reply=$("$SSH_ASKPASS" "$FAKE_SSH_HOST_KEY") || exit 255
        [ "$reply" = yes ] || { echo "Host key verification failed." >&2; exit 255; }
    fi
    answer=$("$SSH_ASKPASS" "user@login.example.org's password: ") || {
        echo "Permission denied (password)." >&2; exit 255; }
    [ "$answer" = "$FAKE_SSH_PASSWORD" ] || {
        echo "Permission denied (password)." >&2; exit 255; }
    touch "$FAKE_SSH_DIR/master"
    exit 0
    ;;
control)
    case "$control" in
    check) [ -f "$FAKE_SSH_DIR/master" ] ;;
    exit) rm -f "$FAKE_SSH_DIR/master" ;;
    cancel) true ;;
    esac
    exit $?
    ;;
esac

[ -f "$FAKE_SSH_DIR/master" ] || { echo "no master" >&2; exit 255; }

forward=""
previous=""
for argument in "$@"; do
    case "$previous" in -L) forward="$argument" ;; esac
    previous="$argument"
done
if [ -n "$forward" ]; then
    # -N -L 127.0.0.1:LOCAL:127.0.0.1:REMOTE: forward between local ports.
    exec python3 - "$forward" <<'PY'
import socket, sys, threading
_, local, _, remote = sys.argv[1].split(":")
server = socket.create_server(("127.0.0.1", int(local)))
def pipe(source, target):
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            target.sendall(data)
    except OSError:
        pass
    finally:
        source.close()
        target.close()
while True:
    client, _ = server.accept()
    upstream = socket.create_connection(("127.0.0.1", int(remote)))
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()
PY
fi

last="${*: -1}"
exec bash -c "$last"
"""


@contextmanager
def fake_ssh(host_key_prompt: bool = False):
    """Put a fake `ssh` first on PATH; yields its state directory."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "ssh"
        path.write_text(SCRIPT)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        environment = {
            "PATH": f"{directory}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_SSH_DIR": directory,
            "FAKE_SSH_PASSWORD": PASSWORD,
        }
        if host_key_prompt:
            environment["FAKE_SSH_HOST_KEY"] = HOST_KEY_PROMPT
        with mock.patch.dict(os.environ, environment):
            yield Path(directory)
