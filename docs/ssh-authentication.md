# SSH Authentication and Connection Reuse

This document explains the complete SSH authentication path used by
Floability Remote's web interface. It follows one connection from the browser,
through FastAPI and OpenSSH, through password or MFA prompts, and finally into
the multiplexed connection used by runs, downloads, checks, and Jupyter
tunnels.

The CLI uses the same `SSHSession` transport, but lets OpenSSH prompt directly
in the terminal. The browser-specific AskPass relay described here is used by
`floability-remote web`.

## Source map

| Responsibility | Source |
|---|---|
| Browser connection form and prompt controls | [`web/static/js/connection.js`](../src/floability_remote/web/static/js/connection.js) |
| Browser polling and connection orchestration | [`web/static/js/main.js`](../src/floability_remote/web/static/js/main.js) |
| Browser HTTP client | [`web/static/js/api.js`](../src/floability_remote/web/static/js/api.js) |
| FastAPI connection routes | [`web/routes/connection.py`](../src/floability_remote/web/routes/connection.py) |
| Long-lived connection state | [`connection.py`](../src/floability_remote/connection.py) |
| AskPass broker, helper, and prompt classification | [`askpass.py`](../src/floability_remote/askpass.py) |
| OpenSSH control master and multiplexed commands | [`ssh.py`](../src/floability_remote/ssh.py) |
| API request and response models | [`web/schemas.py`](../src/floability_remote/web/schemas.py) |

## Control flow at a glance

```text
Browser                 FastAPI          ConnectionManager       OpenSSH
   │                       │                     │                   │
   │ POST /connection      │                     │                   │
   ├──────────────────────▶│ connect(config)     │                   │
   │                       ├────────────────────▶│ background thread │
   │◀── 202 connecting ────┤                     ├── start master ──▶│
   │                       │                     │                   │
   │                       │         AskPassBroker/helper ◀─────────┤ prompt
   │                       │                     │                   │
   │ GET /connection       │ snapshot()          │                   │
   ├──────────────────────▶├────────────────────▶│                   │
   │◀── prompt metadata ───┤                     │                   │
   │                       │                     │                   │
   │ POST answer           │ answer_prompt()     │                   │
   ├──────────────────────▶├────────────────────▶│── helper stdout ─▶│
   │◀── 204 ───────────────┤                     │                   │
   │                       │                     │                   │
   │     password, passphrase, host-key, or MFA cycle may repeat     │
   │                       │                     │                   │
   │ GET /connection       │ snapshot()          │◀── authenticated ─┤
   ├──────────────────────▶├────────────────────▶│                   │
   │◀── connected ─────────┤                     │                   │
```

Authentication happens in a background thread because the local SSH process
may wait several minutes for a person to answer a password or MFA question.
The web request that starts the connection therefore returns immediately with
`state: "connecting"`.

## 1. The browser starts a connection

The connection form supplies a target such as `alice@login.example.edu` and an
optional private-key path. `main.js` serializes only the connection section and
calls the API:

```javascript
async function connect() {
  const payload = serialize(form).connection;
  connectionCard.showError("");
  try {
    setConnection(await api.connect(payload));
  } catch (error) {
    if (error instanceof ApiError && error.issues.length) {
      return revealIssues(error.issues);
    }
    handleError(error, (message) => connectionCard.showError(message));
  }
}
```

The HTTP wrapper sends:

```javascript
connect: (connection) => request("POST", "/connection", connection),
```

Example request:

```http
POST /api/v1/connection
Content-Type: application/json

{
  "target": "alice@login.example.edu",
  "identity_file": "/Users/alice/.ssh/id_ed25519"
}
```

The private-key path identifies a local file. It is passed to the local
OpenSSH client with `-i`; the key is not uploaded to the remote host or sent to
the Floability Remote API as file content.

## 2. FastAPI validates and starts authentication

The route converts the request to a `ConnectionConfig`, validates the target
and key path, and asks the singleton `ConnectionManager` to connect:

```python
@router.post("", response_model=ConnectionResponse, status_code=202)
def connect(request: ConnectRequest, services: Services) -> ConnectionResponse:
    config = request.to_config()
    issues = connection_issues(config)
    if issues:
        raise ApiError(422, "invalid_config", "The connection settings are not valid.")

    snapshot = services.connections.connect(normalize_connection(config))
    return connection_response(snapshot)
```

Only one SSH connection is owned by one web-server process. Starting another
connection while one is connecting or connected returns HTTP `409`.

## 3. ConnectionManager creates a background authentication thread

`ConnectionManager.connect()` records the configuration, changes state, and
starts `_authenticate()` in a daemon thread:

