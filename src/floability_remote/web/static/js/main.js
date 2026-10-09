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
let clusterChecking = false;
let checkedClusterKey = null;
let availableBatchTypes = null;

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
  const wasConnected = connection.state === "connected";
  const previousTarget = connection.target;
  connection = next;
  connectionCard.render(connection);
  if (connection.state === "connecting") pollConnection();
  renderConnectionSidebar();
  if (
    connection.state === "connected" &&
    (!wasConnected || previousTarget !== connection.target)
  ) {
    checkedClusterKey = null;
    checkCluster();
  }
  updateStartControl();
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "Not reported";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = units[0];
  for (const candidate of units) {
    unit = candidate;
    if (value < 1024 || candidate === units.at(-1)) break;
    value /= 1024;
  }
  return unit === "B" ? `${value} ${unit}` : `${value.toFixed(1)} ${unit}`;
}

const BATCH_LABELS = {
  local: "Local",
  slurm: "Slurm",
  condor: "HTCondor",
  uge: "UGE",
};

function setBatchAvailability(batchTypes) {
  availableBatchTypes = batchTypes ? new Set(batchTypes) : null;
  const inputs = [...form.querySelectorAll('input[name="batch_type"]')];
  for (const input of inputs) {
    const available = !availableBatchTypes || availableBatchTypes.has(input.value);
    input.disabled = !available;
    const label = input.closest("label");
    label.classList.toggle("disabled", !available);
    if (available) label.removeAttribute("aria-disabled");
    else label.setAttribute("aria-disabled", "true");
    label.title = available
      ? ""
      : `${BATCH_LABELS[input.value] || input.value} was not detected on this remote host.`;
  }

  const selected = inputs.find((input) => input.checked);
  if (selected && selected.disabled) {
    const fallback = inputs.find((input) => !input.disabled);
    if (fallback) {
      fallback.checked = true;
      scheduleValidation();
    }
  }
  updateStartControl();
}

function clusterSettings() {
  return {
    target: connection.target || form.elements.namedItem("connection.target").value.trim(),
    env_name: form.elements.namedItem("environment.env_name").value.trim(),
    floability_version: form.elements.namedItem("environment.floability_version").value.trim(),
    conda_executable: form.elements.namedItem("environment.conda_executable").value.trim(),
    base_dir: form.elements.namedItem("base_dir").value.trim(),
  };
}

function clusterKey(settings = clusterSettings()) {
  return JSON.stringify(settings);
}

function renderConnectionSidebar() {
  const connected = connection.state === "connected";
  document.getElementById("getting-started-card").hidden = connected;
  document.getElementById("cluster-card").hidden = !connected;
  if (!connected) {
    setBatchAvailability(null);
    return;
  }

  const settings = clusterSettings();
  document.getElementById("cluster-account").textContent =
    `${connection.remote_user || "user"}@${connection.remote_host || connection.target}`;
  if (checkedClusterKey !== clusterKey(settings) && !clusterChecking) {
    const badge = document.getElementById("cluster-readiness");
    badge.dataset.state = "unchecked";
    badge.textContent = "Check needed";
    document.getElementById("cluster-environment").textContent = settings.env_name;
    document.getElementById("cluster-base-dir").textContent = settings.base_dir || "~/floability-base-dir";
    document.getElementById("cluster-check-message").textContent = "Settings changed. Check the remote host again.";
  }
}

function showClusterReport(report) {
  const badge = document.getElementById("cluster-readiness");
  badge.dataset.state = report.ready ? "ready" : "setup";
  badge.textContent = report.ready ? "Ready" : "Setup required";
  document.getElementById("cluster-account").textContent = `${report.remote_user}@${report.remote_host}`;
  let environment = report.env_name;
  if (report.floability_version) environment += ` · Floability ${report.floability_version}`;
  else if (report.env_prefix) environment += " · Floability not found";
  else environment += " · environment not found";
  document.getElementById("cluster-environment").textContent = environment;
  document.getElementById("cluster-base-dir").textContent = report.resolved_base_dir;
  setBatchAvailability(report.available_batch_types);
  document.getElementById("cluster-batch-systems").textContent = report.available_batch_types
    .map((batchType) => BATCH_LABELS[batchType] || batchType)
    .join(", ");
  const storage = report.free_bytes === null || report.total_bytes === null
    ? "Free space not reported"
    : `${formatBytes(report.free_bytes)} free of ${formatBytes(report.total_bytes)}`;
  const quotaLabels = {
    reported: "quota reported below",
    "not-reported": "quota not reported",
    "timed-out": "quota not reported",
    error: "quota not reported",
    unavailable: "quota not reported",
    "timeout-unavailable": "quota not reported",
  };
  const quota = quotaLabels[report.quota_status] || "quota not reported";
  document.getElementById("cluster-storage").textContent = `${storage}; ${quota}.`;
  const quotaDetails = document.getElementById("cluster-quota");
  quotaDetails.hidden = !report.quota_summary;
  quotaDetails.open = false;
  document.getElementById("cluster-quota-output").textContent = report.quota_summary;
  document.getElementById("cluster-issues").replaceChildren(
    ...report.issues.map((message) => Object.assign(document.createElement("li"), { textContent: message })),
  );
  document.getElementById("cluster-check-message").textContent = report.ready
    ? "The selected environment and required tools are ready."
    : "Resolve the items above before running. Missing Conda or Floability software can be prepared by the normal run flow.";
}

async function checkCluster() {
  if (clusterChecking || connection.state !== "connected") return;
  clusterChecking = true;
  const settings = clusterSettings();
  const button = document.getElementById("check-cluster");
  const badge = document.getElementById("cluster-readiness");
  button.disabled = true;
  button.textContent = "Checking…";
  badge.dataset.state = "checking";
  badge.textContent = "Checking";
  document.getElementById("cluster-check-message").textContent = "Inspecting the remote environment, batch systems, and storage…";
  document.getElementById("cluster-batch-systems").textContent = "Detecting…";
  try {
    const report = await api.checkCluster(settings);
    checkedClusterKey = clusterKey(settings);
    showClusterReport(report);
  } catch (error) {
    badge.dataset.state = "setup";
    badge.textContent = "Check failed";
    setBatchAvailability(null);
    document.getElementById("cluster-batch-systems").textContent = "Could not determine.";
    handleError(error, (message) => {
      document.getElementById("cluster-check-message").textContent = message;
    });
  } finally {
    clusterChecking = false;
    button.disabled = false;
    button.textContent = "Check remote host";
  }
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
  downloadUrl: api.downloadUrl,
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
      loadRunFiles(run.id);
    },
  });
  updateStartControl();
}

async function loadRunFiles(runId) {
  if (!runPanel.run || runPanel.run.id !== runId) return;
  runPanel.loadingFiles();
  try {
    const inventory = await api.runFiles(runId);
    if (runPanel.run && runPanel.run.id === runId) runPanel.showFiles(inventory);
  } catch (error) {
    if (!runPanel.run || runPanel.run.id !== runId) return;
    handleError(error, (message) => runPanel.showFilesError(message));
  }
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

  form.addEventListener("input", (event) => {
    if (event.target.closest("#ssh-prompt")) return;
    scheduleValidation();
    updateStartControl();
    if (["environment.env_name", "environment.floability_version", "environment.conda_executable", "base_dir"].includes(event.target.name)) {
      renderConnectionSidebar();
    }
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
  document.getElementById("check-cluster").addEventListener("click", checkCluster);

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
