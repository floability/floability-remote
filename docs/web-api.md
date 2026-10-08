# Floability Remote Web API

`floability-remote web` serves a local web interface and a versioned JSON API
on `127.0.0.1`. The packaged HTML, CSS, and JavaScript client uses only this
API, so another frontend (for example React) can replace
`src/floability_remote/web/static/` without backend changes.

The API is an adapter. Validation, environment preparation, workspaces,
Floability execution, events, cancellation, and cleanup live in shared Python
services used by both the CLI and the API:

```text
config.py        RunConfig and validation (run_config_issues, validate_run_config)
events.py        Event, EventSink, Emitter, Redactor, RedactingSink
interaction.py   ConfirmationRequest/Confirm callback and CancelToken
environment.py   remote Conda and Floability setup
workspace.py     remote run directory and backpack clone
workflow.py      RemoteWorkflow: run/execute orchestration, tunnel, cleanup
```

## Starting the server

```bash
floability-remote web [--port PORT] [--no-browser]
```

The command binds to `127.0.0.1`, prints a one-time sign-in link containing a
random session token, opens it in the default browser unless `--no-browser`
is given, and stops on Ctrl+C. Uvicorn access logging is disabled so the
token is never logged.

## Security model

| Check | Behavior |
|---|---|
| Host header | Must be `127.0.0.1:<port>` or `localhost:<port>`; otherwise `400 invalid_host`. Defeats DNS rebinding. |
| Origin | When present, must be this server's origin. Without `Origin`, `Sec-Fetch-Site` must be `same-origin` or `none`. Otherwise `403 cross_origin`. Another local port is a different origin. |
| Session | `GET /?token=<token>` sets an `HttpOnly`, `SameSite=Strict` cookie named `floability_remote_session_<port>` and redirects to `/`. API calls need that cookie or `Authorization: Bearer <token>`; otherwise `401 unauthorized`. |
| Headers | Strict CSP (`'self'` only, no inline scripts), `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cache-Control: no-store` on API responses. |

`GET /api/v1/health` is the only unauthenticated API route.

## Conventions

- All routes are under `/api/v1`. Breaking changes require a new version
  prefix.
- OpenAPI: `GET /api/v1/openapi.json` (authenticated). Interactive docs pages
  are disabled because they load assets from external CDNs.
- Request bodies reject unknown fields.
- Configuration fields use dotted `RunConfig` paths such as
  `connection.target` and `environment.env_name`. Validation issues and
  request-shape errors use the same paths, so clients can attach messages to
  inputs generically.

### Error envelope

