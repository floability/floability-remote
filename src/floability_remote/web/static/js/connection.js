// SSH connection card: status, Connect/Disconnect, and AskPass prompts.

const STATUS_TEXT = {
  disconnected: () => "Not connected",
  connecting: (connection) => `Connecting to ${connection.target}…`,
  connected: (connection) =>
    connection.remote_user && connection.remote_host
      ? `Connected as ${connection.remote_user}@${connection.remote_host}`
      : `Connected to ${connection.target}`,
  failed: () => "Connection failed",
};

export class ConnectionCard {
  constructor(form, { onConnect, onDisconnect, onAnswer }) {
    this.form = form;
    this.status = document.getElementById("connection-status");
    this.connectButton = document.getElementById("connect");
    this.disconnectButton = document.getElementById("disconnect");
    this.error = document.getElementById("connection-error");
    this.prompt = document.getElementById("ssh-prompt");
    this.promptMessage = document.getElementById("prompt-message");
    this.promptSecret = document.getElementById("prompt-secret");
    this.promptConfirm = document.getElementById("prompt-confirm");
    this.promptInput = document.getElementById("prompt-input");
    this.inputs = [form.elements.namedItem("connection.target"), form.elements.namedItem("connection.identity_file")];
    this.shownPromptId = null;

    this.connectButton.addEventListener("click", onConnect);
    this.disconnectButton.addEventListener("click", onDisconnect);

    const answer = (body) => {
      const id = this.shownPromptId;
      if (!id) return;
      this.promptInput.value = "";
      this.setPromptBusy(true);
      onAnswer(id, body);
    };
    document.getElementById("prompt-submit").addEventListener("click", () =>
      answer({ answer: this.promptInput.value }),
    );
    this.promptInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      answer({ answer: this.promptInput.value });
    });
    document.getElementById("prompt-yes").addEventListener("click", () => answer({ answer: "yes" }));
    document.getElementById("prompt-no").addEventListener("click", () => answer({ answer: "no" }));
    document.getElementById("prompt-cancel").addEventListener("click", () => answer({ cancel: true }));
  }

  render(connection) {
    const state = connection.state;
    this.status.dataset.state = state;
    this.status.querySelector(".connection-text").textContent = STATUS_TEXT[state](connection);

    const busy = state === "connecting" || state === "connected";
    for (const input of this.inputs) input.readOnly = busy;
    if (busy && connection.target) {
      this.inputs[0].value = connection.target;
      this.inputs[1].value = connection.identity_file || "";
    }

    this.connectButton.hidden = state === "connected" || state === "connecting";
    this.connectButton.textContent = state === "failed" ? "Try again" : "Connect";
    this.disconnectButton.hidden = state === "disconnected" || state === "failed";
    this.disconnectButton.textContent = state === "connecting" ? "Cancel" : "Disconnect";

    this.error.hidden = !connection.error;
    this.error.textContent = connection.error || "";
    this.renderPrompt(connection.prompt);
  }

  renderPrompt(prompt) {
    if (!prompt) {
      this.prompt.hidden = true;
      this.shownPromptId = null;
      return;
    }
    if (prompt.id === this.shownPromptId) return;
    this.shownPromptId = prompt.id;
    this.prompt.hidden = false;
    this.promptMessage.textContent = prompt.message;
    const confirm = prompt.kind === "confirm";
    this.promptSecret.hidden = confirm;
    this.promptConfirm.hidden = !confirm;
    this.setPromptBusy(false);
    (confirm ? document.getElementById("prompt-yes") : this.promptInput).focus();
  }

  setPromptBusy(busy) {
    for (const control of this.prompt.querySelectorAll("button, input")) control.disabled = busy;
  }

  showError(message) {
    this.error.hidden = !message;
    this.error.textContent = message || "";
  }
}