```python
def connect(self, config: ConnectionConfig) -> ConnectionSnapshot:
    with self._lock:
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            raise ConnectionConflict("Already connected; disconnect first.")

        self._state = ConnectionState.CONNECTING
        self._config = config
        self._error = None
        self._prompt = None
        self._thread = threading.Thread(
            target=self._authenticate,
            args=(config,),
            daemon=True,
        )
        self._thread.start()

    return self.snapshot()
```

The first API response is therefore normally:

```json
{
  "state": "connecting",
  "target": "alice@login.example.edu",
  "prompt": null,
  "error": null
}
```

## 4. The authentication thread creates an AskPass broker

The broker bridges synchronous OpenSSH questions to asynchronous browser
requests:

```python
def _authenticate(self, config: ConnectionConfig) -> None:
    broker = self._broker_factory(self._set_prompt)
    with self._lock:
        self._broker = broker

    session = self._session_factory(
        config.target,
        identity_file=config.identity_file,
        ssh_options=config.ssh_options,
        askpass_env=broker.env(),
        control_persist="1h",
    )
    session.start()
```

`AskPassBroker` creates four local resources:

1. A random temporary directory. `mkdtemp()` creates it with mode `0700`.
2. A Unix-domain socket inside that directory, changed to mode `0600`.
3. A random 128-bit token used to authenticate helper requests.
4. A short executable helper script that invokes `floability_remote.askpass`.

The generated helper is equivalent to:

```sh
#!/bin/sh
exec /path/to/floability-remote/venv/bin/python \
  -m floability_remote.askpass "$@"
```

The broker starts a local thread that accepts helper requests from the Unix
socket. No TCP port is opened for this communication.

## 5. The broker configures OpenSSH's AskPass interface

The broker supplies these environment variables to the SSH control-master
process:

```python
def env(self):
    return {
        "SSH_ASKPASS": self.helper_path,
        "SSH_ASKPASS_REQUIRE": "force",
        "FLOABILITY_REMOTE_ASKPASS_SOCKET": self.socket_path,
        "FLOABILITY_REMOTE_ASKPASS_TOKEN": self._token,
        "DISPLAY": os.environ.get("DISPLAY", ":0"),
    }
```

`SSH_ASKPASS_REQUIRE=force` tells OpenSSH 8.4 or newer to invoke the helper for
questions instead of trying to read from a terminal. `DISPLAY` is also set for
older OpenSSH behavior, but no graphical AskPass program is involved.

## 6. SSHSession starts one OpenSSH control master

`SSHSession.start()` constructs a command equivalent to:

```bash
ssh \
  -i /Users/alice/.ssh/id_ed25519 \
  -o ControlPath=/tmp/fr-12345-a1b2c3d4.sock \
  -o ControlMaster=yes \
  -o ControlPersist=1h \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=4 \
  -o ConnectTimeout=20 \
  -fN \
  alice@login.example.edu
```

The actual process is started without a terminal:

```python
result = subprocess.run(
    command,
    env={**os.environ, **self.askpass_env},
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=errors,
    start_new_session=True,
    check=False,
)
```

Important options:

- `ControlMaster=yes` creates the reusable SSH connection.
- `ControlPath=...` names the local Unix socket used by later SSH commands.
- `ControlPersist=1h` lets the master remain alive between operations.
- `-N` starts no remote command; this process exists only as the connection.
- `-f` moves the authenticated master into the background.

OpenSSH can satisfy authentication immediately from an SSH agent or an
unencrypted key. In that case AskPass is never invoked. Otherwise, every
question causes OpenSSH to run the helper once.

## 7. OpenSSH invokes the helper for a question

Suppose the remote host asks:

```text
alice@login.example.edu's password:
```

OpenSSH effectively runs:

```bash
/private/tmp/fr-askpass-abc123/askpass \
  "alice@login.example.edu's password:"
```

The helper reads the private socket path and token from its inherited
environment, then sends one JSON line to the broker:

```python
request = {
    "token": os.environ.get("FLOABILITY_REMOTE_ASKPASS_TOKEN", ""),
    "prompt": " ".join(arguments),
}

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.connect(socket_path)
    _send(connection, request)
    reply = json.loads(_read_line(connection))
```

The helper remains blocked in `_read_line()` until the browser answers, the
prompt times out, or the connection is cancelled.

## 8. The broker authenticates and classifies the question

The broker first verifies the helper's token with a timing-safe comparison:

```python
request = json.loads(_read_line(connection))
if not secrets.compare_digest(str(request.get("token", "")), self._token):
    _send(connection, {"cancel": True})
    return
```

