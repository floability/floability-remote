import { api } from "./api.js";

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class BackpackPicker {
  constructor(form, { onChange, onError }) {
    this.form = form;
    this.onChange = onChange;
    this.onError = onError;
    this.summary = form.querySelector("#backpack-summary");
    this.folderPath = form.querySelector("#local-folder-path");
    this.zipPath = form.querySelector("#local-zip-path");
    this.note = form.querySelector("#backpack-note");
    this.entrypoint = form.querySelector("#entrypoint");
    this.browser = form.querySelector("#folder-browser");
    this.browseButton = form.querySelector("#browse-folder");
    this.report = null;
    this.provenance = null;
    this.selectedSource = null;
    this.inspectTimer = null;
    this.browsing = null;
    this.vscodeAvailable = false;

    form.querySelector("#backpack-source").addEventListener("change", () => this.sync());
    form.querySelector("#choose-zip").addEventListener("click", () => form.querySelector("#zip-picker").click());
    form.querySelector("#zip-picker").addEventListener("change", (event) => this.chooseZip(event.target.files[0]));
    form.querySelector("#clone-backpack").addEventListener("click", () => this.cloneRepository());
    form.elements.namedItem("backpack.repository").addEventListener("input", () => this.updateCloneLocation());
    form.querySelector("#open-folder").addEventListener("click", () => this.open("folder"));
    form.querySelector("#open-vscode").addEventListener("click", () => this.open("vscode"));
    form.querySelector("#discard-changes").addEventListener("click", () => this.discard());
    this.browseButton.addEventListener("click", () => (this.browser.hidden ? this.openBrowser() : this.closeBrowser()));
    form.querySelector("#folder-up").addEventListener("click", () => this.browsing?.parent && this.browse(this.browsing.parent));
    form.querySelector("#use-folder").addEventListener("click", () => this.useBrowsedFolder());
    form.querySelector("#close-folder-browser").addEventListener("click", () => this.closeBrowser());
    this.folderPath.addEventListener("input", () => {
      this.showNote("");
      clearTimeout(this.inspectTimer);
      this.inspectTimer = setTimeout(() => this.inspectPath(), 350);
    });
    this.entrypoint.addEventListener("input", () => this.markEntrypoint());
    this.updateCloneLocation();
    this.sync();
  }

  sourceType() {
    return new FormData(this.form).get("backpack.source_type") || "git";
  }

  sync() {
    const selected = this.sourceType();
    const changed = selected !== this.selectedSource;
    this.selectedSource = selected;
    for (const group of this.form.querySelectorAll("[data-backpack-source]")) {
      const visible = group.dataset.backpackSource === selected;
      group.hidden = !visible;
      for (const control of group.querySelectorAll("input, button")) control.disabled = !visible;
    }
    if (changed) {
      this.render(null);
      this.renderStatus(null);
      this.closeBrowser();
      this.showNote("");
    }
    if (changed && selected === "folder" && this.folderPath.value.trim()) {
      this.inspectPath();
    }
    if (changed && selected === "zip" && this.zipPath.value.trim()) {
      this.inspectLocal(this.zipPath.value.trim());
    }
    this.onChange();
  }

  // Summary and workflow files ---------------------------------------------

  render(report, label = "") {
    this.report = report;
    this.renderWorkflowFiles(report && report.valid ? report.workflow_files || [] : []);
    if (!report) {
      this.summary.hidden = true;
      this.summary.textContent = "";
      return;
    }
    this.summary.hidden = false;
    if (report.valid) {
      this.summary.dataset.state = "valid";
      const files = `${report.file_count} ${report.file_count === 1 ? "file" : "files"}`;
      this.summary.textContent = `${label || report.name} · ${files} · ${humanSize(report.total_bytes)} · workflow/ found ✓`;
    } else {
      this.summary.dataset.state = "invalid";
      this.summary.textContent = report.problems.join(" ");
    }
  }

  renderWorkflowFiles(files) {
    const container = this.form.querySelector("#workflow-files");
    const list = this.form.querySelector("#workflow-file-list");
    list.replaceChildren(
      ...files.map((name) => {
        const chip = element("button", "workflow-file", name);
        chip.type = "button";
        chip.dataset.file = name;
        chip.addEventListener("click", () => {
          this.entrypoint.value = this.entrypoint.value === name ? "" : name;
          // Let the form revalidate and refresh the CLI preview.
          this.entrypoint.dispatchEvent(new Event("input", { bubbles: true }));
        });
        return chip;
      }),
    );
    container.hidden = files.length === 0;
    // Drop an entrypoint left over from another backpack.
    const current = this.entrypoint.value.trim();
    if (files.length && current && !files.includes(current)) {
      this.entrypoint.value = "";
      this.entrypoint.dispatchEvent(new Event("input", { bubbles: true }));
    }
    this.markEntrypoint();
  }

  markEntrypoint() {
    const value = this.entrypoint.value.trim();
    for (const chip of this.form.querySelectorAll(".workflow-file")) {
      chip.setAttribute("aria-pressed", String(chip.dataset.file === value));
    }
  }

  showNote(text) {
    this.note.hidden = !text;
    this.note.textContent = text;
  }

  // Inspection ------------------------------------------------------------

  async inspectPath() {
    const path = this.folderPath.value.trim();
    if (!path) {
      this.render(null);
      this.renderStatus(null);
      this.onChange();
      return;
    }
    await this.inspectLocal(path);
  }

  async inspectLocal(path, label = "") {
    try {
      const report = await api.inspectBackpack(path);
      this.render(report, label);
      if (this.sourceType() === "folder") {
        this.renderStatus(report.valid ? await api.backpackStatus(path) : null);
      }
    } catch (error) {
      this.onError(error);
    }
    this.onChange();
  }

  restore(sourceType, path) {
    const choice = this.form.querySelector(
      `input[name="backpack.source_type"][value="${CSS.escape(sourceType)}"]`,
    );
    if (!choice) return;
    if (sourceType === "folder") this.folderPath.value = path;
    if (sourceType === "zip") this.zipPath.value = path;
    choice.checked = true;
    this.selectedSource = null;
    this.sync();
  }

  // Folder browser --------------------------------------------------------

  async openBrowser() {
    this.browser.hidden = false;
    this.browseButton.setAttribute("aria-expanded", "true");
    await this.browse(this.folderPath.value.trim());
  }

  closeBrowser() {
    this.browser.hidden = true;
    this.browseButton.setAttribute("aria-expanded", "false");
  }

  async browse(path) {
    const note = this.form.querySelector("#folder-browser-note");
    try {
      this.browsing = await api.browseFolders(path);
    } catch (error) {
      if (!path) return this.onError(error);
      // A typed path that does not exist: start from the home folder instead.
      return this.browse("");
    }
    const listing = this.browsing;
    this.form.querySelector("#folder-browser-path").textContent = listing.path;
    this.form.querySelector("#folder-up").disabled = !listing.parent;
    const items = listing.directories.map((directory) => {
      const item = element("li", "folder-item");
      const open = element("button", "folder-open");
      open.type = "button";
      open.append(element("span", "folder-name", directory.name));
      if (directory.is_backpack) open.append(element("span", "folder-badge", "Backpack"));
      open.addEventListener("click", () => this.browse(directory.path));
      item.append(open);
      if (directory.is_backpack) {
        const choose = element("button", "button secondary small", "Use");
        choose.type = "button";
        choose.setAttribute("aria-label", `Use ${directory.name}`);
        choose.addEventListener("click", () => this.useFolder(directory.path));
        item.append(choose);
      }
      return item;
    });
    if (!items.length) items.push(element("li", "folder-empty", "No subfolders."));
    this.form.querySelector("#folder-list").replaceChildren(...items);
    const use = this.form.querySelector("#use-folder");
    use.disabled = !listing.is_backpack;
    note.textContent = listing.is_backpack
      ? "This folder is a backpack."
      : `Open a folder marked Backpack, or one that contains workflow/.${listing.truncated ? " Only the first 500 folders are shown." : ""}`;
  }

  useBrowsedFolder() {
    if (this.browsing && this.browsing.is_backpack) this.useFolder(this.browsing.path);
  }

  async useFolder(path) {
    this.folderPath.value = path;
    this.closeBrowser();
    this.showNote("");
    await this.inspectPath();
    this.folderPath.focus();
  }

  // Editable clones (Git) ------------------------------------------------

  /** Show where "Clone and edit" will put the repository when no directory is typed. */
  updateCloneLocation() {
    const repository = this.form.elements.namedItem("backpack.repository").value.trim();
    const name = repository.replace(/[?#].*$/, "").replace(/\/+$/, "").split("/").pop().replace(/\.git$/, "");
    this.form.querySelector("#clone-destination").placeholder =
      `~/floability-backpacks/${name || "repository-name"}`;
  }

  /** Switch to "Local folder" showing a fresh editable clone. */
  showCopy(result, note) {
    this.form.querySelector('input[name="backpack.source_type"][value="folder"]').checked = true;
    this.folderPath.value = result.path;
    this.sync();
    this.render(result.inspection);
    this.renderStatus(result.provenance);
    this.showNote(note);
    this.onChange();
  }

  async cloneRepository() {
    const repository = this.form.elements.namedItem("backpack.repository").value.trim();
    const ref = this.form.elements.namedItem("backpack.ref").value.trim();
    const destination = this.form.querySelector("#clone-destination").value.trim();
    const button = this.form.querySelector("#clone-backpack");
    const error = this.form.querySelector("#clone-error");
    error.textContent = "";
    if (!repository) {
      error.textContent = "Enter a repository first.";
      return;
    }
    button.disabled = true;
    button.textContent = "Cloning…";
    try {
      const result = await api.cloneBackpack(repository, ref, destination);
      this.showCopy(
        result,
        "Cloned. Edit the files in this folder, then press Start.",
      );
    } catch (caught) {
      error.textContent = caught.message;
    } finally {
      button.disabled = false;
      button.textContent = "Clone and edit";
    }
  }

  async chooseZip(file) {
    if (!file) return;
    const button = this.form.querySelector("#choose-zip");
    button.disabled = true;
    button.textContent = "Checking…";
    try {
      const report = await api.uploadBackpackZip(file);
      // Invalid archives are not kept, so only a valid one becomes the source.
      this.zipPath.value = report.valid ? report.path : "";
      this.render(report, file.name);
    } catch (error) {
      this.onError(error);
    } finally {
      button.disabled = false;
      button.textContent = "Choose ZIP…";
      this.form.querySelector("#zip-picker").value = "";
      this.onChange();
    }
  }

  /** Show "Open in VS Code" only when the server found the `code` command. */
  setVsCodeAvailable(available) {
    this.vscodeAvailable = Boolean(available);
    this.form.querySelector("#open-vscode").hidden = !this.vscodeAvailable;
  }

  // Local status ----------------------------------------------------------

  renderStatus(status) {
    this.provenance = status;
    const actions = this.form.querySelector("#local-backpack-actions");
    actions.hidden = !status;
    if (!status) return;
    const parts = [];
    if (status.original_repository) {
      parts.push(`Cloned from ${status.original_repository}${status.original_ref ? ` @ ${status.original_ref}` : ""}`);
    }
    const count = status.uncommitted_changes;
    if (status.commit) {
      parts.push(count ? `${count} ${count === 1 ? "file" : "files"} edited` : "no edits yet");
    }
    this.form.querySelector("#git-status").textContent = parts.join(" · ") || "Local folder.";
    this.form.querySelector("#discard-changes").hidden = !status.commit || !count;
  }

  async open(editor) {
    try {
      await api.openBackpack(this.folderPath.value.trim(), editor);
    } catch (error) {
      this.onError(error);
    }
  }

  async discard() {
    if (!window.confirm("Discard your edits to this backpack's tracked files?")) return;
    try {
      this.renderStatus(await api.discardBackpackChanges(this.folderPath.value.trim()));
      await this.inspectPath();
    } catch (error) {
      this.onError(error);
    }
  }
}