Every non-2xx response has this shape:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "The request body is not valid.",
    "issues": [{ "field": "connection.target", "message": "Field required", "flag": null }]
  }
}
```

Codes: `bad_request`, `invalid_host`, `unauthorized`, `cross_origin`,
`forbidden`, `not_found`, `method_not_allowed`, `conflict`, `invalid_request`.

## Endpoints

### `GET /api/v1/health`

```json
{ "status": "ok", "version": "0.1.0" }
```

### `GET /api/v1/meta`

Server-owned choices and defaults, so clients do not hard-code them:

```json
{
  "version": "0.1.0",
  "api_version": "v1",
  "modes": ["run", "execute"],
  "batch_types": ["local", "slurm", "condor", "uge"],
  "defaults": {
    "env_name": "floability-env",
    "remote_root": "~/.cache/floability-remote/runs",
    "jupyter_port": 8888
  },
  "features": {
    "validate": { "available": true,  "milestone": "M1", "description": "..." },
    "connect":  { "available": false, "milestone": "M2", "description": "..." }
  }
}
```

Clients should enable controls from `features` instead of assuming them.

### `POST /api/v1/runs/validate`

Checks a run configuration with exactly the rules the CLI applies
(`config.run_config_issues`). It does not contact a remote host. The local
identity file, when given, must exist on this computer.

Request (`RunRequest`; omitted fields take CLI defaults):

```json
{
  "mode": "run",
  "connection": { "target": "user@login.example.edu", "identity_file": null },
  "backpack": { "repository": "https://github.com/floability-hub/matrix-multiplication.git", "ref": "" },
  "batch_type": "slurm",
  "environment": {
    "env_name": "floability-env",
    "floability_version": "",
    "conda_executable": "",
    "reinstall_miniforge": false
  },
  "entrypoint": "",
  "remote_root": "~/.cache/floability-remote/runs",
  "jupyter_port": 8888,
  "local_port": null
}
```

Response, always `200` for a well-formed body:

```json
{
  "valid": false,
  "issues": [
    { "field": "connection.target", "message": "must be an SSH host or user@host, not an option.", "flag": "--target" }
  ],
  "command": null
}
```

When `valid` is true, `command` is the equivalent shell-quoted
`floability-remote` command produced by `cli.cli_command`. An issue with a
`flag` describes the field relative to its name; the CLI prints
`<flag> <message>`, and the web client prints `<field label> <message>`.

### Connection

The web server keeps at most one authenticated SSH connection
(`connection.ConnectionManager`). It authenticates through OpenSSH with
`SSH_ASKPASS_REQUIRE=force` (OpenSSH 8.4 or newer): every password, key
passphrase, keyboard-interactive (MFA), and unknown host-key question is
relayed to the page through a private Unix socket (`askpass.py`). Answers stay
in memory, are written only to ssh's pipe, and are never logged or stored.
After sign-in, commands reuse the control master with `BatchMode=yes`, so they
can never fall back to a terminal prompt. The master uses keepalives and is
closed on Disconnect, on server shutdown, or after one idle hour.

| Method and path | Purpose |
|---|---|
| `GET /api/v1/connection` | Current state. Poll it while `state` is `connecting`. |
| `POST /api/v1/connection` | `{"target", "identity_file"}` → `202` and `connecting`. `409` if already connecting or connected; `422 invalid_config` with field issues. |
| `POST /api/v1/connection/prompts/{id}` | `{"answer": "..."}` or `{"cancel": true}` → `204`; `404 prompt_not_found` if stale. |
| `DELETE /api/v1/connection` | Disconnect or cancel sign-in. `409 run_active` while a run is active. |

```json
{
  "state": "connecting",
  "target": "user@login.example.edu",
  "identity_file": null,
  "remote_user": null,
  "remote_host": null,
  "prompt": { "id": "49c5…", "kind": "secret", "message": "user@login.example.edu's password:" },
  "error": null,
  "connected_at": null
}
```

`prompt.kind` is `secret` (hidden text) or `confirm` (yes/no, such as trusting
a new host key; answer `yes` or `no`). `state` becomes `connected` (with
`remote_user` and `remote_host`) or `failed` (with ssh's `error`). A lost
master is reported as `failed`.

### Runs

One run may be active at a time, on the open connection. Runs continue if the
browser tab closes; reopening the page restores the connection and the latest
run. Stopping the server (Ctrl+C) cancels an active run with remote cleanup
and closes the connection.

| Method and path | Purpose |
|---|---|
| `POST /api/v1/runs` | `RunRequest` with `mode` `execute` or `run` → `201` `RunResponse`. `409` without a matching connection or while another run is active; `422 invalid_config`. |
| `GET /api/v1/runs/current` | The active or most recent run; `404` if none. |
| `GET /api/v1/runs/{id}` | Run state, result paths, Jupyter link, and any pending `confirmation`. |
| `GET /api/v1/runs/{id}/events` | Server-Sent Events (below). |
| `POST /api/v1/runs/{id}/cancel` | `202`; cancels an execution or stops an interactive session. Cleanup progress arrives as events. |
| `POST /api/v1/runs/{id}/confirmations/{id}` | `{"approved": true}` → `204`. |

`RunResponse` fields: `id`, `mode`, `state` (`running`, `completed`, `failed`,
`cancelled`), `target`, `repository`, `ref`, `batch_type`, `entrypoint`,
`created_at`, `finished_at`, `message`, `result` (`run_dir`, `backpack_dir`,
`log_path`), `jupyter_url`, `confirmation`, `cancel_requested`, and
`event_count`.

#### Interactive sessions (`mode: run`)

1. The shared workflow starts `floability run`, detects Floability's Jupyter
   announcement, and registers the Jupyter token for redaction.
2. It opens a local forward (`127.0.0.1:<local_port>` → the remote Jupyter
   port) through the open control master and waits until the local port
   accepts connections.
3. Only then does it emit `ready` with `data.url`
   (`http://127.0.0.1:<local_port>/lab/?token=…`). `RunResponse.jupyter_url`
   holds the same link while the session is ready and is cleared when the run
   ends. The web page shows it as **Open JupyterLab**.
4. The run stays `running` until it is stopped with `POST /runs/{id}/cancel`.
   Floability receives SIGINT and stops Jupyter and its workers; the
   workflow then closes the tunnel and cancels the forward on the master
   (`ssh -O cancel`), because the connection outlives the run. The run ends
   `cancelled` with the message "The interactive session was stopped."