It then classifies the prompt:

```python
_CONFIRM_PATTERN = re.compile(
    r"\(yes/no|authenticity of host|continue connecting",
    re.I,
)

def classify(message: str) -> str:
    return "confirm" if _CONFIRM_PATTERN.search(message) else "secret"
```

Only recognized host-key questions become `confirm`. Everything else becomes
`secret`, including passwords, key passphrases, Duo codes, one-time passwords,
and other keyboard-interactive questions. This conservative default prevents
an authentication secret from accidentally appearing in a normal text field.

The broker gives every question a random ID and publishes it to
`ConnectionManager`:

```python
prompt = Prompt(
    id=secrets.token_hex(8),
    kind=classify(message),
    message=message,
    created_at=time.time(),
)

self._pending = prompt
self._on_prompt(prompt)
answered = self._answered.wait(PROMPT_TIMEOUT_SECONDS)
```

The prompt timeout is five minutes. Only one prompt can be pending for one
authentication attempt.

## 9. The browser polls the connection state

While state is `connecting`, the browser calls `GET /api/v1/connection` every
500 milliseconds:

```javascript
function pollConnection() {
  if (connectionPoll) return;

  connectionPoll = setTimeout(async () => {
    connectionPoll = null;
    try {
      setConnection(await api.connection());
    } catch (error) {
      if (!handleError(error)) pollConnection();
    }
  }, 500);
}
```

The route turns the in-memory prompt into public metadata:

```python
PromptModel(
    id=prompt.id,
    kind=prompt.kind,
    message=prompt.message,
)
```

It does not include an answer field. An example response is:

```json
{
  "state": "connecting",
  "target": "alice@login.example.edu",
  "prompt": {
    "id": "6d61fc44a2a177a1",
    "kind": "secret",
    "message": "alice@login.example.edu's password:"
  },
  "error": null
}
```

## 10. The page renders either buttons or a hidden input

The connection card uses `prompt.kind` to choose the controls:

```javascript
renderPrompt(prompt) {
  this.promptMessage.textContent = prompt.message;

  const confirm = prompt.kind === "confirm";
  this.promptSecret.hidden = confirm;
  this.promptConfirm.hidden = !confirm;

  (confirm ? document.getElementById("prompt-yes") : this.promptInput).focus();
}
```

The controls map to answers as follows:

```javascript
// Password, passphrase, MFA code, or keyboard-interactive answer
answer({ answer: this.promptInput.value });

// Unknown host-key confirmation
answer({ answer: "yes" });
answer({ answer: "no" });

// Abort authentication
answer({ cancel: true });
```

The secret field uses password-style display. The answer exists in the input
only until submission; the JavaScript clears it immediately afterward.

## 11. FastAPI returns the answer to the pending broker request

The browser sends:

```http
POST /api/v1/connection/prompts/6d61fc44a2a177a1
Content-Type: application/json

{"answer": "correct horse battery staple"}
```

The route converts cancellation into `None` and passes the answer to the
manager:

```python
@router.post("/prompts/{prompt_id}", status_code=204)
def answer_prompt(prompt_id: str, body: PromptAnswer, services: Services):
    answer = None if body.cancel else (body.answer or "")
    if not services.connections.answer_prompt(prompt_id, answer):
        raise ApiError(404, "prompt_not_found", "This prompt is no longer waiting.")
```

The broker accepts the answer only when the supplied ID matches the question
that is still pending:

```python
def answer(self, prompt_id: str, answer: Optional[str]) -> bool:
    with self._lock:
        if self._pending is None or self._pending.id != prompt_id:
            return False
        self._answer = answer
        self._answered.set()
        return True
```

This prevents a delayed browser response from answering a newer SSH question.

## 12. The broker wakes the helper, and the helper answers OpenSSH

The waiting broker thread resumes and replies over the private Unix socket:

```python
answered = self._answered.wait(self._timeout)
answer = self._answer if answered else None

_send(
    connection,
    {"cancel": True} if answer is None else {"answer": answer},
)
```

The helper prints the answer to standard output:

```python
if "answer" not in reply:
    return 1

sys.stdout.write(f"{reply['answer']}\n")
sys.stdout.flush()
return 0
```

That stdout pipe is the documented AskPass response channel. OpenSSH consumes
the line as if the user had answered an interactive terminal prompt.

If the broker sends `cancel`, the helper exits with status 1 and OpenSSH treats
the prompt as unanswered.

## 13. A password-plus-Duo example

One authentication may invoke the helper several times:

