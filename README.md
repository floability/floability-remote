# Floability Remote

`floability-remote` runs a Floability backpack on a remote Linux login node
without requiring the user to manually log in, clone the backpack, activate an
environment, or create a Jupyter tunnel.

This is a standalone client. It uses the released `floability` command on the
remote system and does not modify Floability itself.

## Local installation

You need Python 3.9 or newer and the system OpenSSH client. The installer also
installs the web interface's Python dependencies (FastAPI and Uvicorn). Install the command
and its isolated virtual environment with:

```bash
curl -fsSL https://raw.githubusercontent.com/floability/floability-remote/main/install.sh | sh
```

The installer places the environment under
`~/.local/share/floability-remote/venv` and links the command into
`~/.local/bin`. You do not need to activate the environment. Confirm the
installation:

```bash
floability-remote --version
floability-remote --help
```

If `~/.local/bin` is not already on `PATH`, the installer prints the one-line
`export PATH=...` command needed by the current terminal. Add the same line to
your shell profile to make it permanent.

Run the installer again to update to the latest version from the `main` branch.

### Development installation

To work on the source, clone the repository and install it in an editable
environment instead:

```bash
git clone https://github.com/floability/floability-remote.git
cd floability-remote
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --editable .
```

## Commands

### Interactive run

Start an interactive backpack and create a local Jupyter tunnel:

```bash
floability-remote run \
  --target <USER@LOGIN_NODE> \
  --backpack https://github.com/floability-hub/matrix-multiplication.git \
  --batch-type slurm
```

The command prints a localhost URL when Jupyter is ready. Keep the command
running while using Jupyter and press Ctrl+C when finished.

### Execute

Execute a notebook, Python, or shell backpack and wait for it to finish:

```bash
floability-remote execute \
  --target <USER@LOGIN_NODE> \
  --backpack https://github.com/floability-hub/matrix-multiplication-script.git \
  --batch-type slurm
```

Use `--entrypoint` when a backpack contains more than one possible workflow:

```bash
floability-remote execute \
  --target <USER@LOGIN_NODE> \
  --backpack <GITHUB_URL> \
  --batch-type slurm \
  --entrypoint <WORKFLOW_FILENAME>
```

After execution, the client prints the remote backpack directory containing
the synchronized workflow and generated outputs. Automatic output download is
planned but not implemented yet.

### Web interface (preview)

Start the local web interface:

```bash
floability-remote web
```

The server listens only on `127.0.0.1`, prints a sign-in link, and opens it in
your browser. Use `--port` to choose the port and `--no-browser` to only print
the link. Press Ctrl+C to stop it.

From the browser you can:

- connect to a login node; passwords, MFA codes, and new host keys are asked
  in the page (OpenSSH 8.4 or newer) and never stored;
- validate a configuration and copy the equivalent CLI command; and
- execute a backpack, follow its progress and full log, approve a Miniforge
  installation, and cancel with remote cleanup; and
- start an interactive run and open JupyterLab from the **Open JupyterLab**
  link once the SSH tunnel is ready, then stop the session from the page.

Runs and Jupyter sessions continue if you close the tab; reopening the page
shows them again. Stopping the server with Ctrl+C stops an active run or
session with remote cleanup and closes the SSH connection. See
[docs/web-ui-milestones.md](docs/web-ui-milestones.md) and the API reference in
[docs/web-api.md](docs/web-api.md).

## Authentication

The client creates one OpenSSH control connection and reuses it for setup,
execution, cleanup, and tunneling. Existing SSH aliases, keys, `ProxyJump`, MFA,
and password prompts continue to be handled by OpenSSH.

An SSH alias from `~/.ssh/config` can be used as the target:

```bash
floability-remote run \
  --target my-cluster \
  --backpack <GITHUB_URL> \
  --batch-type slurm
```

Or provide an identity file explicitly:

```bash
floability-remote run \
  --target <USER@LOGIN_NODE> \
  --identity-file ~/.ssh/id_ed25519 \
  --backpack <GITHUB_URL> \
  --batch-type slurm
```

## Remote environment

The client looks for a remote Conda environment named
`floability-remote-managed`. It first uses the Conda installation available on
the remote host. If the environment is missing, or exists without Floability,
the client creates or repairs it with the equivalent of:

```bash
conda create -y -n floability-remote-managed \
  -c conda-forge \
  --strict-channel-priority \
  python=3.12 \
  floability
```

If no Conda installation can be found, the client asks before installing
user-scoped Miniforge at:

```text
~/.local/share/floability-remote/miniforge
```

Use `--yes` to approve that bootstrap non-interactively. No remote `sudo` access
is required.

