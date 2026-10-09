# How Floability Remote Works

This document explains the techniques that make `floability-remote` work:
how one SSH sign-in is reused for everything, how passwords and MFA reach the
browser without being stored, how long the connection lives, how Jupyter is
reached without a manual SSH tunnel, and how remote processes are started,
stopped, and inspected safely.

For the API contract see [web-api.md](web-api.md); for Floability concepts see
[floability-deployment-basics.md](floability-deployment-basics.md).

## Current implementation status

| Capability | Status |
|---|---|
| SSH keys, passwords, passphrases, and MFA | Complete |
| Managed remote Floability environment | Complete |
| Execute mode and interactive Jupyter sessions | Complete |
| Cancellation and remote cleanup | Complete |
| Remote-host readiness checks | Complete |
| Single-file result downloads | Complete |
| Persistent run history and reconnecting after the local server exits | Planned |

## Contents

1. [Big picture](#1-big-picture)
2. [One SSH connection for everything](#2-one-ssh-connection-for-everything)
3. [How long the SSH session lasts](#3-how-long-the-ssh-session-lasts)
4. [Signing in from the browser (AskPass relay)](#4-signing-in-from-the-browser-askpass-relay)
5. [Remote orchestration without installing an agent](#5-remote-orchestration-without-installing-an-agent)
6. [Preparing the remote environment](#6-preparing-the-remote-environment)
7. [Starting and stopping Floability safely](#7-starting-and-stopping-floability-safely)
8. [Reaching Jupyter without a manual SSH tunnel](#8-reaching-jupyter-without-a-manual-ssh-tunnel)
9. [One backend, two clients](#9-one-backend-two-clients)
10. [Local web server security](#10-local-web-server-security)
11. [Safe file downloads](#11-safe-file-downloads)
12. [Remote-host readiness check](#12-remote-host-readiness-check)
13. [Testing real scripts without a cluster](#13-testing-real-scripts-without-a-cluster)
14. [Limits and open questions](#14-limits-and-open-questions)

---

## 1. Big picture

```text
 LAPTOP                                                 REMOTE LOGIN NODE
 ┌───────────────────────────────────────────┐          ┌──────────────────────────────────┐
 │ Browser                                   │          │ sshd                             │
 │  ├─ Floability Remote page ──┐ HTTP       │          │  │                               │
 │  └─ JupyterLab tab ───────┐  │ 127.0.0.1  │          │  ├─ bash -s  (probe, clone, ...) │
 │                           │  ▼            │          │  ├─ bash -s  LAUNCH_FLOABILITY   │
 │  floability-remote web    │  FastAPI      │          │  │     └─ floability run/execute │
 │   (or the CLI)            │  + services   │  ONE SSH │  │          ├─ TaskVine manager  │
 │                           │     │         │  TCP     │  │          └─ JupyterLab :8888  │
 │                           │     ▼         │  CONN.   │  │               ▲               │
 │                           │  OpenSSH ═════╪══════════╪══╡ (multiplexed channels)        │
 │                           │  control      │          │  │               │               │
 │   127.0.0.1:<local port> ─┘  master       │          │  └─ port forward ┘               │
 └───────────────────────────────────────────┘          └───────────────┬──────────────────┘
                                                                        │ batch system
                                                                        ▼
                                                         TaskVine workers on compute nodes
                                                         (local, Slurm, Condor, or UGE)
```

Floability Remote never reimplements Floability. It prepares the login node,
starts the released `floability` command, and manages everything around it:
authentication, progress, the Jupyter tunnel, cleanup, and file retrieval.

## 2. One SSH connection for everything

Every remote operation travels over **one authenticated OpenSSH connection**,
using OpenSSH's connection multiplexing (`ControlMaster`):

```text
             ssh -o ControlMaster=yes -o ControlPath=/tmp/fr-<pid>-<id>.sock -fN host
                              │   (authenticates once: key, agent, password, MFA)
                              ▼
               ┌──────── control master (background ssh) ────────┐
 ssh -S sock … │  channel: bash -s  PROBE                          │
 ssh -S sock … │  channel: bash -s  CLONE_BACKPACK                 │──── one TCP ───▶ sshd
 ssh -S sock … │  channel: bash -s  LAUNCH_FLOABILITY  (long)      │     connection
 ssh -S sock … │  forward: 127.0.0.1:L → 127.0.0.1:8888 (Jupyter)  │
 ssh -S sock … │  channel: bash -s  DOWNLOAD_FILE                  │
               └────────────────────────────────────────────────────┘
```

Why it matters:

- **Sign in once.** Password and MFA (for example Duo) are asked a single time
  even though a run uses dozens of SSH commands.
- **No new transport.** Plain OpenSSH is used, so existing `~/.ssh/config`
  aliases, keys, agents, `ProxyJump`, and site settings keep working.
- **Fast.** Later commands open a channel on an existing connection instead of
  a new TCP connection and handshake.

Implementation: `ssh.SSHSession` (`start`, `run_script`, `start_script`,
`run_script_to_file`, `start_tunnel`, `cancel_tunnel`, `is_alive`, `close`).

## 3. How long the SSH session lasts

The CLI and the web interface use the same `SSHSession`, with different
lifetimes:

| | CLI (`run`, `execute`, `download`, `check-cluster`) | Web interface |
|---|---|---|
| Opened | At the start of the command | When you click **Connect** |
| Prompts | OpenSSH's own terminal prompts | Relayed to the browser (section 4) |
| Closed | Explicitly at the end of the command (`ssh -O exit`) | **Disconnect**, server Ctrl+C, idle timeout, or network loss |
| `ControlPersist` (idle lifetime) | 300 s (a safety net; the command closes it first) | **1 hour** with no active channel |
| Keepalives | From your `~/.ssh/config`, if any | `ServerAliveInterval=30`, `ServerAliveCountMax=4` |
| Connection timeout | OpenSSH default | `ConnectTimeout=20` |
| Reused by later commands | Within the one command | Across all runs, checks, and downloads until disconnect |

What this means for the web interface:

```text
 Connect ──▶ connected ───────────────────────────────────────────────▶ ...
             │  run / Jupyter / download open channels; while any       │
             │  channel is open the master is never idle                │
             │                                                          │
             ├─ no channel for 1 hour ────────────▶ master exits itself  │
             ├─ network silent ≈ 2 min (30 s × 4) ─▶ master exits        │
             │   (laptop sleep, Wi-Fi change, VPN drop)                  │
             ├─ Disconnect / server Ctrl+C ───────▶ ssh -O exit          │
             └─ cluster policy (sshd idle limits, forced re-auth) ──────▶ remote side ends it
```

- An active run, an open Jupyter tunnel, or a transfer keeps the connection
  busy, so the one-hour idle limit only applies between activities.
- While authentication is in progress, the page polls for prompts and state
  changes. After connection, the server checks the master before operations,
  when the page is restored, and after runs. A dead master is reported as
  **failed: "The SSH connection was lost. Connect again."**
- Ending the connection is safe for the cluster: the server stops an active run
  first (section 7). Runs do not yet survive a lost connection; see section 14.
- Pending sign-in prompts expire after 5 minutes; a pending Miniforge
  confirmation expires after 15 minutes.

## 4. Signing in from the browser (AskPass relay)

A browser cannot type into a terminal. Instead of embedding a second SSH
implementation, Floability Remote uses an OpenSSH feature: with
`SSH_ASKPASS_REQUIRE=force` (OpenSSH 8.4+), ssh runs an external program for
**every** question it would normally ask on the terminal.

```text
 Browser          Local server (ConnectionManager)       askpass helper        ssh (master)       Cluster
    │  Connect ──────▶│                                        │                    │                │
    │                 │ start AskPassBroker (private Unix socket, random token)    │                │
    │                 │ spawn ssh with SSH_ASKPASS=<helper>, SSH_ASKPASS_REQUIRE=force                │
    │                 │──────────────────────────────────────────────────────────────▶│── handshake ──▶│
    │                 │                                        │◀── run helper ─────│◀─ "password:" ─│
    │                 │◀── prompt over Unix socket (+ token) ──│                    │                │
    │◀─ poll: prompt ─│                                        │                    │                │
    │── answer ──────▶│── answer over socket ─────────────────▶│── print to ssh ───▶│── answer ─────▶│
    │                 │                                        │  (repeats for MFA codes and          │
    │                 │                                        │   unknown host-key confirmations)    │
    │◀─ connected ────│◀──────────────────────────── authenticated ────────────────│                │
```

Properties:

- **Every prompt type works**: passwords, key passphrases, keyboard-interactive
  MFA (Duo), and "trust this new host key?" (shown with **Trust** / **Reject**).
- **Nothing is stored.** Answers live in memory only long enough to be written
  to ssh through the helper's stdout. They are never logged, written to disk,
  placed on a command line, or returned by the API.
- **Private channel.** The broker's socket lives in a `0700` temporary
  directory, and each request must carry a random token.
- **Never prompts again.** After sign-in, every command that reuses the master
  runs with `BatchMode=yes`, so it fails instead of silently starting a second
  interactive login.
- **Detached from the terminal.** The web server's ssh processes run in their
  own session, so Ctrl+C in the server's terminal cannot kill remote work
  before cleanup runs.

Implementation: `askpass.py` (broker and helper), `connection.ConnectionManager`.

## 5. Remote orchestration without installing an agent

Floability Remote does not install a resident agent or copy its own Python
package to the cluster. It may install user-scoped Miniforge and Floability
when they are missing. Each orchestration step is a small Bash program sent on
**standard input**:

```text
 local:  ssh -S sock host  'bash -s -- arg1 arg2 ...'   <  remote_scripts.CLONE_BACKPACK
                                     ▲                       ▲
                     arguments shell-quoted with shlex      script text never touches disk
```

- Arguments are quoted once, by `shell_command()`, and arrive as `$1`, `$2`, ...
  so user values (paths, URLs, Floability options) are never interpreted by a
  shell.
- Results come back as **marker lines** mixed with normal output:

  ```text
  Cloning into '/home/u/.cache/floability-remote/runs/2026…/backpack'...
  __FLOABILITY_REMOTE_RUN_DIR__=/home/u/.cache/floability-remote/runs/2026…
  __FLOABILITY_REMOTE_BACKPACK__=/home/u/.cache/floability-remote/runs/2026…/backpack
  ```

  `output.marker_values()` extracts them; everything else is shown as the log.
- All scripts live in `remote_scripts.py` and are checked with `bash -n` in the
  test suite.

## 6. Preparing the remote environment

`environment.ensure_environment()` makes the login node ready without `sudo`:

```text
 PROBE ─▶ Linux? Git? setsid? Conda? environment? Floability version?
   │
   ├─ no Conda ──▶ ask (CLI prompt / --yes / web confirmation)
   │               └─▶ INSTALL_MINIFORGE into ~/.local/share/floability-remote/miniforge
   │                   (download + SHA-256 check against the GitHub release digest)
   │
   ├─ environment missing or wrong Floability version
   │               └─▶ PREPARE_ENVIRONMENT (conda create/install from conda-forge)
   │
   └─ ready ──▶ CLONE_BACKPACK into ~/.cache/floability-remote/runs/<run-id>/backpack
```

The launcher environment (which provides the `floability` command) is kept
separate from each backpack's own software environment, which Floability
builds itself.

## 7. Starting and stopping Floability safely

### Launch

```text
 ssh channel ── bash LAUNCH_FLOABILITY
                 │
                 ├─ setsid python -c "<reset SIGINT>; exec floability …"  &   ← new session
                 │        │                                                      on the remote
                 │        └─ output ──▶ tee ──▶ <run_dir>/<mode>-command.log
                 │                         └──▶ ssh channel ──▶ local progress events
                 ├─ writes Floability's PID to <run_dir>/floability.pid
                 └─ waits for Floability and exits with its status
```

Two details took real debugging and are guarded by tests:

1. **SIGINT must reach Floability.** Bash starts background jobs with SIGINT
   *ignored*; Python then never raises `KeyboardInterrupt`, so Floability
   could not clean up its workers. A tiny Python wrapper restores the default
   SIGINT action before `exec`-ing Floability.
2. **The recorded PID must be Floability's.** Using bash job control
   (`set -m`) instead would make util-linux `setsid` fork, so the saved PID
   would belong to a short-lived parent and **Stop** would signal nothing.
   `tests/test_remote_scripts.py` runs the real scripts with a `setsid` that
   forks exactly like util-linux.

### Stop (Ctrl+C, Cancel, Stop session, server shutdown)

```text
 STOP_FLOABILITY <run_dir> INT 45 ──▶ Floability gets SIGINT, stops Jupyter and
        │                              workers, exits  ──▶ done
        └─ still running after 45 s
              └─ STOP_FLOABILITY <run_dir> TERM 15 ──▶ done, or a warning naming the
                                                         run directory to inspect
```

The run directory, backpack, and logs are always kept on the cluster.

## 8. Reaching Jupyter without a manual SSH tunnel

Normally a Floability user must read the port and token from the output and
type `ssh -N -L 8888:localhost:8888 user@cluster` themselves. Floability Remote
does all of that automatically, over the connection it already has.

```text
 1. Floability prints:  [jupyter] Detected JupyterLab URL with port 8888 and token abc…
 2. Workflow:  register the token for redaction  (logs show "token [REDACTED]")
 3. Pick a free local port L (or the one requested)
 4. Open the forward through the master:
        ssh -S sock -N -L 127.0.0.1:L:127.0.0.1:8888 host
 5. Wait until 127.0.0.1:L actually accepts a connection
 6. Only then emit "ready" with  http://127.0.0.1:L/lab/?token=abc…
        CLI: prints the clickable link     Web: "Open JupyterLab" button

 Browser tab ──▶ 127.0.0.1:L ══ ssh channel ══▶ login node 127.0.0.1:8888 (JupyterLab)
```

- **No extra sign-in.** The forward rides on the authenticated master.
- **Never reachable from the network.** Both ends bind to `127.0.0.1`; Jupyter
  is not exposed on the cluster's network interfaces, and the local port is not
  exposed on the laptop's network.
- **Clean shutdown.** When the session stops, the forward client is terminated
  and, because the web connection outlives the run, the forward is also removed
  from the master with `ssh -O cancel -L …`. (OpenSSH may keep a multiplexed
  forward on the master after its client exits, which would otherwise leak a
  local port.)
- **Secrets.** Structured log events sent to the terminal or browser redact the
  token. The `ready` event must retain it so the signed-in user can open the
  notebook. Floability's original remote output is also retained in
  `<run_dir>/run-command.log`; because that raw output can contain the Jupyter
  URL, treat the remote command log as sensitive and do not share it while the
  session is active.

### Could Jupyter be reached with no tunnel at all?

Not safely with the current design. The alternatives all trade away security
or depend on site infrastructure:

| Approach | Why not (yet) |
|---|---|
| Bind Jupyter to the login node's public interface | Exposes a code-execution service on the network; usually blocked by firewalls and site policy |
| A relay or reverse proxy service (ngrok-style) | Sends traffic through a third party; typically violates HPC policy |
| Site portals such as Open OnDemand | Good option where available, but site-specific; would be a separate backend |
| VS Code Remote-SSH port forwarding | Still an SSH tunnel, just managed by another tool |

The SSH forward is the one mechanism every SSH-accessible cluster already
allows, which is why the tool automates it rather than replacing it.

## 9. One backend, two clients

The CLI and the web interface are thin adapters over the same Python services,
so a capability added once works in both.

```text
          CLI (argparse)                          Web (FastAPI routes + static JS)
              │                                              │
              ▼                                              ▼
      config_from_args ─────▶  RunConfig  ◀────────── RunRequest.to_config
                               validate_run_config (same rules, same messages)
                                         │
       ┌─────────────────────────────────┼────────────────────────────────────┐
       │ RemoteWorkflow   environment   workspace   files   cluster   ssh      │  shared
       │        │                                                              │  services
       │        └─ emits structured Events ──┐                                 │
       └─────────────────────────────────────┼─────────────────────────────────┘
                                             ▼
                  CliReporter (terminal)            RunManager → SSE stream (browser)
```

- **Typed configuration** (`config.RunConfig`) instead of argparse namespaces.
- **Structured events** (`events.py`): step, progress, log, ready, warning,
  confirmation, cancelling, completed/failed/cancelled. The terminal output is
  byte-for-byte what it was before the refactor.
- **Confirmation and cancellation interfaces** (`interaction.py`): the CLI
  answers questions from the terminal or `--yes`; the web relays them to the
  page. Cancellation is a thread-safe token that triggers the same remote
  cleanup as Ctrl+C.
- **Redaction** (`Redactor`, `RedactingSink`): secrets such as the Jupyter
  token are removed from structured log events before they are retained by the
  local run manager or streamed to a client. This does not rewrite the raw
  command log stored on the cluster (section 8).
- **Live progress in the browser** uses Server-Sent Events. Every event has a
  position, so a reloaded page replays the run and a dropped stream resumes with
  `Last-Event-ID`.

## 10. Local web server security

`floability-remote web` runs a server on the laptop that can run commands on a
cluster, so it treats other websites in the same browser as hostile.

```text
 request ─▶ Host is 127.0.0.1:<port> or localhost:<port>?  ── no ─▶ 400 (blocks DNS rebinding)
         ─▶ same origin? (Origin / Sec-Fetch-Site)         ── no ─▶ 403 (blocks other sites/ports)
         ─▶ API call carries the session cookie or token?  ── no ─▶ 401
         ─▶ handled; response gets a strict CSP, no-referrer, no framing, no-store
```

- Binds to `127.0.0.1` only.
- A random per-process token is printed in the terminal link; the first visit
  swaps it for an `HttpOnly`, `SameSite=Strict` cookie and removes it from the
  URL. Access logging is off so the token never reaches a log.
- No inline scripts and no external assets (Content-Security-Policy `'self'`);
  the logo and all code ship inside the Python package.

## 11. Safe file downloads

Files are downloaded one at a time from a finished or stopped run, over the
existing connection.

```text
 LIST_DOWNLOAD_FILES <run_dir>             (remote, read-only, Python)
   ├─ confirm <run_dir>/.floability-remote-run marker
   ├─ find the instance from "[floability] Created instance structure at: <path>"
   ├─ walk only allowed places, never following symlinks:
   │     run command log · instance workflow/ · instance logs/ (top level)
   │     catalog_update.json · metadata/ · metrics/
   ├─ skip pyuser/, logs/vine_factory_scratch/, symlinks, special files
   └─ return ≤ 2,000 entries (group, logical path, size)

 local: each entry gets an opaque ID (hash of group + path)
        browser asks for an ID, never for a remote path

 DOWNLOAD_FILE <path> <limit>                (remote, Python)
   ├─ open with O_NOFOLLOW, fstat: must still be a regular file
   ├─ refuse if larger than 100 MiB; stop if it grows past the limit while sending
   └─ stream raw bytes on stdout ──▶ local temporary file ──▶ atomic rename
```

The file list is rebuilt before every download, so a file that changed or
became a symlink after listing is refused. The CLI uses the same service:

```bash
floability-remote download --target <host> --run-dir <run_dir> --list-only
floability-remote download --target <host> --run-dir <run_dir> --file workflow/results.csv --output results.csv
```

## 12. Remote-host readiness check

`cluster.ClusterService` (web **Check remote host**, CLI `check-cluster`)
inspects a login node **without changing anything**: operating system, Git,
`setsid`, Conda, the Floability environment and version, available batch
systems, the resolved base directory, free space on its filesystem, and quota
information where the site exposes it. The web form disables batch systems
whose standard client commands were not detected. The check reports issues
instead of fixing them.

## 13. Testing real scripts without a cluster

Most tests run the real code paths against stand-ins, so they need no cluster
and no network:

- **A fake `ssh`** that implements master/check/exit/cancel, forwards `-L`
  ports, asks for passwords through `$SSH_ASKPASS`, and runs commands locally
  (`tests/fake_ssh.py`). It exercises the real `SSHSession`, AskPass helper,
  `ConnectionManager`, and tunnels.
- **A util-linux-style `setsid`** and a stand-in Floability that reports its
  PID and SIGINT state, so the real launch and stop scripts are tested.
- **Web API integration tests** use fake SSH sessions and a scripted workflow
  to verify sign-in prompts, streamed progress, Jupyter readiness, stopping,
  downloads, and shutdown cleanup. The underlying SSH, tunnel, launch, and stop
  components are exercised separately with the local stand-ins above.

The package supports Python 3.9 and newer. A clean Python 3.9 installation from
both the wheel and source distribution has been verified manually; an
automated multi-version CI matrix is not configured yet. A real-cluster smoke
test remains part of release validation.

## 14. Limits and open questions

- **Runs depend on the laptop.** If the SSH connection drops (laptop sleep,
  network loss), a run cannot be re-attached. The planned fix is to launch
  Floability fully detached on the cluster, record its state in the run
  directory, and reconnect from there.
- **No idle limit for Jupyter sessions** beyond the server's lifetime yet.
- **Jupyter on the login node.** Floability runs JupyterLab and the TaskVine
  manager on the login node; long sessions may conflict with site policies.
- **Output parsing.** Jupyter details and the instance path are read from
  Floability's human-readable output. A machine-readable status from Floability
  would make this sturdier.
- **One connection and one active run** per web server.
