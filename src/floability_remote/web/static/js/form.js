// Run-form rendering and serialization. Field names are dotted API paths
// (for example "connection.target"), so payloads and validation issues map
// directly onto inputs without per-field code.

const MODE_TEXT = {
  run: { title: "Interactive", text: "Start JupyterLab and open it here." },
  execute: { title: "Execute", text: "Run the workflow to completion." },
};

function element(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

export function renderChoices(form, meta, preferredMode) {
  const selected = preferredMode || meta.modes[0];
  const modes = form.querySelector("#mode-choices");
  modes.replaceChildren(
    ...meta.modes.map((mode) => {
      const text = MODE_TEXT[mode] || { title: mode, text: "" };
      const input = element("input", { type: "radio", name: "mode", value: mode });
      input.checked = mode === selected;
      return element("label", { class: "choice" }, [
        input,
        element("span", { class: "choice-body" }, [
          element("span", { class: "choice-title", text: text.title }),
          element("span", { class: "choice-text", text: text.text }),
        ]),
      ]);
    }),
  );

  const batches = form.querySelector("#batch-choices");
  batches.replaceChildren(
    ...meta.batch_types.map((batchType, index) => {
      const input = element("input", { type: "radio", name: "batch_type", value: batchType });
      input.checked = index === 0;
      return element("label", { class: "segment" }, [input, element("span", { text: batchType })]);
    }),
  );
}

export function applyDefaults(form, defaults) {
  const values = {
    "environment.env_name": defaults.env_name,
    remote_root: defaults.remote_root,
    jupyter_port: defaults.jupyter_port,
  };
  for (const [name, value] of Object.entries(values)) {
    const input = form.elements.namedItem(name);
    if (input && !input.value) input.value = value;
  }
}

function assign(target, path, value) {
  const parts = path.split(".");
  let node = target;
  for (const part of parts.slice(0, -1)) node = node[part] ??= {};
  node[parts.at(-1)] = value;
}

export function serialize(form) {
  const payload = {};
  for (const input of form.elements) {
    if (!input.name || input.disabled) continue;
    if (input.type === "radio") {
      if (input.checked) assign(payload, input.name, input.value);
    } else if (input.type === "checkbox") {
      assign(payload, input.name, input.checked);
    } else if (input.type === "number") {
      assign(payload, input.name, input.value === "" ? null : Number(input.value));
    } else if (input.tagName === "INPUT") {
      assign(payload, input.name, input.value.trim());
    }
  }
  if (payload.connection && !payload.connection.identity_file) {
    payload.connection.identity_file = null;
  }
  return payload;
}

export function selectedMode(form) {
  const checked = form.querySelector('input[name="mode"]:checked');
  return checked ? checked.value : null;
}

export function syncModeFields(form) {
  const mode = selectedMode(form);
  for (const group of form.querySelectorAll("[data-mode-only]")) {
    const visible = group.dataset.modeOnly === mode;
    group.hidden = !visible;
    for (const control of group.querySelectorAll("input, select, textarea, button")) {
      control.disabled = !visible;
    }
  }
}

function issueText(field, issue) {
  // Issues with a CLI flag describe the field relative to its name.
  if (!issue.flag) return issue.message;
  const label = field ? field.dataset.label : issue.field;
  return `${label} ${issue.message}`;
}

/**
 * Show issues next to their fields. Only fields in `visibleFields` are marked,
 * so untouched inputs are not flagged while the user is still typing.
 * Returns the number of issues shown and the text of issues with no field.
 */
export function showIssues(form, issues, visibleFields) {
  const unmatched = [];
  let shown = 0;
  for (const field of form.querySelectorAll("[data-field]")) {
    field.classList.remove("invalid");
    field.querySelector(".field-error").textContent = "";
  }
  for (const issue of issues) {
    const field = form.querySelector(`[data-field="${CSS.escape(issue.field)}"]`);
    if (!field) {
      unmatched.push(issueText(null, issue));
      shown += 1;
      continue;
    }
    if (visibleFields && !visibleFields.has(issue.field)) continue;
    field.classList.add("invalid");
    field.closest(".advanced")?.setAttribute("open", "");
    field.closest(".advanced-group")?.setAttribute("open", "");
    const error = field.querySelector(".field-error");
    error.textContent = error.textContent || issueText(field, issue);
    shown += 1;
  }
  return { shown, unmatched };
}