To ignore another Conda installation and replace Floability Remote's managed
Miniforge with a clean installation, use:

```bash
floability-remote execute \
  --target my-cluster \
  --backpack <GITHUB_URL> \
  --batch-type slurm \
  --reinstall-miniforge
```

This replaces only
`~/.local/share/floability-remote/miniforge`. It does not modify a system Conda
installation or Miniforge installed elsewhere. Environments inside the managed
installation are recreated as needed.

Use an exact Floability release when creating or repairing the environment:

```bash
floability-remote execute \
  --target my-cluster \
  --backpack <GITHUB_URL> \
  --batch-type slurm \
  --floability-version <VERSION>
```

## Floability storage directories

Floability Remote keeps cloned backpacks and command logs under `--remote-root`.
Floability itself separately stores instances and reusable software environments
under its base directory. Override Floability's storage locations when a cluster
requires a scratch or project filesystem:

```bash
floability-remote execute \
  --target my-cluster \
  --backpack <GITHUB_URL> \
  --batch-type slurm \
  --base-dir /scratch/$USER/floability \
  --data-cache-dir /scratch/$USER/floability-data-cache
```

When omitted, Floability uses `~/floability-base-dir` and its
`floability-data-cache` subdirectory.

## Progress and logs

The default display shows concise stages without printing every Conda, Git, or
Floability log line:

```text
[1/N] Connect to the login node
[2/N] Check remote tools and the Floability environment
[3/N] Clone the backpack
[4/N] Start Floability
      Prepare the instance, data, environment, and workers
[5/N] Open Jupyter or complete execution
```

Show the complete remote output for troubleshooting:

```bash
floability-remote execute \
  --target my-cluster \
  --backpack <GITHUB_URL> \
  --batch-type slurm \
  --verbose
```

Every invocation creates a unique directory similar to:

```text
~/.cache/floability-remote/runs/20261005-153000-a1b2c3d4/
```

The directory contains the cloned backpack and `run-command.log` or
`execute-command.log`. It is retained after completion so results are not
destroyed.

## Remote requirements

- Linux;
- Git;
- `setsid`; and
- either Conda or `curl`/`wget` for the optional Miniforge bootstrap.

The first version supports public Git repositories. Private repository
credential forwarding is not implemented.

## Code layout

```text
src/
└── floability_remote/
    ├── cli.py              argument parsing; adapter to the shared services
    ├── cli_reporter.py     terminal rendering of workflow events
    ├── config.py           typed run configuration and validation
    ├── events.py           structured events, sinks, and secret redaction
    ├── interaction.py      confirmation callbacks and cancellation
    ├── askpass.py          relay of SSH prompts to a client (web sign-in)
    ├── connection.py       long-lived SSH connection for the web interface
    ├── runs.py             background runs, event history, and cancellation
    ├── environment.py      remote Conda and Floability setup
    ├── workspace.py        remote run directory and backpack clone
    ├── workflow.py         shared run/execute orchestration
    ├── models.py           shared data structures
    ├── output.py           remote marker and Floability output parsing
    ├── remote_scripts.py   Bash programs sent through SSH
    ├── ssh.py              OpenSSH sessions and tunnels
    └── web/
        ├── server.py       `floability-remote web` startup
        ├── app.py          FastAPI application factory
        ├── security.py     loopback, origin, and session checks
        ├── schemas.py      API request and response models
        ├── errors.py       JSON error envelope
        ├── routes/         thin `/api/v1` routes
        └── static/         packaged HTML, CSS, and JavaScript client
```

The CLI and web API are adapters around the same services: deployment logic
lives in `config`, `environment`, `workspace`, and `workflow`, never in
argument parsing, API routes, or JavaScript.

## Tests

Tests use `unittest` and do not contact a remote host. The web API tests also
need `httpx`, provided by the `test` extra, and are skipped without it:

```bash
python -m pip install --editable '.[test]'
python -m unittest discover -s tests -v
```

Before relying on the client, test both commands against a disposable login
node. Test `--batch-type local` first, then the target site's batch system.
After interruption, verify that Floability, Jupyter, TaskVine workers,
factories, and batch jobs have stopped.

## Current limitations

- Outputs are retained remotely but are not downloaded automatically yet.
- Interactive mode parses Floability's current human-readable Jupyter output.
- Jupyter is expected to run on the login node where Floability is launched.
- Detached sessions and reconnecting to an existing run are not supported.
- Automatic Miniforge bootstrap requires access to GitHub release downloads.
- Cleanup is bounded and best-effort; an unconfirmed cleanup reports the exact
  remote directory requiring inspection.
