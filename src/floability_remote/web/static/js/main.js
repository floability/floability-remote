import { api, ApiError } from "./api.js";
import { ConnectionCard } from "./connection.js";
import {
  addOptionRow,
  applyDefaults,
  renderChoices,
  serialize,
  showIssues,
  syncModeFields,
} from "./form.js";
import { RunPanel } from "./run.js";

const HEALTH_INTERVAL_MS = 15000;
const VALIDATE_DELAY_MS = 300;
const CONNECTING_POLL_MS = 500;

const form = document.getElementById("run-form");
const status = document.getElementById("server-status");
const validation = document.getElementById("validation");
const generalErrors = document.getElementById("general-errors");
const commandOutput = document.getElementById("command");
const copyButton = document.getElementById("copy-command");
const startButton = document.getElementById("start");
const startNote = document.getElementById("start-note");

const touched = new Set();
let showAll = false;
let validateTimer = null;
let validationRun = 0;
let features = {};
let configValid = false;
let connection = { state: "disconnected" };
let connectionPoll = null;
let stopFollowing = null;
let starting = false;

// Status ------------------------------------------------------------------

function setStatus(state, label) {
  status.dataset.state = state;
  status.querySelector(".status-label").textContent = label;
}

function setValidation(state, text) {
  validation.dataset.state = state;
  validation.querySelector(".validation-text").textContent = text;
}

function showUnauthorized() {
  document.getElementById("auth-banner").hidden = false;
  setStatus("unauthorized", "Not signed in");
  for (const control of form.querySelectorAll("input, button")) control.disabled = true;
}

function handleError(error, fallback) {
  if (error instanceof ApiError && error.status === 401) {
    showUnauthorized();
    return true;
  }
  if (fallback) fallback(error.message);
  return false;
}

async function checkHealth() {
  try {
    const health = await api.health();
    if (status.dataset.state !== "unauthorized") setStatus("online", "Local server online");
    document.getElementById("version").textContent = `v${health.version}`;
  } catch {
    setStatus("offline", "Local server stopped");
  }
}

function renderFeatures() {
  const list = document.getElementById("features");
  list.replaceChildren(
    ...Object.entries(features).map(([name, feature]) => {
      const item = document.createElement("li");
      const text = document.createElement("span");
      text.className = "feature-text";
      const label = document.createElement("span");
      label.className = "feature-name";
      label.textContent = name;
      text.append(label, feature.description);
      const pill = document.createElement("span");
      pill.className = "pill";
      pill.dataset.available = String(feature.available);
      pill.textContent = feature.available ? "Available" : feature.milestone;
      item.append(text, pill);
      return item;
    }),
  );
}

// Start control -----------------------------------------------------------

function selectedMode() {
  return new FormData(form).get("mode");
}

function startBlocker() {
  const mode = selectedMode();
  const feature = features[mode];
  const target = form.elements.namedItem("connection.target").value.trim();
  if (!feature || !feature.available) {
    return `Interactive runs from the browser arrive in ${feature ? feature.milestone : "a later milestone"}. Use the CLI command above until then.`;
  }
  if (runPanel.active) return "A run is in progress.";
  if (connection.state !== "connected") return "Connect to the login node to start.";
  if (connection.target !== target) return `Connected to ${connection.target}; disconnect to use another login node.`;
  if (!configValid) return "Fix the highlighted fields to start.";
  return "";
}

function updateStartControl() {
  const mode = selectedMode();
  startButton.textContent = starting ? "Starting…" : mode === "run" ? "Start interactive run" : "Start execution";
  const blocker = startBlocker();
  startButton.disabled = starting || Boolean(blocker);
  startNote.textContent = blocker;
}

// Validation --------------------------------------------------------------

async function validate() {
  const run = ++validationRun;
  setValidation("checking", "Checking configuration…");
  try {
    const result = await api.validateRun(serialize(form));
    if (run !== validationRun) return;

    const { shown, unmatched } = showIssues(form, result.issues, showAll ? null : touched);
    generalErrors.replaceChildren(
      ...unmatched.map((text) => Object.assign(document.createElement("li"), { textContent: text })),
    );

    configValid = result.valid;
    if (result.valid) {
      setValidation("valid", "Configuration is valid.");
      commandOutput.textContent = result.command;
      copyButton.disabled = false;
    } else {
      if (shown > 0) {
        setValidation("invalid", `${shown} ${shown === 1 ? "field needs" : "fields need"} attention.`);
      } else {
        setValidation("idle", "Enter a login node and a repository.");
      }
      commandOutput.textContent = "—";
      copyButton.disabled = true;
    }
  } catch (error) {
    if (run !== validationRun) return;
    configValid = false;
    handleError(error, (message) => setValidation("error", message));
  }
  updateStartControl();
}

function scheduleValidation() {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(validate, VALIDATE_DELAY_MS);
}

function fieldName(target) {
  const field = target.closest("[data-field]");
  return field ? field.dataset.field : null;
}

function revealIssues(issues) {
  showAll = true;
  for (const issue of issues) touched.add(issue.field);
  showIssues(form, issues, null);
}

// Connection --------------------------------------------------------------

const connectionCard = new ConnectionCard(form, {
  onConnect: connect,
  onDisconnect: disconnect,
  onAnswer: answerPrompt,
});

