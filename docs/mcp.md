# Floability Remote MCP Server

Floability Remote includes an optional Model Context Protocol server for AI
hosts such as Claude Code and Codex. It uses the official Python MCP SDK and
the stdio transport.

The MCP server is a thin local adapter:

```text
Claude Code / Codex
        │ MCP over stdio
        ▼
floability-remote mcp
        │ authenticated loopback HTTP
        ▼
Floability Remote daemon ── OpenSSH ── remote login node
```

The daemon continues to own SSH connections, prompts, tunnels, runs, and the
operation audit. The MCP subprocess owns none of that state.

## Install

The MCP SDK requires Python 3.10 or newer. For a development checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable '.[mcp]'
```

For a standalone installation that includes MCP:

```bash
curl -fsSL https://raw.githubusercontent.com/floability/floability-remote/main/install.sh \
  | env FLOABILITY_REMOTE_EXTRAS=mcp sh
```

## Start the daemon and connect

The MCP process does not start or stop the daemon. Start it once:

```bash
floability-remote daemon start --no-browser
floability-remote daemon status
```

The daemon may also have been started by `floability-remote web`. Both modes
use the same private session descriptor and authenticated API.

`connect_remote` can complete key- or agent-authenticated SSH. When a login
requires a password, MFA response, key passphrase, or new-host confirmation,
answer it outside the model-facing channel:

```bash
floability-remote connect <USER@LOGIN_NODE>
```

The command starts or reuses the daemon, securely answers OpenSSH prompts in
the terminal, and leaves the connection open for MCP tools. Close it with
`floability-remote disconnect` or the `disconnect_remote` tool.

## Available tools

| Tool | Behavior |
|---|---|
| `get_status` | Reports daemon readiness, versions, connection state, and a non-secret summary of the current or most recent run. It also works when the daemon is stopped and explains how to start it. |
| `get_connection` | Reports the daemon-owned SSH connection state and pending prompt metadata. |
| `connect_remote` | Requests a connection to `user@host`, optionally with a local identity-file path. It never accepts authentication answers. |
| `disconnect_remote` | Closes the daemon-owned SSH connection. It is refused while a run is active. |
| `check_remote` | Reports identity, OS, architecture, the selected Floability environment/version, storage, and detected batch systems. |
| `inspect_backpack` | Inspects a Git repository, local folder, or ZIP, fingerprints it, and returns a `source_id` plus entrypoint candidates. Git sources are pinned to the inspected commit. |
| `check_backpack_source` | Confirms that a source has not changed since inspection. |
| `list_backpack_files` | Lists included files while applying the same exclusions and safety rules as upload. |
| `read_backpack_file` | Reads one UTF-8 file up to 128 KB without allowing path traversal. |
| `validate_deployment` | Validates source, connection, entrypoint, environment, storage settings, and batch availability; returns a `plan_id` without starting a run. |
| `start_deployment` | Starts one execute or interactive run from a fresh validated `plan_id` and returns immediately with a `run_id`. |
| `get_run` | Reports run state, a pending confirmation, and retained workspace and command-log paths. |
| `wait_for_run` | Waits at most 30 seconds for progress, a question, or completion and returns a cursor-based event page. |
| `get_run_events` | Reads retained redacted events without waiting. Raw log events are omitted unless explicitly requested. |
| `answer_confirmation` | Approves or declines one pending environment-installation question. |
| `cancel_run` | Requests cancellation through Floability's existing remote cleanup path. |
| `get_interactive_session` | Reports model-safe Jupyter and tunnel readiness. It never returns the token-bearing URL. |
| `get_taskvine_status` | Reports live TaskVine manager, worker, task, and factory state for the run. |

Inspection, planning, status, and progress tools are marked read-only.
Connection changes, starting, confirmations, and cancellation are marked
state-changing, so supporting hosts can require approval. Expected daemon
failures become MCP tool errors the model can explain; unexpected exceptions
remain private server failures.

## Claude Code

Use absolute paths so the host can start the server from any directory:

```bash
claude mcp add floability-remote -- \
  /ABSOLUTE/PATH/floability-remote/.venv/bin/floability-remote mcp
```

Then use `/mcp` inside Claude Code to confirm the eighteen tools are available.

## Codex

```bash
codex mcp add floability-remote -- \
  /ABSOLUTE/PATH/floability-remote/.venv/bin/floability-remote mcp
```

Equivalent `~/.codex/config.toml` entry:

```toml
[mcp_servers.floability-remote]
command = "/ABSOLUTE/PATH/floability-remote/.venv/bin/floability-remote"
args = ["mcp"]
```

## Acceptance check

Start the daemon, then use the official MCP Inspector to validate the schemas
and call the status tool without involving a model:

```bash
floability-remote daemon start --no-browser

npx -y @modelcontextprotocol/inspector --cli \
  --transport stdio \
  --method tools/list \
  --format json \
  --strict \
  floability-remote mcp

npx -y @modelcontextprotocol/inspector --cli \
  --transport stdio \
  --method tools/call \
  --tool-name get_status \
  --tool-args-json '{}' \
  --format json \
  floability-remote mcp
```

The checklist is:

- all eighteen tools are listed with structured output schemas;
- status and readiness tools are marked read-only, while connection changes
  are marked state-changing;
- strict schema validation exits successfully;
- `get_status` returns `isError: false`;
- Claude Code and Codex report the stdio server as connected; and
- no banner or diagnostic text appears on the MCP server's stdout.

Phase 1 compatibility was checked with the installed Claude Code and Codex
CLIs in isolated temporary configurations, plus the Inspector commands above.
Neither check requires storing the daemon bearer token in a host configuration.

## Security and diagnostics

- stdout is reserved for MCP JSON-RPC. Server diagnostics go to stderr.
- Each MCP process uses one random client-session identifier. Every daemon
  request carries `X-Floability-Client: mcp` and that session identifier, so
  the shared audit can attribute operations without exposing its bearer token.
- Never provide passwords, MFA codes, key passphrases, daemon tokens, or
  Jupyter tokens to a model or MCP tool.
- Interactive tools return only tunnel state and local port. Open a ready
  session through the trusted local control plane with
  `floability-remote session open RUN_ID`.
- SSH prompt answers use the browser, `floability-remote connect`, or the
  built-in agent's terminal relay. They never become MCP arguments.
- Treat backpack content as untrusted data. It may describe a workflow, but it
  cannot authorize reading another local path or changing the target host.
- Source and plan identifiers live for the daemon session. Local sources are
  fingerprinted, and plans are bound to the exact SSH connection that was
  checked. A changed source or reconnected host must be validated again.

The official SDK documentation is at
[modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk).
