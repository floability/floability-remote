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

Install MCP or the built-in agent in the same isolated environment by selecting
an extra during installation:

```bash
curl -fsSL https://raw.githubusercontent.com/floability/floability-remote/main/install.sh \
  | env FLOABILITY_REMOTE_EXTRAS=mcp sh

curl -fsSL https://raw.githubusercontent.com/floability/floability-remote/main/install.sh \
  | env FLOABILITY_REMOTE_EXTRAS=agent sh
```

The `agent` extra includes MCP, OpenAI, and Anthropic support. Ollama itself is
a separate local application. If `python3` is older than 3.10, add
`FLOABILITY_REMOTE_PYTHON=python3.12` to the `env` command.

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

Install the optional MCP adapter with Python 3.10 or newer:

```bash
python -m pip install --editable '.[mcp]'
```

Install the built-in agent and all supported model providers with:

```bash
python -m pip install --editable '.[agent]'
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

### Run a local backpack

`--backpack` also accepts a local folder or ZIP file. Floability Remote checks
the backpack, skips local development artifacts such as `.git` and `.venv`,
and uploads a fresh copy through the existing SSH connection for every run:

```bash
floability-remote execute \
  --target <USER@LOGIN_NODE> \
  --backpack ./my-backpack \
  --batch-type slurm
```

Local backpacks must contain `workflow/`, may contain at most 5,000 files and
200 MB of data, and cannot contain symlinks. ZIP paths are checked before any
remote upload.

Copy a Git backpack to `~/floability-backpacks/` when you want to edit it
locally first:

```bash
floability-remote clone \
  https://github.com/floability-hub/matrix-multiplication-script.git
```

Use `--ref <BRANCH_TAG_OR_COMMIT>` or `--into <LOCAL_FOLDER>` when needed.
The clone's repository, ref, commit, and edit status are recorded when that
folder is run.

After execution, the client prints the retained remote run directory. Use it
to list and download one result or log file:

```bash
floability-remote download \
  --target <USER@LOGIN_NODE> \
  --run-dir <REMOTE_RUN_DIRECTORY>
```

The command opens one SSH connection, groups the available files, and asks
which one to save. For scripts, select a logical path and destination directly:

```bash
floability-remote download \
  --target <USER@LOGIN_NODE> \
  --run-dir <REMOTE_RUN_DIRECTORY> \
  --file workflow/results.csv \
  --output ./results.csv
