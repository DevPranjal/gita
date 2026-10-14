(() => {
  const param = new URLSearchParams(window.location.search).get("scoutTheme");
  document.documentElement.setAttribute("data-theme", param === "dark" ? "dark" : "light");
})();

const state = { boot: null, diff: null, catalog: [], view: "changes", detail: "patch", selected: null, entity: null, patch: null, selectionRequest: 0, comparisonRequest: 0, copyText: "", comparing: false };
const byId = (id) => document.getElementById(id);
const labels = { added: "Added", removed: "Removed", body_changed: "Modified", signature_changed: "Signature changed", renamed: "Renamed", moved: "Moved", observed: "Observed", gap: "Evidence gap" };

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function icon(name) {
  const element = node("i");
  element.dataset.lucide = name;
  return element;
}

function icons() {
  if (window.lucide) window.lucide.createIcons();
}

function errorMessage(error) {
  return typeof error === "string" ? error : error?.message || "The query could not be completed.";
}

async function api(route, params = {}) {
  const response = await fetch(`/api/${route}?${new URLSearchParams(params)}`);
  const result = await response.json();
  if (!response.ok || result.error) throw new Error(errorMessage(result.error));
  return result;
}

function report(error) {
  byId("error").textContent = error.message;
  byId("error").hidden = false;
}

function revisionLabel(sha) {
  const tag = state.boot.tags.find((item) => item.sha === sha);
  return tag ? tag.name : sha.slice(0, 7);
}

function splitEntity(entity) {
  const [path, ...names] = entity.id.split("::");
  return { path, name: names.join("::") || path.split("/").at(-1) };
}

function empty(text) {
  return node("div", "empty-state", text);
}

function codeBlock(text, patch = false, startLine = 1) {
  const block = node("pre", "code-block");
  let lineNumber = startLine;
  for (const line of text.trimEnd().split("\n")) {
    let kind = "";
    let number = "";
    if (patch) {
      if (line.startsWith("---") || line.startsWith("+++")) kind = "file";
      else if (line.startsWith("@@")) {
        kind = "hunk";
        const match = line.match(/\+(\d+)/);
        if (match) lineNumber = Number(match[1]);
      } else if (line.startsWith("-")) kind = "remove";
      else {
        if (line.startsWith("+")) kind = "add";
        number = String(lineNumber++);
      }
    } else number = String(lineNumber++);
    const row = node("span", `code-line ${kind}`);
    row.append(node("span", "line-number", number), node("span", "line-content", line));
    block.append(row);
  }
  return block;
}

function renderList() {
  const target = byId("entity-list");
  target.replaceChildren();
  const search = byId("search").value.trim().toLowerCase();
  if (state.comparing) {
    target.append(empty("Reading comparison..."));
    byId("visible-count").textContent = "";
    return;
  }
  let items = state.view === "entities" ? state.catalog : state.diff?.changes || [];
  items = items.filter((item) => item.id.toLowerCase().includes(search));
  if (state.view === "changes" && byId("interface-only").checked) items = items.filter((item) => item.interface);
  byId("visible-count").textContent = items.length;
  const groups = new Map();
  for (const item of items) {
    const path = splitEntity(item).path;
    if (!groups.has(path)) groups.set(path, []);
    groups.get(path).push(item);
  }
  for (const [path, entities] of [...groups].sort(([left], [right]) => left.localeCompare(right))) {
    const heading = node("div", "file-label");
    heading.append(icon("file-code-2"), node("span", "", path));
    target.append(heading);
    for (const entity of entities) {
      const button = node("button", `entity-row ${state.selected?.id === entity.id ? "selected" : ""}`);
      button.setAttribute("aria-pressed", String(state.selected?.id === entity.id));
      button.title = `${entity.id}${entity.kind ? ` / ${labels[entity.kind] || entity.kind}` : ""}`;
      button.append(node("span", "name", splitEntity(entity).name));
      if (entity.kind) {
        const dot = node("span", `change-dot ${entity.kind}`);
        dot.setAttribute("aria-label", labels[entity.kind] || entity.kind);
        button.append(dot);
      }
      button.addEventListener("click", () => selectEntity(entity));
      target.append(button);
    }
  }
  if (!items.length) target.append(empty(search ? "No matching entities." : "No entities in this view."));
  icons();
}

function updateView() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  byId("interface-only").disabled = state.view !== "changes";
  renderList();
}