function setConnection(next) {
  connection = next;
  connectionCard.render(connection);
  if (connection.state === "connecting") pollConnection();
  updateStartControl();
}

function pollConnection() {
  if (connectionPoll) return;
  connectionPoll = setTimeout(async () => {
    connectionPoll = null;
    try {
      setConnection(await api.connection());
    } catch (error) {
      if (!handleError(error)) pollConnection();
    }
  }, CONNECTING_POLL_MS);
}

async function connect() {
  const payload = serialize(form).connection;
  connectionCard.showError("");
  try {
    setConnection(await api.connect(payload));
  } catch (error) {
    if (error instanceof ApiError && error.issues.length) return revealIssues(error.issues);
    handleError(error, (message) => connectionCard.showError(message));
  }
}

async function disconnect() {
  try {
    setConnection(await api.disconnect());
  } catch (error) {
    handleError(error, (message) => connectionCard.showError(message));
  }
}

async function answerPrompt(promptId, answer) {
  try {
    await api.answerPrompt(promptId, answer);
  } catch (error) {
    handleError(error);
  }
  pollConnection();
}

// Runs --------------------------------------------------------------------

const runPanel = new RunPanel({
  onCancel: async (runId) => {
    try {
      await api.cancelRun(runId);
      runPanel.setState("cancelling");
      return true;
    } catch (error) {
      handleError(error, (message) => (startNote.textContent = `Could not stop the run: ${message}`));
      return false;
    }
  },
  onConfirm: async (runId, confirmationId, approved) => {
    try {
      await api.answerConfirmation(runId, confirmationId, approved);
    } catch (error) {
      handleError(error);
    }
  },
  onNewRun: () => {
    if (stopFollowing) stopFollowing();
    runPanel.hide();
    updateStartControl();
    form.querySelector("#repository").focus();
  },
});

function restoreRunFields(run) {
  // After a page reload, show the restored run's settings in the form.
  const values = {
    "backpack.repository": run.repository,
    "backpack.ref": run.ref,
    entrypoint: run.entrypoint,
  };
  for (const [name, value] of Object.entries(values)) {
    const input = form.elements.namedItem(name);
    if (input && !input.value) input.value = value;
  }
  for (const [name, value] of [["mode", run.mode], ["batch_type", run.batch_type]]) {
    const choice = form.querySelector(`input[name="${name}"][value="${CSS.escape(value)}"]`);
    if (choice) choice.checked = true;
  }
  syncModeFields(form);
}

function follow(run, { restored = false } = {}) {
  if (stopFollowing) stopFollowing();
  runPanel.reset(run);
  if (restored) restoreRunFields(run);
  else runPanel.panel.scrollIntoView({ behavior: "smooth", block: "start" });
  stopFollowing = api.followRun(run.id, {
    onEvent: (event, position) => {
      runPanel.apply(event, position);
      updateStartControl();
    },
    onEnd: () => {
      stopFollowing = null;
      updateStartControl();
      refreshConnection();
    },
  });
  updateStartControl();
}

async function startRun() {
  starting = true;
  updateStartControl();
  try {
    follow(await api.startRun(serialize(form)));
  } catch (error) {
    if (error instanceof ApiError && error.issues.length) {
      revealIssues(error.issues);
    } else {
      handleError(error, (message) => (startNote.textContent = message));
    }
    refreshConnection();
  } finally {
    starting = false;
    updateStartControl();
  }
}

async function refreshConnection() {
  try {
    setConnection(await api.connection());
  } catch (error) {
    handleError(error);
  }
}

// Startup -----------------------------------------------------------------

async function start() {
  checkHealth();
  setInterval(checkHealth, HEALTH_INTERVAL_MS);

  let meta;
  try {
    meta = await api.meta();
  } catch (error) {
    handleError(error, (message) => setValidation("error", message));
    return;
  }

  features = meta.features;
  const preferredMode = meta.modes.find((mode) => features[mode] && features[mode].available);
  renderChoices(form, meta, preferredMode);
  applyDefaults(form, meta.defaults);
  syncModeFields(form);
  renderFeatures();

  form.addEventListener("input", (event) => {
    if (event.target.closest("#ssh-prompt")) return;
    scheduleValidation();
    updateStartControl();
  });
  form.addEventListener("change", (event) => {
    if (event.target.closest("#ssh-prompt")) return;
    const name = fieldName(event.target);
    if (name) touched.add(name);
    if (event.target.name === "mode") syncModeFields(form);
    scheduleValidation();
  });
  form.addEventListener("focusout", (event) => {
    const name = fieldName(event.target);
    if (name && event.target.value) {
      touched.add(name);
      scheduleValidation();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    showAll = true;
    clearTimeout(validateTimer);
    validate();
  });
  startButton.addEventListener("click", startRun);
  document.getElementById("add-floability-option").addEventListener("click", () => {
    addOptionRow(form).focus();
  });

  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(commandOutput.textContent);
      copyButton.textContent = "Copied";
      setTimeout(() => (copyButton.textContent = "Copy"), 1500);
    } catch {
      copyButton.textContent = "Select and copy";
    }
  });

  await refreshConnection();
  try {
    const run = await api.currentRun();
    follow(run, { restored: true });
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 404)) handleError(error);
  }
  validate();
}

start();
