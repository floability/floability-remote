// The only module that talks to the server. Every call goes through /api/v1.

const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  constructor(status, error) {
    super(error.message || `Request failed with status ${status}`);
    this.status = status;
    this.code = error.code || "error";
    this.issues = error.issues || [];
  }
}

async function request(method, path, body) {
  const options = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, options);
  } catch (cause) {
    throw new ApiError(0, { code: "network", message: "The local server is not reachable." });
  }

  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, (data && data.error) || { message: response.statusText });
  }
  return data;
}

const segment = encodeURIComponent;

/**
 * Follow a run's Server-Sent Events. `onEvent(event, position)` receives every
 * event after `after`; the browser reconnects automatically after network
 * errors and resumes from the last position. Returns a function that stops.
 */
function followRun(runId, { onEvent, onEnd, onError }, after = -1) {
  const source = new EventSource(`${API_PREFIX}/runs/${segment(runId)}/events?after=${after}`);
  source.addEventListener("run", (message) => {
    onEvent(JSON.parse(message.data), Number(message.lastEventId));
  });
  source.addEventListener("end", () => {
    source.close();
    if (onEnd) onEnd();
  });
  source.onerror = () => {
    if (onError) onError(source.readyState === EventSource.CLOSED);
  };
  return () => source.close();
}

export const api = {
  health: () => request("GET", "/health"),
  meta: () => request("GET", "/meta"),
  validateRun: (config) => request("POST", "/runs/validate", config),

  connection: () => request("GET", "/connection"),
  connect: (connection) => request("POST", "/connection", connection),
  disconnect: () => request("DELETE", "/connection"),
  answerPrompt: (promptId, answer) =>
    request("POST", `/connection/prompts/${segment(promptId)}`, answer),

  startRun: (config) => request("POST", "/runs", config),
  currentRun: () => request("GET", "/runs/current"),
  run: (runId) => request("GET", `/runs/${segment(runId)}`),
  cancelRun: (runId) => request("POST", `/runs/${segment(runId)}/cancel`),
  answerConfirmation: (runId, confirmationId, approved) =>
    request("POST", `/runs/${segment(runId)}/confirmations/${segment(confirmationId)}`, { approved }),
  followRun,
};
