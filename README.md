# Floability Remote

`floability-remote` runs a Floability backpack on a remote Linux login node
without requiring the user to manually log in, clone the backpack, activate an
environment, or create a Jupyter tunnel.

This is a standalone client. It uses the released `floability` command on the
remote system and does not modify Floability itself.

## Local installation

You need Python 3.9 or newer and the system OpenSSH client. From the repository
root, create an isolated local environment and install the command:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

Confirm the installation:

```bash
floability-remote --version
floability-remote --help
```

Whenever you open a new terminal, activate the environment before using the
command:

```bash
cd <PATH_TO_FLOABILITY_REMOTE>
source .venv/bin/activate
```

If you are modifying the source code, install it in editable mode instead:

```bash
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

The client looks for a remote Conda environment named `floability-env`. If the
environment is missing, it creates the equivalent of:

```bash
conda create -y -n floability-env \
  -c conda-forge \
  --strict-channel-priority \
  python=3.12 \
  floability
```

If Conda is unavailable, the client offers to install user-scoped Miniforge at:

```text
~/.local/share/floability-remote/miniforge
```

Use `--yes` to approve that bootstrap non-interactively. No remote `sudo` access
is required.

Use an exact Floability release when creating or repairing the environment:

```bash
floability-remote execute \
  --target my-cluster \
  --backpack <GITHUB_URL> \
  --batch-type slurm \
  --floability-version <VERSION>
```

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
    ├── cli.py              command definitions and validation
    ├── environment.py      remote Conda and Floability setup
    ├── models.py           shared data structures
    ├── output.py           quiet progress and Jupyter parsing
    ├── remote_scripts.py   Bash programs sent through SSH
    ├── ssh.py              OpenSSH sessions and tunnels
    └── workflow.py         shared run/execute orchestration
```

This separation leaves file transfer as a transport/workflow feature instead
of mixing it into command parsing or output handling.

## Tests

Tests use the standard library and do not contact a remote host:

```bash
python3 -m unittest discover -s tests -v
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
