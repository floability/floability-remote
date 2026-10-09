# Floability Remote Web UI Milestones

## Status

| Milestone | State |
|---|---|
| M0. Shared Python services for CLI and web | Done |
| M1. Local web server, `/api/v1`, configuration validation | Done |
| M2. SSH connection screen (AskPass prompts) | Done |
| M3. `execute` vertical slice | Done |
| M4. Interactive Jupyter runs | Done (no idle timeout yet) |
| M5. Run history and file transfer | In progress; single-file download done |
| M6–M8 | Planned |

The API contract, security model, event schema, and CLI/web capability matrix
are maintained in [web-api.md](web-api.md). Record any capability that is
intentionally exposed to only one client there.

## Direction

Build a local-first web interface around the existing SSH implementation. The
user will start the application on their laptop:

```bash
floability-remote web
```

The command will start a server bound to `127.0.0.1`, open the interface in the
default browser, and keep remote credentials and SSH tunnels on the user's
machine.

Use FastAPI for the local API and packaged static HTML, CSS, and JavaScript
modules (no templates and no build step) for the first version. Server-Sent
Events (SSE) can stream progress and log updates to the browser. This avoids
requiring Node.js and a separate React or Next.js application for the PoC. The
JavaScript calls only the versioned JSON API (`/api/v1`) through
`static/js/api.js`, so a React frontend can later replace the `static/`
directory without backend changes.

Globus Compute is deferred until the SSH-based web experience works. It can
later become an optional backend for non-interactive execution, but it will
not be required for the first web interface.

## Initial Architecture

```text
Browser on the user's laptop
        |
        | HTTP on 127.0.0.1 only
        v
Floability Remote FastAPI service
        |
        | managed SSH connection and port forwarding
        v
Remote login node
        |
        | floability run or floability execute
        v
TaskVine workers through local, Slurm, or Condor
```

The web layer should call the same application services as the CLI. It must
not duplicate environment setup, backpack cloning, Floability execution, or
output parsing.

## Milestones

### 0. Share Python services between the CLI and web API (done)

The CLI was refactored without changing its output (verified byte for byte
against the previous implementation):

- typed `RunConfig` and shared validation in `config.py`;
- structured events and an `EventSink` in `events.py`, rendered for the
  terminal by `cli_reporter.py`;
- a `Confirm` callback for Miniforge installation instead of `input()`;
- a thread-safe `CancelToken` and `RemoteWorkflow.cancel()` that run the same
  SIGINT/SIGTERM cleanup as Ctrl+C;
- `workspace.py` for the remote run directory and clone; and
- Jupyter-token redaction for logs while the `ready` event keeps the
  clickable link.

### 1. Start a functional local web server (done)

Implemented as described below, with the API under `/api/v1`, a
`GET /api/v1/meta` endpoint for server-owned choices and feature flags, and
`POST /api/v1/runs/validate`, which applies the CLI's validation rules and
returns the equivalent CLI command. FastAPI and Uvicorn are regular
dependencies rather than an optional group so `floability-remote web` works
after a normal installation.

Add an optional `web` dependency group containing FastAPI, Uvicorn, and the
small set of packages required to serve templates and static assets.

Add:

```bash
floability-remote web
```

The first server should:

1. bind only to `127.0.0.1` by default;
2. select an available port or accept `--port`;
3. print the local URL and open it in the default browser;
4. serve a simple Floability Remote page;
5. expose `GET /api/health`; and
6. stop cleanly with Ctrl+C.

The page should initially contain the planned connection and backpack fields,
even before they launch a real run. It should clearly identify which controls
are not implemented yet.

Success condition: `floability-remote web` opens a working page, the health
endpoint responds, and the server shuts down cleanly.

### 2. Add an SSH connection screen

Collect:

- login node;
- username;
- authentication method; and
- optional identity-file location.

Support SSH keys, SSH agent authentication, and SSH configuration first. Add
password authentication through a local, in-process SSH session so sites such
as CRC that only support passwords can be used from the browser.

Password rules:

- send it only from the browser to the loopback service;
- keep it only in process memory for the active connection;
- never write it to a database, file, command line, exception, or log;
- redact authentication fields from request logging; and
- discard it after disconnect or session timeout.

Present and verify an unknown server's host-key fingerprint before trusting
it. Maintain one SSH session with keepalives and expose an explicit Disconnect
button.

Success condition: the page connects to a password-only CRC account and a
key-based test host, runs `whoami`, and disconnects without retaining the
password.

### 3. Complete one `execute` vertical slice

Add a run form with:

- public backpack Git URL;
- batch type;
- optional entrypoint; and
- optional Floability version.

Run the existing environment setup and `execute` workflow as a background
operation. Give every run a local ID and stream structured progress through
SSE:

```text
Connecting
Checking remote tools
Preparing the Floability environment
Cloning the backpack
Executing the backpack
Completed or failed
```

The page should show concise progress by default, make the complete log
available on demand, and support cancellation with confirmed remote cleanup.

Success condition: the matrix-multiplication-script backpack completes from
the browser and the page displays its final state, remote directory, and log.

### 4. Add interactive Jupyter runs

Reuse the existing `run` workflow and Jupyter-output parser. When Floability
reports the remote host, port, and token:

1. create a local SSH port forward through the managed SSH session;
2. present the resulting `http://localhost:<PORT>/...` URL;
3. provide an Open Jupyter button;
4. keep the connection and tunnel alive while the run is active; and
5. provide a Stop button that terminates Floability and closes the tunnel.

If the requested local port is unavailable, choose another port and display
the actual URL. A closed browser tab must not silently leave an unbounded
remote process running; use an explicit lifecycle and idle policy.

Success condition: a user launches an interactive backpack, opens Jupyter in
the browser, and stops the run cleanly without entering an SSH command.

