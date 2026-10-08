# Floability Deployment Basics

This document gives developers the minimum domain context needed to extend
`floability-remote`. It describes the behavior that the CLI and web API must
share. The current Python implementation remains the source of truth when this
document and the code differ.

## Mental model

Floability deploys and runs a portable workflow package called a **backpack**.
`floability-remote` does not reimplement Floability. It prepares a remote Linux
login node, obtains a backpack, invokes the released `floability` command, and
manages the connection around it.

```text
User interface (CLI or web)
        |
        | shared Python orchestration
        v
SSH connection to a remote login node
        |
        | floability run or floability execute
        v
TaskVine manager on the login node
        |
        | local, Slurm, Condor, or UGE workers
        v
Workflow tasks on available worker resources
```

The CLI and web server should be adapters around the same configuration,
workflow, event, cancellation, and cleanup functions.

## Backpacks

A backpack is normally a Git repository containing the workflow and the
specifications Floability needs to reproduce it. Common components include:

```text
backpack/
├── README.md
├── workflow/            notebook, Python, or shell entrypoint
├── software/            software environment specification
└── data/                data specification
```

Exact contents vary by backpack. Floability is responsible for interpreting
the backpack, materializing data, preparing its software environment, starting
TaskVine workers, and running the selected workflow.

The current remote client accepts a public Git repository, an optional branch,
tag, or commit, and an optional entrypoint. An entrypoint must remain inside
the backpack's `workflow/` directory. Private repository authentication and
arbitrary local uploads are not implemented.

## Two execution modes

### Interactive `run`

```bash
floability run \
  --backpack <REMOTE_BACKPACK_DIRECTORY> \
  --batch-type <BATCH_TYPE> \
  --jupyter-port <REMOTE_PORT>
```

`run` prepares the backpack and starts JupyterLab. It is a long-lived,
interactive session. Floability reports a Jupyter port and token after startup.
`floability-remote` then creates a local SSH port forward and gives the user a
URL such as:

```text
http://127.0.0.1:<LOCAL_PORT>/lab/?token=<TOKEN>
```

The SSH connection, Floability process, Jupyter process, and tunnel must remain
alive until the user explicitly stops the session. The token must be treated as
a secret and must not be written to logs.

### Finite `execute`

```bash
floability execute \
  --backpack <REMOTE_BACKPACK_DIRECTORY> \
  --batch-type <BATCH_TYPE>
```

`execute` runs a notebook, Python script, or shell workflow to completion. The
client waits for its exit status, reports success or failure, and retains the
remote directory containing logs and generated outputs. It does not require a
Jupyter tunnel.

Both modes accept `--entrypoint <FILENAME>` when the backpack contains multiple
possible workflows.

## Software environments

There are two different environment layers:

1. **Floability launcher environment**: a remote Conda environment named
   `floability-remote-managed` by default, containing the `floability` command.
2. **Backpack software environment**: defined by the backpack and prepared by
   Floability for its manager and workers.

Do not merge these concepts. Updating the launcher environment must not bypass
or replace the backpack's software specification.

The current client:

1. confirms the remote operating system is Linux;
2. checks for Git and `setsid`;
3. uses the remote host's default Conda when one is available;
4. if Conda is unavailable, asks before installing user-scoped Miniforge under
   `~/.local/share/floability-remote/miniforge`;
5. creates or repairs the launcher environment with Python 3.12 and
   Floability; and
6. confirms the installed Floability version.

Environment installation requires explicit user confirmation unless the caller
has already approved it. Core business logic must request confirmation through
a callback or interface; it must not call `input()` directly because web API
requests cannot answer terminal prompts.

## Batch systems and TaskVine

The current client supports these batch-type values:

```text
local
slurm
condor
uge
```

The batch type is passed to Floability. Floability starts the TaskVine manager
and arranges for TaskVine workers through the selected system. The remote client
should not duplicate scheduler or TaskVine management that belongs to
Floability.

Heavy workflow tasks should execute through TaskVine workers. Processes on the
login node should remain limited to orchestration, the TaskVine manager, and
interactive services allowed by the site.

## Current remote deployment lifecycle

One CLI invocation currently performs these steps:

1. Open one OpenSSH control connection to the target.
2. Probe and prepare the remote launcher environment.
3. Create a unique directory under
   `~/.cache/floability-remote/runs/`.
4. Clone the public backpack repository into that directory.
5. Launch Floability in a new session and record its PID and command log.
6. Stream output and convert recognized Floability messages into concise
   progress updates.
7. For interactive mode, detect the Jupyter connection and open a local SSH
   tunnel.
8. Wait for completion or user cancellation.
9. On cancellation or failure, ask Floability to stop cleanly, escalating from
   `SIGINT` to `SIGTERM` when necessary.
10. Close the tunnel and SSH control connection.
11. Retain the remote run directory and report its location.

The current clone-per-invocation behavior is sufficient for the CLI. The web UI
will eventually need a persistent **workspace** so a user can open Jupyter,
edit backpack files, and submit the edited workspace without cloning a fresh
copy. A workspace, an interactive session, and an execution run should remain
separate domain objects.

## Progress, logs, and events

Floability produces human-readable output describing stages such as:

- backpack instance preparation;
- data materialization;
- software environment preparation;
- TaskVine worker startup;
- Jupyter startup; and
- notebook, Python, or shell execution.

The CLI currently prints concise progress by default and complete remote output
in verbose mode. New orchestration code should emit structured events to an
`EventSink`. The CLI reporter, web SSE stream, and persistent log should consume
the same events.

Events and logs must redact SSH passwords, browser session credentials, AskPass
responses, and Jupyter tokens before reaching any sink.

## Outputs and persistence

Every invocation currently retains:

```text
<remote-run-directory>/
├── backpack/                 cloned repository and generated outputs
├── run-command.log           interactive mode, when used
├── execute-command.log       execute mode, when used
└── .floability-remote-run
```

Automatic output download is not implemented. A future download API should
operate inside the known workspace or run directory, reject path traversal, and
apply file-count and size limits.

## Shared implementation requirements

When adding CLI or web functionality:

- place deployment behavior in Python services, not in argument parsing or
  FastAPI routes;
- make the CLI and web API call the same services;
- represent configuration with typed objects rather than `argparse.Namespace`;
- report progress through structured events;
- provide explicit confirmation and cancellation interfaces;
- preserve cleanup when a browser disconnects or the CLI receives Ctrl+C;
- keep secrets out of commands, URLs, exception messages, and logs;
- validate repository URLs, refs, entrypoints, remote paths, and local ports;
- retain enough state to report an exact remote directory after failure; and
- do not claim that an interactive session is ready until the SSH tunnel is
  accepting local connections.

## Current CLI examples

Interactive deployment:

```bash
floability-remote run \
  --target <USER@LOGIN_NODE> \
  --backpack https://github.com/floability-hub/matrix-multiplication.git \
  --batch-type slurm
```

Finite execution:

```bash
floability-remote execute \
  --target <USER@LOGIN_NODE> \
  --backpack https://github.com/floability-hub/matrix-multiplication-script.git \
  --batch-type slurm
```

These commands and the future web API should differ only in how they collect
input and present events. Their remote behavior should remain shared.
