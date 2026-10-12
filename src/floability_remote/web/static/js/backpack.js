import { api } from "./api.js";

const SKIPPED = new Set([".git", ".venv", "__pycache__", ".ipynb_checkpoints", ".DS_Store"]);

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export class BackpackPicker {
  constructor(form, { onChange, onError }) {
    this.form = form;
    this.onChange = onChange;
    this.onError = onError;
    this.summary = form.querySelector("#backpack-summary");
    this.folderPath = form.querySelector("#local-folder-path");
    this.zipPath = form.querySelector("#local-zip-path");
    this.report = null;
    this.provenance = null;
    this.selectedSource = null;
    this.inspectTimer = null;

    form.querySelector("#backpack-source").addEventListener("change", () => this.sync());
    form.querySelector("#choose-folder").addEventListener("click", () => form.querySelector("#folder-picker").click());
    form.querySelector("#choose-zip").addEventListener("click", () => form.querySelector("#zip-picker").click());
    form.querySelector("#folder-picker").addEventListener("change", (event) => this.chooseFolder(event.target.files));
    form.querySelector("#zip-picker").addEventListener("change", (event) => this.chooseZip(event.target.files[0]));
    form.querySelector("#clone-backpack").addEventListener("click", () => this.cloneRepository());
    form.querySelector("#open-folder").addEventListener("click", () => this.open("folder"));
    form.querySelector("#open-vscode").addEventListener("click", () => this.open("vscode"));
    form.querySelector("#discard-changes").addEventListener("click", () => this.discard());
    this.folderPath.addEventListener("input", () => {
      clearTimeout(this.inspectTimer);
      this.inspectTimer = setTimeout(() => this.inspectPath(), 350);
    });
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
    }
    if (changed && selected === "folder" && this.folderPath.value.trim()) {
      this.inspectPath();
    }
    if (changed && selected === "zip" && this.zipPath.value.trim()) {
      this.inspectLocal(this.zipPath.value.trim());
    }
    this.onChange();
  }

  render(report, label = "") {
    this.report = report;
    if (!report) {
      this.summary.hidden = true;
      this.summary.textContent = "";
      return;
    }
    this.summary.hidden = false;
    if (report.valid) {
      this.summary.dataset.state = "valid";
      this.summary.textContent = `${label || report.name} · ${report.file_count} files · ${humanSize(report.total_bytes)} · workflow/ found ✓`;
    } else {
      this.summary.dataset.state = "invalid";
      this.summary.textContent = report.problems.join(" ");
    }
  }

  async inspectPath() {
    const path = this.folderPath.value.trim();
    if (!path) return this.render(null);
    try {
      const report = await api.inspectBackpack(path);
      this.render(report);
      this.renderStatus(report.valid ? await api.backpackStatus(path) : null);
    } catch (error) {
      this.onError(error);
    }
    this.onChange();
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

  async chooseFolder(fileList) {
    const all = [...fileList];
    if (!all.length) return;
    const firstParts = all[0].webkitRelativePath.split("/");
    const wrapper = firstParts.length > 1 ? firstParts[0] : "backpack";
    const files = all.filter((file) => {
      const parts = file.webkitRelativePath.split("/").slice(1);
      return !parts.some((part) => SKIPPED.has(part));
    });
    try {
      const staging = await api.createBackpackStaging();
      for (const file of files) {
        const relative = file.webkitRelativePath.split("/").slice(1).join("/");
        await api.uploadBackpackFile(staging.upload_id, relative, file);
      }
      const report = await api.completeBackpackStaging(staging.upload_id);
      this.folderPath.value = report.path;
      this.render(report, wrapper);
      this.renderStatus(null);
      this.onChange();
    } catch (error) {
      this.onError(error);
    } finally {
      this.form.querySelector("#folder-picker").value = "";
    }
  }

  async chooseZip(file) {
    if (!file) return;
    try {
      const report = await api.uploadBackpackZip(file);
      this.zipPath.value = report.path;
      this.render(report, file.name.replace(/\.zip$/i, ""));
      this.onChange();
    } catch (error) {
      this.onError(error);
    } finally {
      this.form.querySelector("#zip-picker").value = "";
    }
  }

  renderStatus(status) {
    this.provenance = status;
    const actions = this.form.querySelector("#local-backpack-actions");
    actions.hidden = !status;
    if (!status) return;
    const count = status.uncommitted_changes;
    this.form.querySelector("#git-status").textContent = status.commit
      ? count
        ? `Edited: ${count} ${count === 1 ? "file" : "files"} changed since clone.`
        : `Git commit ${status.commit.slice(0, 12)} · no local edits.`
      : "Local folder.";
    this.form.querySelector("#discard-changes").hidden = !status.commit || !count;
  }

  async cloneRepository() {
    const repository = this.form.elements.namedItem("backpack.repository").value.trim();
    const ref = this.form.elements.namedItem("backpack.ref").value.trim();
    const destination = this.form.querySelector("#clone-destination").value.trim();
    const button = this.form.querySelector("#clone-backpack");
    const error = this.form.querySelector("#clone-error");
    error.textContent = "";
    button.disabled = true;
    button.textContent = "Copying…";
    try {
      const result = await api.cloneBackpack(repository, ref, destination);
      const folderChoice = this.form.querySelector('input[name="backpack.source_type"][value="folder"]');
      this.folderPath.value = result.path;
      folderChoice.checked = true;
      this.sync();
      this.render(result.inspection);
      this.renderStatus(result.provenance);
      this.onChange();
    } catch (caught) {
      error.textContent = caught.message;
    } finally {
      button.disabled = false;
      button.textContent = "Copy repository";
    }
  }

  async open(editor) {
    try {
      await api.openBackpack(this.folderPath.value.trim(), editor);
    } catch (error) {
      this.onError(error);
    }
  }

  async discard() {
    if (!window.confirm("Discard changes to tracked files in this backpack?")) return;
    try {
      this.renderStatus(await api.discardBackpackChanges(this.folderPath.value.trim()));
      await this.inspectPath();
    } catch (error) {
      this.onError(error);
    }
  }
}
