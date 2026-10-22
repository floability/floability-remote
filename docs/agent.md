# Floability Remote Agent

`floability-remote agent` is a terminal chat built with PydanticAI. It starts
the same `floability-remote mcp` stdio server used by Claude Code and Codex;
it does not have a private deployment API.

```text
terminal chat → PydanticAI → MCPToolset → floability-remote mcp
                                           │
                                           ▼
                                  authenticated local daemon
```

The agent discovers or starts a headless daemon. Exiting chat does not stop that
daemon, close its SSH connection, or cancel a run. Use the explicit lifecycle
commands when needed:

```bash
floability-remote daemon status
floability-remote daemon stop
```

## Install

Python 3.10 or newer is required for the agent dependencies:

```bash
python -m pip install 'floability-remote[agent]'
```

With the standalone installer:

```bash
curl -fsSL https://raw.githubusercontent.com/floability/floability-remote/main/install.sh \
  | env FLOABILITY_REMOTE_EXTRAS=agent sh
```

If the default `python3` is older than 3.10, select a newer interpreter:

```bash
curl -fsSL https://raw.githubusercontent.com/floability/floability-remote/main/install.sh \
  | env FLOABILITY_REMOTE_EXTRAS=agent \
        FLOABILITY_REMOTE_PYTHON=python3.12 sh
```

For a development checkout:

```bash
python -m pip install --editable '.[agent,test]'
```

## Ollama

Ollama is the default development provider and does not require an API key:

```bash
ollama pull qwen3
floability-remote agent
```

Bare model names and Ollama tags are accepted:

```bash
floability-remote agent --model llama3.1:8b
floability-remote agent --model ollama:qwen3-coder:30b
```

The default endpoint is `http://localhost:11434/v1`. Override it with
`OLLAMA_BASE_URL` or `--ollama-base-url`.

## OpenAI and Anthropic

```bash
export OPENAI_API_KEY='your-api-key'
floability-remote agent --model openai:gpt-5
```

```bash
export ANTHROPIC_API_KEY='your-api-key'
floability-remote agent --model anthropic:your-model-name
```

Provider credentials are read by their SDKs. They are not sent to the MCP
server, daemon, remote host, conversation text, or operation audit.

## One-shot requests

Use `--prompt` for scripting or a quick check:

```bash
floability-remote agent \
  --model llama3.1:8b \
  --prompt "Is Floability Remote ready?"
```

## Connect and check a remote host

Ask naturally, for example:

```text
Connect to mislam5@crcfe01.crc.nd.edu and check whether it is ready for Floability.
```

The model requests `connect_remote`, but the terminal—not the model—asks you to
approve the connection. Password, MFA, key-passphrase, and host-key prompts use
the terminal's private input and go directly to the daemon. Do not type those
answers into chat. After login, the same OpenSSH control connection is reused
by `check_remote` and later tools.

Ask the agent to disconnect when finished, or run:

```bash
floability-remote disconnect
```

If Claude Code, Codex, or another external MCP host cannot complete an
interactive login, establish the reusable connection separately:

```bash
floability-remote connect <USER@LOGIN_NODE>
```

## Inspect, plan, and run a deployment

After connecting, ask the agent to inspect and plan a backpack:

```text
Inspect https://github.com/floability-hub/matrix-multiplication-script.git.
Show me the likely entrypoint, prepare an execute-mode plan using Slurm, start
it, and monitor it until it finishes.
```

The agent may list the backpack and read small relevant files such as its
README, software specification, or entrypoint. Git sources are pinned to the
inspected commit. Local folders and ZIP files require an extra terminal
approval and are fingerprinted, so edits invalidate an old plan.

Successful validation returns a `plan_id` and reproducible CLI command but
does not execute anything by itself. `start_deployment` accepts only that
validated plan ID and returns immediately with a `run_id`. The agent follows
the run with bounded waits and cursor-based redacted events. On completion it
reports the retained remote workspace and command log.

For an interactive backpack, ask for `mode=run`. The agent waits for Jupyter,
reports model-safe tunnel readiness, and can inspect TaskVine manager, worker,
task, and factory progress. When Jupyter becomes ready, the trusted terminal
control plane prints the working link separately:

```text
[session] Open Jupyter: http://127.0.0.1:<port>/lab/?token=<token>
```

The token is never given to the model or returned by an MCP tool. External MCP
hosts can open the same ready session outside their model-facing channel:

```bash
floability-remote session open <RUN_ID>
```

If remote environment preparation requires confirmation, the agent explains
the pending question and the terminal asks for approval before the answer is
sent. Raw command-output events are omitted by default and requested only when
they are useful for diagnosing a failure.

## Approval policy

Status, inspection, planning, progress, and event tools are read-only and
execute without an approval prompt. Connecting, disconnecting, starting,
answering a confirmation, and cancelling require explicit terminal approval.
The model cannot bypass that policy.

Use `--yes` only when you deliberately want to preapprove those future calls:

```bash
floability-remote agent --yes
```

`--yes` bypasses the action approval only. It does not bypass authentication;
passwords, MFA codes, SSH key passphrases, daemon bearer tokens, and Jupyter
tokens must never be entered into chat.

## Current scope

The current agent can:

- whether the local daemon is ready;
- whether the daemon owns an SSH connection;
- non-secret connection metadata;
- whether a run is active or which run most recently completed;
- connect once and reuse the daemon-owned SSH session;
- securely relay OpenSSH prompts outside model history;
- check remote identity, platform, Floability, storage, and batch systems; and
- disconnect when no run is active;
- inspect Git repositories, local folders, and ZIP backpacks;
- safely list and preview backpack files; and
- validate settings into a connection- and source-bound deployment plan;
- start either an execute-mode or interactive run from that plan;
- follow bounded, redacted progress and answer approved installation prompts;
- report model-safe Jupyter tunnel readiness and live TaskVine status;
- deliver the working Jupyter link through trusted terminal output;
- cancel through Floability's cleanup path; and
- report the final state, retained workspace, and command log.

Result downloads through the agent and automated failure diagnosis are added
in later phases.