function renderHistory(target) {
  const history = state.entity?.history;
  if (!history || history.error) {
    target.append(empty(errorMessage(history?.error || state.entity?.error || "No indexed history is available.")));
    return;
  }
  const identity = node("div", "history-identity");
  const eid = state.entity.identity.eid;
  const identityCode = node("code", "", `${eid.slice(0, 17)}...`);
  identityCode.title = eid;
  identity.append(node("span", "", "Persistent identity"), identityCode);
  target.append(identity);
  const list = node("div", "history-list");
  for (const event of history.results) {
    const commit = state.boot.commits.find((item) => item.sha === event.commit.hex);
    const entry = node("article", "history-event");
    const meta = node("div", "event-meta");
    meta.append(node("code", "", event.commit.hex.slice(0, 7)), node("span", "", commit?.date || ""), node("span", "", commit?.author || ""));
    entry.append(meta, node("div", "event-title", commit?.subject || "Committed transition"));
    entry.append(node("div", "event-flags", event.flags.map((flag) => labels[flag] || flag.replaceAll("_", " ")).join(" / ")));
    const source = event.source;
    const destination = event.destination;
    if (source && destination && (source.path !== destination.path || source.names.join() !== destination.names.join())) {
      const before = `${source.path}::${source.names.join("::")}`;
      const after = `${destination.path}::${destination.names.join("::")}`;
      entry.append(node("div", "event-location", `${before} -> ${after}`));
    }
    if (event.patch) {
      const details = node("details");
      details.append(node("summary", "", "View change"), codeBlock(event.patch, true));
      entry.append(details);
    } else if (event.patch_status) {
      entry.append(node("div", "event-location", event.patch_status === "unchanged" ? "Source bytes unchanged" : `Patch: ${event.patch_status.replaceAll("_", " ")}`));
    }
    list.append(entry);
  }
  if (!history.results.length) list.append(empty("No history events at this revision."));
  if (history.page?.truncated) list.append(node("p", "event-location", "History is incomplete at the response budget. Full history is available through the CLI."));
  target.append(list);
  state.copyText = history.results.map((event) => `${event.commit.hex} ${event.flags.join(", ")}\n${event.patch || ""}`).join("\n\n");
}

