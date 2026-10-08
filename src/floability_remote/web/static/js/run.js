// Run panel: renders a run snapshot and the events streamed for it.

const STATE_TEXT = {
  running: "Running",
  ready: "Jupyter ready",
  cancelling: "Stopping…",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  stopped: "Stopped",
};

// After a failed stop request, keep the button disabled at least this long.
const RETRY_DELAY_MS = 5000;

const RESULT_LABELS = {
  run_dir: "Remote run directory",
  backpack_dir: "Backpack and outputs",
  log_path: "Remote command log",
};

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class RunPanel {
  constructor({ onCancel, onConfirm, onNewRun }) {
    this.panel = document.getElementById("run-panel");
    this.summary = document.getElementById("run-summary");
    this.badge = document.getElementById("run-state");
    this.steps = document.getElementById("run-steps");
    this.confirmation = document.getElementById("run-confirmation");
    this.confirmationMessage = document.getElementById("confirmation-message");
    this.ready = document.getElementById("run-ready");
    this.openLink = document.getElementById("run-open");
    this.url = document.getElementById("run-url");
    this.title = document.getElementById("run-title");
    this.message = document.getElementById("run-message");
    this.result = document.getElementById("run-result");
    this.log = document.getElementById("run-log");
    this.logCount = document.getElementById("log-count");
    this.cancelButton = document.getElementById("cancel-run");
    this.newRunButton = document.getElementById("new-run");

    this.cancelButton.addEventListener("click", async () => {
      if (this.stopping) return;
      this.stopping = true;
      this.refreshCancelButton();
      const clickedAt = Date.now();
      if (await onCancel(this.run.id)) return; // stays disabled until the run ends
      // The request failed: allow another attempt, but not immediately.
      const wait = Math.max(0, RETRY_DELAY_MS - (Date.now() - clickedAt));
      setTimeout(() => {
        this.stopping = false;
        this.refreshCancelButton();
      }, wait);
    });
    document.getElementById("copy-jupyter").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      try {
        await navigator.clipboard.writeText(this.openLink.href);
        button.textContent = "Copied";
      } catch {
        button.textContent = "Select the link below";
      }
      setTimeout(() => (button.textContent = "Copy link"), 1500);
    });
    this.newRunButton.addEventListener("click", onNewRun);
    document.getElementById("confirmation-approve").addEventListener("click", () => this.answer(true));
    document.getElementById("confirmation-decline").addEventListener("click", () => this.answer(false));
    this.onConfirm = onConfirm;
  }

  get active() {
    return Boolean(this.run) && !TERMINAL.has(this.state) && this.state !== "stopped";
  }

  get interactive() {
    return Boolean(this.run) && this.run.mode === "run";
  }

  refreshCancelButton() {
    const stopping = this.stopping || this.state === "cancelling";
    this.cancelButton.disabled = stopping;
    if (stopping) this.cancelButton.textContent = this.interactive ? "Stopping…" : "Cancelling…";
    else this.cancelButton.textContent = this.interactive ? "Stop session" : "Cancel run";
  }

  /** Show `run` (a RunResponse) and clear previously rendered events. */
  reset(run) {
    this.run = run;
    this.state = run.state;
    this.position = -1;
    this.currentStep = null;
    this.finalStep = false;
    this.sawReady = false;
    this.cancellingNote = null;
    this.logLines = 0;
    this.pendingConfirmation = null;
    this.steps.replaceChildren();
    this.log.textContent = "";
    this.logCount.textContent = "";
    this.result.replaceChildren();
    this.result.hidden = true;
    this.message.hidden = true;
    this.ready.hidden = true;
    this.confirmation.hidden = true;
    this.title.textContent = this.interactive ? "Interactive session" : "Execution";
    this.stopping = Boolean(run.cancel_requested);

    const ref = run.ref ? ` @ ${run.ref}` : "";
    this.summary.textContent = `${run.repository}${ref} · ${run.batch_type} on ${run.target}`;
    this.panel.hidden = false;
    this.setState(run.cancel_requested && run.state === "running" ? "cancelling" : run.state);
  }

  hide() {
    this.panel.hidden = true;
    this.run = null;
  }

  setState(state) {
    this.state = state;
    this.badge.dataset.state = state;
    this.badge.textContent = STATE_TEXT[state] || state;
    const finished = TERMINAL.has(state) || state === "stopped";
    this.cancelButton.hidden = finished;
    this.refreshCancelButton();
    this.newRunButton.hidden = !finished;
    if (finished) {
      this.confirmation.hidden = true;
      this.ready.hidden = true;
    }
  }

  apply(event, position) {
    if (position <= this.position) return; // already rendered (stream resumed)
    this.position = position;

    switch (event.kind) {
      case "step":
        this.addStep(event);
        break;
      case "progress":
      case "detail":
        if (event.data && event.data.confirmation_id) this.confirmation.hidden = true;
        // Retained paths are shown in the result box when the run ends.
        if (event.data && ("run_dir" in event.data || "log_path" in event.data)) break;
        if (this.finalStep && event.data && "backpack_dir" in event.data) break;
        this.addNote(event.message, event.kind);
        break;
      case "warning":
        this.addNote(`Warning: ${event.message}`, "warning");
        break;
      case "log":
        this.appendLog(event.message);
        break;
      case "confirmation":
        this.showConfirmation(event);
        break;
      case "ready":
        this.openLink.href = event.data.url;
        this.url.textContent = event.data.url;
        this.ready.hidden = false;
        this.sawReady = true;
        if (this.currentStep) this.currentStep.dataset.status = "done";
        if (this.state === "running") this.setState("ready");
        break;
      case "cancelling":
        this.setState("cancelling");
        // Replaced by the outcome when the run ends (see finish()).
        this.cancellingNote = this.addNote(event.message, "warning");
        break;
      case "completed":
      case "failed":
      case "cancelled":
        this.finish(event);
        break;
    }
  }

  addStep(event) {
    if (this.currentStep) this.currentStep.dataset.status = "done";
    const item = element("li", "step-item");
    item.dataset.status = "active";
    const head = element("div", "step-head");
    head.append(
      element("span", "step-marker"),
      element("span", "step-text", event.message),
      element("span", "step-count", `${event.step}/${event.total}`),
    );
    item.append(head, element("ul", "step-notes"));
    this.steps.append(item);
    this.currentStep = item;
    this.finalStep = event.step === event.total;
  }

  addNote(text, kind) {
    if (!this.currentStep) this.addStep({ message: "Preparing…", step: "–", total: "–" });
    const note = element("li", `step-note ${kind}`, text);
    this.currentStep.querySelector(".step-notes").append(note);
    return note;
  }

  appendLog(text) {
    const pinned = this.log.scrollTop + this.log.clientHeight >= this.log.scrollHeight - 8;
    this.log.append(text);
    this.logLines += 1;
    this.logCount.textContent = `(${this.logLines} ${this.logLines === 1 ? "line" : "lines"})`;
    if (pinned) this.log.scrollTop = this.log.scrollHeight;
  }

  showConfirmation(event) {
    this.pendingConfirmation = event.data.id;
    this.confirmationMessage.textContent = event.message;
    this.confirmation.hidden = false;
    for (const button of this.confirmation.querySelectorAll("button")) button.disabled = false;
  }

  answer(approved) {
    if (!this.pendingConfirmation) return;
    for (const button of this.confirmation.querySelectorAll("button")) button.disabled = true;
    this.onConfirm(this.run.id, this.pendingConfirmation, approved);
  }

  finish(event) {
    // Stopping a ready Jupyter session is its normal end, not a failure.
    const stopped = event.kind === "cancelled" && this.sawReady;
    if (this.cancellingNote) {
      // The "Stopping…/Cancellation requested…" note is no longer in progress.
      this.cancellingNote.textContent = event.message;
      this.cancellingNote.className = `step-note ${event.kind === "failed" ? "warning" : "detail"}`;
      this.cancellingNote = null;
    }
    if (this.currentStep) {
      this.currentStep.dataset.status = event.kind === "completed" || stopped ? "done" : "stopped";
    }
    this.setState(stopped ? "stopped" : event.kind);

    this.message.hidden = event.kind === "completed" || stopped;
    this.message.dataset.kind = event.kind;
    this.message.textContent = event.message;

    const rows = Object.entries(RESULT_LABELS).filter(([key]) => event.data && event.data[key]);
    this.result.replaceChildren(
      ...rows.flatMap(([key, label]) => [element("dt", "", label), element("dd", "", event.data[key])]),
    );
    this.result.hidden = rows.length === 0;
  }
}