```text
1. SSH:  Are you sure you want to continue connecting (yes/no/[fingerprint])?
   kind: confirm
   UI:   Trust and continue / Reject
   user: Trust and continue
   sent: yes

2. SSH:  alice@login.example.edu's password:
   kind: secret
   UI:   hidden input
   user: enters password
   sent: password through helper stdout

3. SSH:  Duo two-factor login for alice
         Enter a passcode or select one of the following options:
   kind: secret
   UI:   hidden input
   user: enters 1
   sent: 1 through helper stdout

4. SSH authenticates and backgrounds the control master.
```

Each question has a different prompt ID. The browser never receives the
previous answer when it polls for the next question.

## 14. Authentication completes

After `session.start()` succeeds, `ConnectionManager` runs a small command over
the new master to identify the account and host:

```python
result = session.run_script(remote_scripts.IDENTIFY)
values = marker_values(result.stdout or "")

self._finish(
    session=session,
    user=values.get("__FLOABILITY_REMOTE_USER__"),
    host=values.get("__FLOABILITY_REMOTE_HOST__"),
)
```

The state becomes:

```json
{
  "state": "connected",
  "target": "alice@login.example.edu",
  "remote_user": "alice",
  "remote_host": "login01.example.edu",
  "prompt": null,
  "error": null
}
```

The broker is then closed. Its socket, token, and temporary helper directory
are removed because later commands no longer need to authenticate.

## 15. Later commands reuse the master without prompting

Every remote operation refers to the same control socket:

```python
def _base(self):
    base = [self.ssh, *self._options(), "-S", self.control_path]
    if self.promptless:
        base.extend(["-o", "BatchMode=yes"])
    return base
```

A remote script therefore produces a command equivalent to:

```bash
ssh \
  -i /Users/alice/.ssh/id_ed25519 \
  -S /tmp/fr-12345-a1b2c3d4.sock \
  -o BatchMode=yes \
  alice@login.example.edu \
  'bash -s -- argument1 argument2'
```

OpenSSH finds the existing master through `ControlPath` and opens another
logical channel inside the same authenticated TCP connection. The same master
is used for:

- Remote-host readiness checks.
- Miniforge and Floability environment preparation.
- Backpack cloning.
- Floability launch and monitoring.
- Jupyter port forwarding.
- File inventory and downloads.

`BatchMode=yes` is important: if the master has disappeared, a later command
fails instead of silently starting a new login and producing an authentication
prompt that no broker is available to answer.

## 16. Connection lifetime and shutdown

The master remains open until one of these events occurs:

- The user clicks **Disconnect**.
- The local web server shuts down.
- The connection is idle with no channels for its `ControlPersist` period.
- Keepalives detect a lost network path.
- The remote SSH server ends the session.

Normal disconnect closes the master explicitly with the equivalent of:

```bash
ssh -S /tmp/fr-12345-a1b2c3d4.sock \
  -O exit \
  alice@login.example.edu
```

If authentication is still waiting for an answer, disconnect calls
`broker.cancel()`. The helper exits unsuccessfully, SSH terminates its attempt,
and the manager returns to `disconnected`.

## 17. Failure and cancellation behavior

| Condition | Result |
|---|---|
| User rejects a new host key | Helper returns `no`; OpenSSH refuses the host |
| User presses Cancel | Broker sends cancellation; helper exits nonzero |
| No answer for five minutes | Broker times out and cancels the helper |
| Wrong or stale prompt ID | API returns `404 prompt_not_found` |
| Invalid helper token | Broker cancels without exposing the prompt |
| SSH authentication fails | State becomes `failed` with OpenSSH's error |
| Master disappears later | State becomes `failed`; user must connect again |
| Another connection is already open | API returns `409 conflict` |

## Security boundaries

- Passwords, passphrases, and MFA responses are kept in memory only long
  enough to answer the waiting helper.
- Answers are not written to disk, placed on command lines, included in API
  responses, or stored in logs.
- The prompt text and prompt ID are not secrets and are returned to the local
  browser so it can render the question.
- The AskPass socket is local, filesystem-protected, and authenticated with a
  random token.
- The Floability Remote web server listens only on `127.0.0.1` and separately
  enforces its browser session token, Host header, and same-origin checks.
- SSH host-key verification is still performed by OpenSSH using the user's
  normal `known_hosts` configuration.
- SSH keys remain local and are handled by OpenSSH or the user's SSH agent.

The key design choice is that Floability Remote does not implement the SSH
protocol. It orchestrates the system OpenSSH client, relays only its
authentication questions, and then reuses OpenSSH's authenticated control
master for the rest of the session.