function renderDetail() {
  const target = byId("detail-content");
  target.replaceChildren();
  state.copyText = "";
  document.querySelectorAll("[data-detail]").forEach((button) => {
    const active = button.dataset.detail === state.detail;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (!state.selected) {
    target.append(empty("No entity selected."));
    return;
  }
  if (!state.entity && !state.patch) {
    target.append(empty("Reading entity..."));
    return;
  }
  if (state.detail === "history") renderHistory(target);
  else if (state.detail === "source") {
    const source = state.entity?.source;
    const content = source?.results?.[0];
    if (!content || source.error) target.append(empty(errorMessage(source?.error || state.entity?.error || "No indexed source is available.")));
    else {
      const meta = node("div", "code-meta");
      meta.append(node("span", "", content.occurrence.path), node("span", "", `${content.byte_length} bytes / ${content.occurrence.commit.hex.slice(0, 7)}`));
      target.append(meta, codeBlock(content.content, false, content.occurrence.span.start_line));
      state.copyText = content.content;
    }
  } else {
    if (state.patch?.patch) {
      const meta = node("div", "code-meta");
      meta.append(node("span", "", `${revisionLabel(byId("base").value)} -> ${revisionLabel(byId("head").value)}`), node("span", "", `${state.patch.tokens.toLocaleString()} tokens`));
      target.append(meta, codeBlock(state.patch.patch, true));
      state.copyText = state.patch.patch;
    } else target.append(empty(state.patch?.error ? errorMessage(state.patch.error) : "No code changes between these revisions."));
  }
  byId("copy").disabled = !state.copyText;
  const footer = byId("detail-footer");
  footer.replaceChildren(node("span", "", state.detail === "history" ? "First-parent committed history" : "Committed Git objects"));
  if (state.entity?.identity) footer.append(node("span", "", `Identity: ${state.entity.identity.lifecycle}`));
}

async function selectEntity(entity) {
  const request = ++state.selectionRequest;
  state.selected = entity;
  state.entity = null;
  state.patch = null;
  const parts = splitEntity(entity);
  byId("detail-path").textContent = parts.path;
  byId("detail-name").textContent = parts.name;
  byId("detail-kind").textContent = labels[entity.kind] || "";
  byId("history-count").hidden = true;
  document.querySelector(".detail-pane").setAttribute("aria-busy", "true");
  renderList();
  renderDetail();
  const base = byId("base").value;
  const head = byId("head").value;
  const at = entity.kind === "removed" ? base : head;
  const locator = entity.id.includes("::") ? entity.id : `${entity.id}::<module>`;
  const changed = state.diff?.changes.some((item) => item.id === entity.id);
  const capture = (promise) => promise.catch((error) => ({ error: error.message }));
  const [detail, patch] = await Promise.all([
    capture(api("entity", { entity: locator, at })),
    changed ? capture(api("patch", { entity: entity.id, base, head })) : { patch: "", tokens: 0 },
  ]);
  if (request !== state.selectionRequest) return;
  state.entity = detail;
  state.patch = patch;
  byId("history-count").textContent = detail.history?.page?.total ?? detail.history?.results?.length ?? "";
  byId("history-count").hidden = !detail.history?.results;
  document.querySelector(".detail-pane").setAttribute("aria-busy", "false");
  renderDetail();
}

async function compare() {
  if (!state.boot) return;
  const request = ++state.comparisonRequest;
  ++state.selectionRequest;
  state.comparing = true;
  state.selected = null;
  state.entity = null;
  state.patch = null;
  byId("error").hidden = true;
  byId("summary").textContent = "Reading comparison...";
  byId("noise").textContent = "";
  byId("answer-button").disabled = true;
  renderList();
  renderDetail();
  try {
    const [diff, catalog] = await Promise.all([
      state.boot.legacy_enabled
        ? api("diff", { base: byId("base").value, head: byId("head").value })
        : { changes: [], files_changed: 0, noise_filtered: 0 },
      api("catalog", { at: byId("head").value }),
    ]);
    if (request !== state.comparisonRequest) return;
    state.comparing = false;
    state.diff = diff;
    state.catalog = catalog.entities;
    byId("changes-count").textContent = diff.changes.length;
    byId("entities-count").textContent = catalog.entities.length;
    const interfaceCount = diff.changes.filter((item) => item.interface).length;
    byId("summary").textContent = state.boot.legacy_enabled
      ? `${diff.changes.length} entity changes / ${diff.files_changed} files / ${interfaceCount} interface changes`
      : `${catalog.entities.length} indexed entities at ${revisionLabel(byId("head").value)}`;
    byId("noise").textContent = state.boot.legacy_enabled ? `${diff.noise_filtered} noise-only changes filtered` : "";
    byId("answer-button").disabled = !state.boot.legacy_enabled;
    updateView();
    let candidates = state.view === "entities" ? state.catalog : diff.changes;
    if (state.view === "changes" && byId("interface-only").checked) candidates = candidates.filter((item) => item.interface);
    const selected = candidates.find((item) => item.id.endsWith("::calculate_order_total")) || candidates[0];
    if (selected) await selectEntity(selected);
    else {
      byId("detail-path").textContent = "";
      byId("detail-name").textContent = "No changes";
      byId("detail-kind").textContent = "";
      byId("history-count").hidden = true;
      byId("detail-footer").replaceChildren();
      renderDetail();
    }
  } catch (error) {
    if (request !== state.comparisonRequest) return;
    state.comparing = false;
    state.diff = null;
    byId("summary").textContent = "Comparison unavailable";
    report(error);
    renderList();
    renderDetail();
  }
}

async function boot() {
  icons();
  byId("reload").disabled = true;
  try {
    state.boot = await api("bootstrap");
    const legacy = state.boot.legacy_enabled;
    document.querySelector('[data-view="changes"]').hidden = !legacy;
    document.querySelector('[data-detail="patch"]').hidden = !legacy;
    byId("open-cli").hidden = !legacy;
    byId("base").hidden = !legacy;
    document.querySelector('label[for="base"]').hidden = !legacy;
    document.querySelector('.revision-range [data-lucide="arrow-right"]').toggleAttribute("hidden", !legacy);
    document.querySelector(".interface-filter").hidden = !legacy;
    byId("answer-button").hidden = !legacy;
    if (!legacy) {
      state.view = "entities";
      state.detail = "source";
      document.querySelector('label[for="head"]').textContent = "At";
    }
    byId("repo-name").textContent = state.boot.name === "relay" ? "Relay" : state.boot.name;
    const facts = byId("repo-facts");
    facts.replaceChildren();
    for (const [value, label] of [[state.boot.commits.length, "commits"], [state.boot.entities.length, "entities"], [state.boot.files.length, "files"]]) {
      const fact = node("span");
      fact.append(node("strong", "", String(value)), document.createTextNode(label));
      facts.append(fact);
    }
    for (const id of ["base", "head"]) {
      const select = byId(id);
      select.replaceChildren();
      for (const commit of state.boot.commits) {
        const tag = state.boot.tags.find((item) => item.sha === commit.sha);
        const option = node("option", "", tag ? `${tag.name}${commit.sha === state.boot.head ? " (HEAD)" : ""}` : `${commit.sha.slice(0, 7)} / ${commit.subject}`);
        option.value = commit.sha;
        option.title = `${commit.subject} / ${commit.author} / ${commit.date}`;
        select.append(option);
      }
      select.value = state.boot[id];
      select.disabled = false;
    }
    const coverage = state.boot.coverage;
    byId("coverage").textContent = `Index coverage: ${coverage.status}`;
    byId("coverage").title = `${coverage.records} records; ${coverage.gaps} gaps; ${coverage.uncovered} uncovered; ${coverage.excluded} excluded. Generation ${state.boot.generation}`;
    await compare();
  } catch (error) {
    report(error);
    byId("detail-content").replaceChildren(empty("Repository unavailable."));
    byId("summary").textContent = "Repository unavailable";
  } finally {
    byId("reload").disabled = false;
  }
}

document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => {
  state.view = button.dataset.view;
  byId("search").value = "";
  if (state.view === "entities") state.detail = "source";
  if (state.view === "changes") state.detail = "patch";
  updateView();
  renderDetail();
}));
document.querySelectorAll("[data-detail]").forEach((button) => button.addEventListener("click", () => {
  state.detail = button.dataset.detail;
  renderDetail();
}));
byId("search").addEventListener("input", renderList);
byId("interface-only").addEventListener("change", renderList);
byId("base").addEventListener("change", compare);
byId("head").addEventListener("change", compare);
byId("reload").addEventListener("click", () => window.location.reload());
byId("answer-button").addEventListener("click", () => {
  if (!state.diff) return;
  byId("output-command").textContent = `gita diff ${revisionLabel(byId("base").value)} ${revisionLabel(byId("head").value)}`;
  byId("output-text").textContent = state.diff.answer;
  byId("output-dialog").showModal();
});
byId("close-output").addEventListener("click", () => byId("output-dialog").close());
byId("output-dialog").addEventListener("click", (event) => {
  if (event.target === byId("output-dialog")) {
    const rect = event.target.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close();
  }
});
byId("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(state.copyText);
    byId("copy").replaceChildren(icon("check"));
    byId("copy").title = "Copied";
    icons();
    window.setTimeout(() => { byId("copy").replaceChildren(icon("copy")); byId("copy").title = "Copy displayed code"; icons(); }, 1600);
  } catch {
    report(new Error("Clipboard access is unavailable in this browser."));
  }
});
const cliHistory = [];
let cliPosition = 0;
let cliDraft = "";
let cliBusy = false;

