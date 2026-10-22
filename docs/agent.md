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

## Approval policy

`get_status`, `get_connection`, and `check_remote` are read-only and execute
without an approval prompt. `connect_remote` and `disconnect_remote` require an
explicit terminal confirmation. The model cannot bypass that policy.

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
- disconnect when no run is active.

Inspecting a backpack, deploying, downloading results, and diagnosing failures
are added in later phases.
