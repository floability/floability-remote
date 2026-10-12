// TaskVine card: live manager, worker, and task activity for the followed run.
// The server summarizes the run's TaskVine transactions log; this card polls
// it while the run is active and highlights any value that changed.
//
// Not every backpack uses TaskVine, and Floability starts its worker factory
// either way. The card therefore stays hidden until the workflow has started
// a TaskVine manager (`uses_taskvine`: the log shows MANAGER … START).

const POLL_MS = 5000;
const FLASH_MS = 1200;

const BADGE = {
  waiting: "Waiting",
  live: "Live",
  finished: "Finished",
  unavailable: "Unavailable",
};

function clock(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleTimeString() : "";
}

export class TaskVineCard {
  constructor({ load }) {
    this.load = load;
    this.card = document.getElementById("taskvine-card");
    this.badge = document.getElementById("taskvine-state");
    this.message = document.getElementById("taskvine-message");
    this.manager = document.getElementById("taskvine-manager");
    this.workers = document.getElementById("taskvine-workers");
    this.tasks = document.getElementById("taskvine-tasks");
    this.bar = document.getElementById("taskvine-bar");
    this.recent = document.getElementById("taskvine-recent");
    this.events = document.getElementById("taskvine-events");
    this.updated = document.getElementById("taskvine-updated");
    this.timer = null;
    this.runId = null;
    this.loading = false;
  }

  /** Follow `runId`; the card appears once a TaskVine manager has started. */
  watch(runId, active) {
    this.stopPolling();
    this.runId = runId;
    this.finished = !active;
    this.card.hidden = true;
    this.reset();
    this.refresh();
    if (active) this.timer = setInterval(() => this.refresh(), POLL_MS);
  }

  /** The run ended: take one last reading and stop polling. */
  finish() {
    this.finished = true;
    this.stopPolling();
    this.refresh();
  }

  hide() {
    this.stopPolling();
    this.runId = null;
    this.card.hidden = true;
  }

  stopPolling() {
    clearInterval(this.timer);
    this.timer = null;
  }

  reset() {
    this.setBadge("waiting");
    this.message.textContent = "";
    this.manager.textContent = "Not started yet";
    this.workers.textContent = "None connected";
    this.tasks.textContent = "None yet";
    this.setBar({ tasks_done: 0, tasks_failed: 0, tasks_running: 0, tasks_waiting: 0 });
    this.events.replaceChildren();
    this.recent.hidden = true;
    this.updated.textContent = "";
  }

  async refresh() {
    if (this.loading || !this.runId) return;
    const runId = this.runId;
    this.loading = true;
    try {
      const status = await this.load(runId);
      if (runId === this.runId) this.render(status);
    } catch (error) {
      // Before TaskVine has appeared the card stays hidden, errors included.
      if (runId === this.runId && !this.card.hidden) {
        this.setBadge("unavailable");
        this.message.textContent = error.message;
      }
    } finally {
      this.loading = false;
    }
  }

  render(status) {
    if (!status.uses_taskvine) return; // no TaskVine manager (yet) in this run
    this.card.hidden = false;
    this.message.textContent = "";
    this.setBadge(status.manager_state === "running" && !this.finished ? "live" : "finished");

    this.update(this.manager, this.managerText(status));
    this.update(this.workers, this.workersText(status));
    this.update(
      this.tasks,
      `${status.tasks_waiting} waiting · ${status.tasks_running} running · ${status.tasks_done} done · ${status.tasks_failed} failed`,
    );
    this.setBar(status);
    this.renderEvents(status.recent || []);
    this.updated.textContent = `Checked at ${clock(status.checked_at)}`;
  }

  managerText(status) {
    if (status.manager_state === "running") return `Running since ${clock(status.manager_started_at)}`;
    if (status.manager_state === "ended") return `Stopped at ${clock(status.manager_ended_at)}`;
    return "Not started yet";
  }

  workersText(status) {
    if (status.manager_state === "ended") {
      return status.workers_seen ? `${status.workers_seen} took part in the run` : "None took part";
    }
    const parts = [status.workers_connected ? `${status.workers_connected} connected` : "None connected"];
    const factory = status.factory;
    if (factory) {
      parts.push(`${factory.submitted} submitted`);
      if (factory.waiting_connection) parts.push(`${factory.waiting_connection} waiting to connect`);
    }
    return parts.join(" · ");
  }

  /** Change text, highlighting it briefly so small changes are noticeable. */
  update(element, text) {
    if (element.textContent === text) return;
    element.textContent = text;
    element.classList.remove("changed");
    void element.offsetWidth; // restart the highlight animation
    element.classList.add("changed");
    setTimeout(() => element.classList.remove("changed"), FLASH_MS);
  }

  setBar(status) {
    const parts = {
      done: status.tasks_done,
      failed: status.tasks_failed,
      running: status.tasks_running,
      waiting: status.tasks_waiting,
    };
    const total = Object.values(parts).reduce((sum, value) => sum + value, 0);
    this.bar.dataset.empty = String(total === 0);
    for (const [name, value] of Object.entries(parts)) {
      this.bar.querySelector(`[data-part="${name}"]`).style.width = total ? `${(value / total) * 100}%` : "0";
    }
  }

  renderEvents(events) {
    this.recent.hidden = events.length === 0;
    const items = [...events].reverse().map((event) => {
      const item = document.createElement("li");
      const time = document.createElement("time");
      time.textContent = clock(event.time);
      const text = document.createElement("span");
      text.textContent = event.text;
      item.append(time, text);
      return item;
    });
    const before = this.events.firstChild && this.events.firstChild.textContent;
    this.events.replaceChildren(...items);
    if (items.length && items[0].textContent !== before) {
      items[0].classList.add("changed");
      setTimeout(() => items[0].classList.remove("changed"), FLASH_MS);
    }
  }

  setBadge(state) {
    this.badge.dataset.state = state;
    this.badge.textContent = BADGE[state];
  }
}