```

Use `--list-only` to inspect files without downloading. Existing local files
are never overwritten.

### Web interface (preview)

Start the local web interface:

```bash
floability-remote web
```

The server listens only on `127.0.0.1`, prints a sign-in link, and opens it in
your browser. Use `--port` to choose the port and `--no-browser` to only print
the link. Press Ctrl+C to stop it.

For a headless process that survives the terminal which started it, use the
daemon lifecycle commands instead:

```bash
floability-remote daemon start
floability-remote daemon status
floability-remote daemon stop
```

`daemon start` reuses an existing healthy daemon and opens its sign-in page.
An idle daemon stops after one hour only when it has no SSH connection or
active run. `daemon stop` closes the connection cleanly; when a run is active,
it refuses unless you explicitly use `daemon stop --yes`, which cancels the
run and waits for remote cleanup.

### MCP server (preview)

Floability Remote can expose its daemon state to Claude Code, Codex, and other
MCP hosts over stdio:

```bash
floability-remote daemon start --no-browser
floability-remote mcp
```

The MCP tools can inspect status, manage a daemon-owned SSH connection, check
remote readiness, inspect Git/local/ZIP backpacks, safely preview their files,
validate an execute-mode deployment plan, and start and monitor that plan.
Runs return immediately with a `run_id`; bounded cursor-based calls report
redacted progress, pending confirmations, completion, and retained remote
paths. The MCP process does not own SSH or deployment state; it calls the
authenticated local daemon, which records requests with an `mcp` client
identity. Passwords, MFA answers, key passphrases, bearer tokens, and Jupyter
tokens must never be passed through MCP.

External MCP hosts can reuse a passwordless connection requested by a tool. If
the host needs a password, MFA response, key passphrase, or new-host
confirmation, establish it in a separate terminal so the answer never enters
the model conversation:

```bash
floability-remote connect <USER@LOGIN_NODE>
```

See [docs/mcp.md](docs/mcp.md) for installation and host configuration.

### Built-in agent (preview)

The terminal agent uses the same MCP server and tools as external hosts. It
starts or reuses the headless daemon and defaults to a local Ollama model:

```bash
ollama pull qwen3
floability-remote agent
```

Use another local model, a hosted provider, or a single noninteractive prompt:

```bash
floability-remote agent --model llama3.1:8b
floability-remote agent --model openai:gpt-5
floability-remote agent --model anthropic:your-model-name
floability-remote agent --prompt "Check Floability Remote status."
```

The agent can connect, inspect a backpack, prepare a validated plan, and run
an execute-mode workflow:

```text
Connect to <USER@LOGIN_NODE>, inspect
https://github.com/floability-hub/matrix-multiplication-script.git,
prepare an execute-mode deployment using Slurm, start it, and monitor it
until it finishes.
```

The terminal asks for approval before starting, answering an installation
question, cancelling, connecting, or disconnecting. Interactive Jupyter runs
are not available through the agent yet.

Set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` before selecting the corresponding
hosted provider. The agent asks for terminal approval before connecting or
disconnecting. OpenSSH authentication questions are then relayed through
hidden terminal input directly to the daemon, outside MCP and model history.
Use `--yes` only when you deliberately want to preapprove state-changing tool
calls; it never supplies or bypasses SSH credentials.

See [docs/agent.md](docs/agent.md) for model configuration and current scope.

From the browser you can:

- connect to a login node; passwords, MFA codes, and new host keys are asked
  in the page (OpenSSH 8.4 or newer) and never stored;
- check whether the selected Floability environment and required remote tools
  are ready, and inspect free filesystem space and available quota reporting;
- validate a configuration and copy the equivalent CLI command;
- choose a Git repository, a local folder (with a built-in folder browser), or
  a ZIP file, which runs as it is; use **Clone to this computer and edit** for
  a Git backpack; open a local folder in the file browser or VS Code; and pick
  the workflow file from the files in `workflow/`;
- execute a backpack, follow its progress and full log, approve a Miniforge
  installation, and cancel with remote cleanup;
- start an interactive run and open JupyterLab from the **Open JupyterLab**
  link once the SSH tunnel is ready, then stop the session from the page; and
- download individual workflow, log, metadata, and metrics files after a run
  finishes.

Runs and Jupyter sessions continue if you close the tab; reopening the page
shows them again. Stopping the server with Ctrl+C stops an active run or
session with remote cleanup and closes the SSH connection. See
[docs/how-it-works.md](docs/how-it-works.md) for the architecture and current
implementation status, [docs/web-ui-milestones.md](docs/web-ui-milestones.md)
for planned work, and [docs/web-api.md](docs/web-api.md) for the API reference.

The same read-only readiness check is available from the terminal:

```bash
floability-remote check-cluster \
  --target <USER@LOGIN_NODE> \
  --base-dir <REMOTE_FLOABILITY_BASE_DIRECTORY>
```

It does not install software or create the base directory. When the selected
environment is missing, the normal `run` or `execute` flow can prepare it after
confirmation.

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

Floability Remote keeps cloned or uploaded backpacks and command logs under `--remote-root`.
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

## Extra Floability options

Pass any other `floability run` or `floability execute` option with
`--floability-option NAME=VALUE`, or `--floability-option NAME` for an on/off
flag. Repeat it as needed:

```bash
floability-remote execute \
  --target my-cluster \
  --backpack <GITHUB_URL> \
  --batch-type slurm \
  --floability-option workers=2
```