`local_port` selects the local port (otherwise a free port is chosen) and
`jupyter_port` the requested remote port.

Lifecycle: the session belongs to the web server, not the browser tab.
Closing the tab leaves Jupyter running so it can still be used in its own tab;
reopening the page shows the link again. Stopping the server stops the
session. There is no idle timeout yet.

The event stream replays every event, then follows new ones:

```text
retry: 2000

id: 0
event: run
data: {"kind": "step", "message": "Connecting to login.example.edu...", ...}

event: end
data: {}
```

`id` is the event's position. Resume with the `Last-Event-ID` header (sent
automatically by `EventSource`) or `?after=<id>`. The stream closes with
`event: end` after the terminal event; comment lines keep idle streams open.

## Events

Workflows report progress only through `EventSink`. The CLI renders events
with `CliReporter`; the run event stream sends `Event.to_dict()`:

```json
{ "kind": "step", "message": "Cloning backpack from ...", "step": 3, "total": 5, "data": {}, "timestamp": 1760000000.0 }
```

| Kind | Meaning | Notable `data` |
|---|---|---|
| `step` | Numbered top-level stage began | — |
| `detail` | Supporting information | `run_dir`, `log_path`, or `backpack_dir` when relevant |
| `progress` | Recognized Floability milestone (once per run) | — |
| `log` | Raw remote output; `message` keeps its newline | — |
| `ready` | Jupyter is reachable through the local tunnel | `url`, `local_port` |
| `warning` | Non-fatal problem, such as unconfirmed cleanup | `run_dir` |
| `confirmation` | The run waits for approval (for example `install_miniforge`) | `id`, `key` |
| `cancelling` | Cleanup started after Ctrl+C or cancellation | — |
| `completed` / `failed` / `cancelled` | Terminal outcome; always the last event | `run_dir`, `backpack_dir`, `log_path` |

Secrets: the workflow registers the Jupyter token with a `Redactor` before the
line that contains it reaches any sink. Wrap persistent logs and streams in
`RedactingSink`; it redacts every event except `ready`, whose `url` is the
link the authenticated user opens (an **Open Jupyter** link in the web UI).
The CLI terminal prints the full link, as before.

## Capability matrix

| Capability | Shared service | CLI | Web API | Notes |
|---|---|---|---|---|
| Validate configuration | `config.validate_run_config` | yes | `POST /runs/validate` | |
| Key, agent, and `~/.ssh/config` auth | `ssh.SSHSession` | yes | `POST /connection` | |
| Password, MFA, and host-key prompts | `askpass.AskPassBroker` | OpenSSH terminal prompts | `connection.prompt` + `POST /connection/prompts/{id}` | The CLI keeps OpenSSH's own terminal prompts |
| Persistent connection | `connection.ConnectionManager` | per command | yes | The CLI connects inside each command |
| Extra OpenSSH `-o` options | `ConnectionConfig.ssh_options` | `--ssh-option` | not exposed | Options such as `ProxyCommand` run local commands; use `~/.ssh/config` |
| Miniforge install confirmation | `interaction.Confirm` | prompt or `--yes` | `confirmation` event + `POST /runs/{id}/confirmations/{id}` | |
| Execute | `workflow.RemoteWorkflow` via `runs.RunManager` | `execute` | `POST /runs` | Background run, SSE events |
| Cancel and clean up | `RemoteWorkflow.cancel`, `CancelToken` | Ctrl+C | `POST /runs/{id}/cancel` | Same SIGINT→SIGTERM cleanup |
| Interactive Jupyter and tunnel | `RemoteWorkflow` | `run` | `POST /runs` with `mode: run` | `ready` event and `jupyter_url` carry the clickable link; stop with `/cancel` |
| Run history | — | — | later | SQLite, non-secret metadata only |
| File transfer (upload and download) | planned shared transfer service | planned | planned (M5); `transfer` feature is `available: false` | Over the open SSH connection; paths confined to the run directory; size and file-count limits |
| Concurrent runs | — | one per process | one active run | Deferred until lifecycle handling is reliable |

Known limitations:

- Cancellation is cooperative between setup steps. A remote setup script
  already running (for example a Conda install) finishes before the workflow
  stops; a running Floability process receives SIGINT immediately, then
  SIGTERM if it has not stopped after 45 seconds.
- The web run ID and the remote run-directory name are generated separately.
