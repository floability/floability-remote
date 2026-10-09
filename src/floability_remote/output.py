"""Parsing of remote script markers and Floability output."""

import re
from typing import Optional
from urllib.parse import quote

from .errors import RemoteRunError
from .models import JupyterConnection, RemoteProbe


MARKER_PREFIX = "__FLOABILITY_REMOTE_"

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_JUPYTER_ANNOUNCEMENT = re.compile(
    r"\[jupyter\]\s+Detected JupyterLab URL with port\s+(\d+)\s+"
    r"and token\s+([^\s.]+)\."
)
_JUPYTER_URL = re.compile(
    r"https?://[^\s/:]+:(\d+)(/[^\s?]*)\?token=([A-Za-z0-9._~-]+)"
)

_FLOABILITY_PROGRESS = (
    ("[floability] Preparing new instance from backpack", "Preparing backpack instance"),
    ("[floability] data materialization", "Preparing backpack data"),
    (
        "[floability] environment setup (manager & worker)",
        "Preparing the backpack software environment",
    ),
    ("[floability] worker factory startup", "Starting TaskVine workers"),
    ("[floability] JupyterLab startup", "Starting JupyterLab"),
    ("[floability] Python script execution", "Executing the Python workflow"),
    ("[floability] Shell script execution", "Executing the shell workflow"),
    ("[floability] notebook execution", "Executing the notebook workflow"),
)


def marker_values(output: str) -> dict:
    values = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith(MARKER_PREFIX) and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def non_marker_output(output: str) -> str:
    return "\n".join(
        line for line in output.splitlines() if not line.strip().startswith(MARKER_PREFIX)
    ).strip()


def parse_probe(output: str) -> RemoteProbe:
    values = marker_values(output)

    def optional(key: str) -> Optional[str]:
        value = values.get(key, "").strip()
        return value or None

    os_name = optional("__FLOABILITY_REMOTE_OS__")
    architecture = optional("__FLOABILITY_REMOTE_ARCH__")
    if not os_name or not architecture:
        raise RemoteRunError("Could not read remote operating-system information.")

    return RemoteProbe(
        os_name=os_name,
        architecture=architecture,
        conda=optional("__FLOABILITY_REMOTE_CONDA__"),
        env_prefix=optional("__FLOABILITY_REMOTE_ENV_PREFIX__"),
        floability_version=optional("__FLOABILITY_REMOTE_VERSION__"),
        git_available=values.get("__FLOABILITY_REMOTE_GIT__") == "yes",
        setsid_available=values.get("__FLOABILITY_REMOTE_SETSID__") == "yes",
        downloader=optional("__FLOABILITY_REMOTE_DOWNLOADER__"),
    )


def parse_jupyter_connection(line: str) -> Optional[JupyterConnection]:
    plain = _ANSI.sub("", line)
    detected = _JUPYTER_ANNOUNCEMENT.search(plain)
    if detected:
        return JupyterConnection(int(detected.group(1)), detected.group(2))

    url = _JUPYTER_URL.search(plain)
    if url:
        return JupyterConnection(
            remote_port=int(url.group(1)),
            token=url.group(3),
            path=url.group(2) or "/lab/",
        )
    return None


def concise_floability_progress(line: str) -> Optional[str]:
    plain = _ANSI.sub("", line)
    for marker, message in _FLOABILITY_PROGRESS:
        if marker in plain:
            return message
    return None


def local_jupyter_url(connection: JupyterConnection, local_port: int) -> str:
    path = connection.path if connection.path.startswith("/") else f"/{connection.path}"
    token = quote(connection.token, safe="._~-")
    return f"http://127.0.0.1:{local_port}{path}?token={token}"