byId("open-cli").addEventListener("click", () => {
  byId("cli-dialog").showModal();
  byId("cli-command").focus();
});
byId("close-cli").addEventListener("click", () => byId("cli-dialog").close());
byId("clear-cli").addEventListener("click", () => {
  byId("cli-transcript").replaceChildren();
  byId("cli-command").focus();
});
byId("cli-command").addEventListener("keydown", (event) => {
  if (cliBusy || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
  event.preventDefault();
  if (cliPosition === cliHistory.length) cliDraft = event.target.value;
  cliPosition = Math.max(0, Math.min(cliHistory.length, cliPosition + (event.key === "ArrowUp" ? -1 : 1)));
  event.target.value = cliHistory[cliPosition] ?? cliDraft;
});
byId("cli-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const command = byId("cli-command").value.trim();
  if (cliBusy || !command) return;
  cliBusy = true;
  cliHistory.push(command);
  cliPosition = cliHistory.length;
  cliDraft = "";
  byId("run-cli").disabled = true;
  byId("cli-command").disabled = true;
  byId("clear-cli").disabled = true;
  byId("cli-status").textContent = "Running...";
  const entry = node("section", "cli-entry");
  entry.append(node("div", "cli-invocation", `$ ${command}`));
  const output = node("pre", "cli-output");
  entry.append(output);
  byId("cli-transcript").append(entry);
  while (byId("cli-transcript").children.length > 20) byId("cli-transcript").firstElementChild.remove();
  try {
    const response = await fetch("/api/cli", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command }),
      signal: AbortSignal.timeout(35000),
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(errorMessage(result.error));
    output.textContent = result.output || "(no output)";
    output.classList.toggle("cli-failure", result.exit_code !== 0);
    byId("cli-status").textContent = `Exit ${result.exit_code}${result.truncated ? " / output truncated" : ""}`;
  } catch (error) {
    output.textContent = error.message;
    output.classList.add("cli-failure");
    byId("cli-status").textContent = "Command failed";
  } finally {
    cliBusy = false;
    byId("run-cli").disabled = false;
    byId("cli-command").disabled = false;
    byId("clear-cli").disabled = false;
    byId("cli-command").value = "";
    byId("cli-command").focus();
    byId("cli-transcript").scrollTop = byId("cli-transcript").scrollHeight;
  }
});
boot();