### 5. Add run history and file transfer (single-file download implemented)

The first M5 slice is available in both clients. After a run ends, it lists
approved regular files from the workflow, top-level instance logs, metadata,
metrics, and the Floability Remote command log. Symbolic links and generated
software or worker scratch directories are excluded. The web page provides a
Download button for each file; `floability-remote download` provides the same
inventory in the terminal. Each file is revalidated before transfer, local
files are not overwritten, listings are limited to 2,000 files, and downloads
to 100 MiB.

Remaining scope:

- **Upload** files from the laptop into a run's workspace, for example input
  data or an edited notebook, before or between runs.
- Multi-file selection, archives, progress, cancellation, and large transfers.
- Persistent local history so downloads survive a web-server restart without
  manually supplying the remote run directory.
- Never overwrite local or remote files without confirmation.

Store non-secret local metadata for recent runs:

- local run ID;
- target alias or hostname;
- backpack and ref;
- mode and batch type;
- timestamps and final status;
- remote run directory; and
- local log location.

Add explicit output manifests and a transfer backend suitable for large files.
Do not recursively download an unknown remote directory without limits.

Success condition: the user can reopen the local UI, inspect previous run
records, reconnect when necessary, and download selected outputs.

### 6. Improve the backpack selection experience

Add:

- a curated dropdown of public `floability-hub` repositories;
- direct public Git URL entry;
- branch, tag, or commit selection;
- clear entrypoint guidance when multiple workflows exist; and
- validation messages before remote setup begins.

Defer ZIP upload until URL-based runs and output retrieval are reliable. ZIP
support will require safe extraction, size limits, isolation, upload progress,
and cleanup rules.

Success condition: common public backpacks require only a target, a backpack
selection, and a Run or Execute click.

### 7. Package and harden the local application

Bundle templates and static assets in the Python package and include the web
dependencies in the installer. Add tests for the API, session state, progress
events, cancellation, credential redaction, and tunnel cleanup.

Security boundaries for the local version:

- bind to loopback unless the user explicitly chooses otherwise;
- reject cross-origin requests;
- use a random per-process browser-session token;
- never place passwords or Jupyter tokens in logs;
- validate Git URLs, entrypoints, filenames, and local ports;
- verify SSH host keys; and
- terminate active tunnels and connections during shutdown.

Success condition: a fresh installation can launch the web interface and run
both execute and interactive workflows without a development checkout.

### 8. Add Globus Compute as an optional backend

After the SSH web UI works, introduce a backend boundary such as:

```text
SSHBackend
GlobusComputeBackend
```

The first Globus backend should support `execute` only. It can submit a
controlled Floability launcher to a configured endpoint and use Globus task
IDs for asynchronous status. It should not claim to provide Jupyter port
forwarding.

Evaluate Globus primarily for hosted-web deployments where the application
must not receive HPC passwords. Keep endpoint installation and lifecycle
requirements visible to the user.

Success condition: the same execute form can target either an SSH connection
or a configured Globus Compute endpoint without duplicating Floability
workflow logic.

## PoC Boundaries

- Run the web server locally and bind it to loopback.
- Preserve the existing CLI behavior.
- Support one active SSH connection initially.
- Support one active run initially; add concurrency after lifecycle handling
  is reliable.
- Begin with public Git repositories.
- Treat backpacks as untrusted code executed under the remote user's account.
- Do not store remote passwords.
- Do not expose the PoC as a public hosted service.
- Defer Globus, ZIP upload, multi-user hosting, and collaboration features.

## M2 and M3 implementation notes

- OpenSSH remains the only transport. `askpass.py` relays password, MFA, and
  host-key prompts to the browser with `SSH_ASKPASS_REQUIRE=force`; no
  in-process SSH library was added.
- `connection.ConnectionManager` keeps one control master alive with
  keepalives; `runs.RunManager` runs the shared `RemoteWorkflow` on it.
- The launcher restores the default SIGINT action (through the environment's
  Python) before exec'ing Floability. Bash starts background jobs with SIGINT
  ignored, so the CLI's and web's "ask Floability to clean up with SIGINT"
  step could previously only succeed through the SIGTERM fallback. Job control
  (`set -m`) is deliberately not used: it makes util-linux `setsid` fork, so
  the recorded PID would not be Floability's and Stop would not reach it.
  `tests/test_remote_scripts.py` runs the real launch and stop scripts with a
  util-linux-style `setsid` to guard this.
- Verified end to end through the web API against a local stand-in remote
  (fake `ssh`, `conda`, and `floability`): password prompt, connect, probe,
  clone, launch, streamed progress, completion, SIGINT cancellation, and
  shutdown cleanup. Not yet verified against a real cluster such as CRC.

## Immediate Next Step

Test M2–M4 against CRC (password and Duo/MFA, Slurm, Jupyter on the login
node) and a key-based host. In particular, confirm how the site's OpenSSH
handles a multiplexed `-N -L` client: the workflow accepts either a client that
stays running or one that exits after handing the forward to the master, and
always cancels the forward when the session ends.

Decide whether interactive sessions need an idle policy (for example a
maximum lifetime or Jupyter's own culling) before M5.

M4 notes: interactive sessions reuse the shared `RemoteWorkflow`; the web
server owns the session lifecycle (a closed tab does not stop Jupyter; Stop or
server shutdown does). The page shows **Open JupyterLab** and the link only
after the local tunnel accepts connections, and a two-click **Stop session**
guards against losing unsaved notebook work. Verified end to end against the
local stand-in remote: tunnel reachable (HTTP 200), Stop sends SIGINT,
Jupyter and the tunnel close, the forward is cancelled, and the SSH
connection stays open for the next run.