This runs `floability execute ... --workers 2`. Each name and value is passed
as a separate argument, never through a shell. Options that Floability Remote
sets itself (`--backpack`, `--batch-type`, `--entrypoint`, `--jupyter-port`,
`--base-dir`, `--data-cache-dir`) are rejected; use their dedicated settings.
The web interface offers the same rows under **Advanced options → Floability
options**.

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

The directory contains the cloned or uploaded backpack and `run-command.log` or
`execute-command.log`. It is retained after completion so results are not
destroyed.

## Downloadable files

The CLI and web interface use the same file-inventory and transfer service.
For a finished run they expose:

- `run-command.log` or `execute-command.log`;
- regular files under the Floability instance's `workflow/`;
- regular files directly under `logs/`;
- `catalog_update.json`; and
- regular files under `metadata/` and `metrics/`.

The instance directory is read from Floability's creation message in the
command log. Symbolic links, `pyuser/`, `vine_factory_scratch/`,
`vine-run-info/`, `.ipynb_checkpoints/`, environments, data caches,
directories, and special files are never offered. A listing is limited to
2,000 files, and an individual download to 100 MiB. Larger and multi-file
transfers are deferred to a later version.

## TaskVine activity

When a run's workflow uses TaskVine, the web interface's **TaskVine** card
shows the manager, workers, and tasks, refreshing every 5 seconds. Many
backpacks never start a TaskVine manager (Floability starts its worker factory
for every run regardless), so the card appears only once the transactions log
shows `MANAGER … START`. The same summary is available from the CLI, which
says so when the run has no TaskVine manager:

```bash
floability-remote taskvine --target my-cluster --run-dir <REMOTE_RUN_DIRECTORY>
```

It is read from two files the run already writes on the cluster, over the
existing SSH connection:

- TaskVine's transactions log, `vine-run-info/<time>/vine-logs/transactions`
  under the manager's working directory in the Floability instance, where the
  manager appends a line for every manager, worker, and task event as it
  happens; and
- the worker factory's latest status line in the instance's
  `logs/vine_factory.stdout`, which counts workers submitted to the batch
  system that have not connected yet.

The rules are deliberately simple: a worker is connected when its latest line
is `CONNECTION` and the manager is still running; a task's state is its
latest `TASK` line (`DONE SUCCESS` is done, any other `DONE` result is
failed); and the manager is running between `START` and `END`. Nothing
depends on the public catalog server.

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
    ├── domain/             configuration, events, errors, and value objects
    ├── remote/             OpenSSH, AskPass, output parsing, and remote scripts
    ├── deployment/         environment, workspace, workflow, and run lifecycle
    ├── services/           backpack, host-check, file, and TaskVine services
    ├── application/        operations, contracts, runtime, validation, and audit
    ├── cli/                terminal parsing, commands, prompts, and rendering
    ├── daemon/             loopback API, lifecycle, discovery, and security
    ├── mcp/                optional stdio tools over the daemon client
    ├── agent/              PydanticAI chat, models, prompts, and approval policy
    └── web/                browser assets and compatibility imports
```

The CLI and daemon API are adapters around the same application operations.
The browser calls the daemon API, and the MCP server calls its typed client.
Deployment logic lives in the application and lower-level packages, never in
argument parsing, API routes, MCP registrations, or JavaScript. The daemon
writes a private discovery record for local clients and a size-bounded,
secret-redacted operation audit under the user's state directory.

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

- Individual retained files can be downloaded explicitly; automatic,
  multi-file, and large-result transfers are not implemented yet.
- Interactive mode parses Floability's current human-readable Jupyter output.
- Jupyter is expected to run on the login node where Floability is launched.
- Detached sessions and reconnecting to an existing run are not supported.
- Automatic Miniforge bootstrap requires access to GitHub release downloads.
- Cleanup is bounded and best-effort; an unconfirmed cleanup reports the exact
  remote directory requiring inspection.
