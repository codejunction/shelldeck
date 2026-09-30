import { S, TERMINAL_THEMES, applySettings, findSession, newTerminal, orderedProjects, palette, refreshProjects, sessionTitle, shellLabel } from "./app.js";
import { renderMonitor } from "./monitor.js";
import { renderHistory } from "./history.js";
import { renderDevices } from "./devices.js";
import { renderAgents } from "./agents.js";
import { $, $$, api, authError, confirmDialog, dialog, esc, fmtTime, fromLocalInput, hydrateIcons, icon, menu, toLocalInput, toast, toastError, withEyes } from "./ui.js";

let bookmarks = [];
let current = null; // visible page view
let pollTimer = null;

export function init() {}

export function show(view, el) {
  current = view;
  clearInterval(pollTimer);
  const render = { bookmarks: renderBookmarks, scheduler: renderScheduler, tasks: renderTasks, monitor: renderMonitor, history: renderHistory, devices: renderDevices, agents: renderAgents }[view];
  render(el);
  if (!["bookmarks", "monitor", "history"].includes(view)) { // monitor refreshes from its own 2s poller
    pollTimer = setInterval(() => {
      if (current === view && !el.hidden && !document.querySelector(".dialog-bg")) render(el);
      else if (el.hidden) clearInterval(pollTimer);
    }, 5000);
  }
}

function projectOptions(selected, { none = "" } = {}) {
  const opts = orderedProjects()
    .map((p) => `<option value="${p.id}" ${p.id === selected ? "selected" : ""}>${esc(p.name)}</option>`)
    .join("");
  return (none ? `<option value="">${esc(none)}</option>` : "") + opts;
}

function projectName(id) {
  return S.projects.find((p) => p.id === id)?.name;
}

// -------------------------------------------------------------- bookmarks

export function bookmarkCache() {
  return bookmarks;
}

export async function loadBookmarks() {
  try {
    bookmarks = (await api("/api/bookmarks")).bookmarks;
  } catch {
    /* offline; keep cache */
  }
  return bookmarks;
}

async function renderBookmarks(el) {
  await loadBookmarks();
  const q = el.querySelector("input[type=search]")?.value || "";
  const list = bookmarks.filter((b) => `${b.name} ${b.command}`.toLowerCase().includes(q.toLowerCase()));
  el.innerHTML = `<div class="page-head">
      <div><h1>Bookmarks</h1><p>Saved commands. Run inserts the command into a terminal; Shift+click runs it.</p></div>
      <div class="row"><input type="search" placeholder="Filter…" value="${esc(q)}" style="width:200px" aria-label="Filter bookmarks" />
      <button class="btn primary" data-b="new">${icon("plus")}New bookmark</button></div>
    </div>
    ${
      list.length
        ? `<div class="list">${list
            .map(
              (b) => `<div class="list-item" data-id="${b.id}">
              <div><b>${esc(b.name)}</b></div>
              <div class="cmd" title="${esc(b.command)}">${esc(b.command)}</div>
              <span class="badge scope">${esc(projectName(b.project_id) || "Any project")}</span>
              <div class="actions">
                <button class="icon-btn sm" data-b="run" title="Insert into terminal (Shift+click to run)" aria-label="Run">${icon("play")}</button>
                <button class="icon-btn sm" data-b="edit" title="Edit" aria-label="Edit">${icon("pencil")}</button>
                <button class="icon-btn sm danger" data-b="del" title="Delete" aria-label="Delete">${icon("trash")}</button>
              </div></div>`,
            )
            .join("")}</div>`
        : `<div class="list"><div class="placeholder">${bookmarks.length ? "No bookmarks match." : "No bookmarks yet. Save commands you run often."}</div></div>`
    }`;
  const search = el.querySelector("input[type=search]");
  search.oninput = () => {
    const pos = search.selectionStart;
    renderBookmarks(el).then(() => {
      const s = el.querySelector("input[type=search]");
      s.focus();
      s.setSelectionRange(pos, pos);
    });
  };
  el.onclick = async (e) => {
    const b = e.target.closest("[data-b]");
    if (!b) return;
    const id = e.target.closest("[data-id]")?.dataset.id;
    const bm = bookmarks.find((x) => x.id === id);
    if (b.dataset.b === "new") bookmarkDialog(null, () => renderBookmarks(el));
    if (b.dataset.b === "edit") bookmarkDialog(bm, () => renderBookmarks(el));
    if (b.dataset.b === "run") runBookmarks([bm], { execute: e.shiftKey });
    if (b.dataset.b === "del" && (await confirmDialog(`Delete bookmark "${bm.name}"?`, { ok: "Delete", danger: true }))) {
      await api(`/api/bookmarks/${id}`, { method: "DELETE" }).catch(toastError);
      renderBookmarks(el);
    }
  };
}

export function bookmarkDialog(bm, onSaved, preset = {}) {
  const v = bm || { name: "", command: "", project_id: preset.project_id || "", ...preset };
  const d = dialog({
    title: bm ? "Edit bookmark" : "New bookmark",
    body: `<form>
      <label class="field"><span>Name</span><input type="text" name="name" value="${esc(v.name)}" required autofocus /></label>
      <label class="field"><span>Command</span><textarea name="command" class="mono" required>${esc(v.command)}</textarea></label>
      <label class="field"><span>Project</span><select name="project_id">${projectOptions(v.project_id, { none: "Any project" })}</select></label>
      <div class="error"></div></form>`,
    foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-ok>${bm ? "Save" : "Create"}</button>`,
  });
  const form = $("form", d.el);
  const submit = async (e) => {
    e?.preventDefault();
    const body = { name: form.name.value, command: form.command.value, project_id: form.project_id.value || null };
    try {
      await api(bm ? `/api/bookmarks/${bm.id}` : "/api/bookmarks", { method: bm ? "PUT" : "POST", body });
      await loadBookmarks();
      d.close();
      onSaved?.();
    } catch (err) {
      $(".error", d.el).textContent = err.message.replaceAll("_", " ");
    }
  };
  form.onsubmit = submit;
  $("[data-ok]", d.el).onclick = submit;
  form.command.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(e);
  });
}

/** Insert (or with execute, run) bookmarks. Reuses the focused terminal when the project matches. */
export async function runBookmarks(list, { execute = false } = {}) {
  const focused = S.focused && findSession(S.focused);
  for (const [i, b] of list.entries()) {
    const sameProject = focused && (!b.project_id || b.project_id === focused.project.id);
    let term;
    if (i === 0 && sameProject) {
      term = S.terms.get(focused.id);
    } else {
      term = await newTerminal(b.project_id || focused?.project.id);
    }
    if (!term) continue;
    await term.ready();
    term.send({ type: "input", data: b.command + (execute ? "\r" : "") });
    term.focus();
  }
}

export async function bookmarkPicker() {
  await loadBookmarks();
  if (!bookmarks.length) {
    toast({ title: "No bookmarks yet", actions: [{ label: "Create one", onClick: () => bookmarkDialog(null) }] });
    return;
  }
  const items = bookmarks.map((b) => ({ label: b.name, hint: b.command, icon: "bookmark", group: projectName(b.project_id) || "Any project", b }));
  palette({
    placeholder: "Run bookmark…",
    items,
    multi: (picked, e) => runBookmarks(picked.map((x) => x.b), { execute: e?.shiftKey }),
    footer: "Enter inserts · Shift+Enter runs · Space or Ctrl+click selects several",
  });
}

// -------------------------------------------------------------- scheduler

const JOB_COLUMNS = [
  ["never_run", "Never run"],
  ["running", "Running"],
  ["success", "Success"],
  ["failed", "Failed"],
  ["disabled", "Disabled"],
];

async function renderScheduler(el) {
  let jobs = [];
  try {
    jobs = (await api("/api/schedule/jobs")).jobs;
  } catch (e) {
    return toastError(e);
  }
  const col = (id) => jobs.filter((j) => (id === "disabled" ? !j.enabled : j.enabled && (j.last_status || "never_run") === id));
  el.innerHTML = `<div class="page-head">
      <div><h1>Scheduler</h1><p>Cron jobs that run a command in a project folder with your default shell.</p></div>
      <button class="btn primary" data-j="new">${icon("plus")}New job</button>
    </div>
    <div class="board">${JOB_COLUMNS.map(
      ([id, label]) => `<div class="col"><div class="col-head"><span>${label}</span><span class="count">${col(id).length}</span></div>
        ${col(id)
          .map(
            (j) => `<div class="card" data-id="${j.id}" tabindex="0">
              <div class="title"><span>${esc(j.name)}</span><span class="status-dot ${j.enabled ? j.last_status : ""}"></span></div>
              <div class="meta mono">${esc(j.cron)} · ${esc(j.timezone)}</div>
              <div class="meta">${esc(projectName(j.project_id) || "?")} · next ${j.next_run_on ? fmtTime(j.next_run_on) : "–"} · ${j.successful_runs}/${j.total_runs} ok</div>
            </div>`,
          )
          .join("")}</div>`,
    ).join("")}</div>`;
  el.onclick = (e) => {
    if (e.target.closest("[data-j=new]")) return jobDialog(null, () => renderScheduler(el));
    const card = e.target.closest(".card");
    if (card) jobDialog(jobs.find((j) => j.id === card.dataset.id), () => renderScheduler(el));
  };
  el.onkeydown = (e) => {
    if (e.key === "Enter" && e.target.classList.contains("card")) e.target.click();
  };
}

function jobDialog(job, onDone) {
  if (!S.projects.length) return toast({ title: "Add a project first" });
  const v = job || { name: "", command: "", cron: "0 * * * *", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", timeout_seconds: 60, enabled: true, project_id: S.focused ? findSession(S.focused)?.project.id : "" };
  const d = dialog({
    title: job ? "Edit job" : "New job",
    wide: !!job,
    body: `<form>
      <div class="field-row">
        <label class="field"><span>Name</span><input type="text" name="name" value="${esc(v.name)}" required autofocus /></label>
        <label class="field"><span>Project</span><select name="project_id">${projectOptions(v.project_id)}</select></label>
      </div>
      <label class="field"><span>Command</span><textarea name="command" class="mono" required>${esc(v.command)}</textarea></label>
      <div class="field-row">
        <label class="field"><span>Cron (min hour day month weekday)</span><input type="text" name="cron" class="mono" value="${esc(v.cron)}" required /></label>
        <label class="field"><span>Timezone</span><input type="text" name="timezone" value="${esc(v.timezone)}" /></label>
        <label class="field"><span>Timeout (s)</span><input type="number" name="timeout_seconds" min="1" value="${v.timeout_seconds}" /></label>
      </div>
      <label class="check"><input type="checkbox" name="enabled" ${v.enabled ? "checked" : ""} /> Enabled</label>
      <div class="error"></div>
      ${job ? `<div class="logs"><div class="faint">Loading runs…</div></div>` : ""}
    </form>`,
    foot: `${job ? `<button class="btn danger" data-a="del">${icon("trash")}Delete</button><button class="btn" data-a="run">${icon("play")}Run now</button>` : ""}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" data-a="save">Save</button>`,
  });
  const form = $("form", d.el);
  const save = async (e) => {
    e?.preventDefault();
    const body = {
      name: form.name.value,
      project_id: form.project_id.value,
      command: form.command.value,
      cron: form.cron.value,
      timezone: form.timezone.value || "UTC",
      timeout_seconds: +form.timeout_seconds.value || 60,
      enabled: form.enabled.checked,
    };
    try {
      await api(job ? `/api/schedule/jobs/${job.id}` : "/api/schedule/jobs", { method: job ? "PUT" : "POST", body });
      d.close();
      onDone();
    } catch (err) {
      $(".error", d.el).textContent = err.message.replaceAll("_", " ");
    }
  };
  form.onsubmit = save;
  d.el.addEventListener("click", async (e) => {
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "save") save(e);
    if (a === "run") {
      await api(`/api/schedule/jobs/${job.id}/run`, { method: "POST" }).catch(toastError);
      toast({ title: `Queued "${job.name}"` });
      setTimeout(loadRuns, 1500);
    }
    if (a === "del" && (await confirmDialog(`Delete job "${job.name}" and its run history?`, { ok: "Delete", danger: true }))) {
      await api(`/api/schedule/jobs/${job.id}`, { method: "DELETE" }).catch(toastError);
      d.close();
      onDone();
    }
  });
  const loadRuns = async () => {
    if (!job) return;
    const box = $(".logs", d.el);
    try {
      const { runs } = await api(`/api/schedule/jobs/${job.id}/runs?limit=20`);
      box.innerHTML = `<div class="faint" style="margin-bottom:6px">Recent runs</div>${
        runs.length
          ? runs
              .map(
                (r) => `<div class="log"><div class="log-head"><span class="status-dot ${esc(r.status)}"></span>${esc(fmtTime(r.run_at))} · ${esc(r.status)} · ${r.duration_ms ?? 0} ms${r.exit_code != null ? ` · exit ${r.exit_code}` : ""}</div>
                <pre>${esc((r.output || "") + (r.error ? `\n${r.error}` : "")) || "(no output)"}</pre></div>`,
              )
              .join("")
          : `<div class="faint">No runs yet.</div>`
      }`;
    } catch {
      box.innerHTML = "";
    }
  };
  loadRuns();
}

// ------------------------------------------------------------------ tasks

const TASK_COLUMNS = [
  ["backlog", "Backlog"],
  ["todo", "To do"],
  ["inprogress", "In progress"],
  ["done", "Done"],
];

async function renderTasks(el) {
  let tasks = [];
  try {
    tasks = (await api("/api/tasks")).tasks;
  } catch (e) {
    return toastError(e);
  }
  const overdue = (t) => t.due_at && t.column !== "done" && new Date(t.due_at) <= new Date();
  el.innerHTML = `<div class="page-head">
      <div><h1>Tasks</h1><p>Personal board with due dates and reminders. Drag cards between columns.</p></div>
      <button class="btn primary" data-t="new">${icon("plus")}New task</button>
    </div>
    <div class="board">${TASK_COLUMNS.map(([id, label]) => {
      const items = tasks.filter((t) => t.column === id);
      return `<div class="col" data-col="${id}"><div class="col-head"><span>${label}</span><span class="count">${items.length}</span></div>
        ${id !== "done" ? `<button class="add-card" data-t="add" data-col="${id}">+ Add task</button>` : ""}
        ${items
          .map(
            (t) => `<div class="card ${overdue(t) ? "overdue" : ""}" draggable="true" data-id="${t.id}" tabindex="0">
              <div class="title"><span>${esc(t.title)}</span><span class="prio ${esc(t.priority)}">${esc(t.priority)}</span></div>
              ${t.due_at || t.reminder_at ? `<div class="meta">${t.due_at ? `due ${fmtTime(t.due_at)}` : `remind ${fmtTime(t.reminder_at)}`}</div>` : ""}
              ${(t.tags || "")
                .split(",")
                .map((x) => x.trim())
                .filter(Boolean)
                .map((x) => `<span class="tag">${esc(x)}</span>`)
                .join("")}
            </div>`,
          )
          .join("")}</div>`;
    }).join("")}</div>`;
  el.onclick = (e) => {
    const t = e.target.closest("[data-t]");
    if (t) return taskDialog(null, t.dataset.col || "backlog", () => renderTasks(el));
    const card = e.target.closest(".card");
    if (card) taskDialog(tasks.find((x) => x.id === card.dataset.id), null, () => renderTasks(el));
  };
  el.onkeydown = (e) => {
    if (e.key === "Enter" && e.target.classList.contains("card")) e.target.click();
  };
  let dragId = null;
  el.ondragstart = (e) => {
    const card = e.target.closest(".card");
    if (!card) return;
    dragId = card.dataset.id;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  };
  el.ondragend = () => {
    dragId = null;
    $$(".dragging, .drag-over", el).forEach((x) => x.classList.remove("dragging", "drag-over"));
  };
  el.ondragover = (e) => {
    const col = e.target.closest(".col");
    if (!dragId || !col) return;
    e.preventDefault();
    $$(".col", el).forEach((c) => c.classList.toggle("drag-over", c === col));
  };
  el.ondrop = async (e) => {
    const col = e.target.closest(".col");
    if (!dragId || !col) return;
    e.preventDefault();
    await api(`/api/tasks/${dragId}/move`, { method: "POST", body: { column: col.dataset.col } }).catch(toastError);
    renderTasks(el);
  };
}

function taskDialog(task, column, onDone) {
  const v = task || { title: "", description: "", column, priority: "medium", due_at: null, reminder_at: null, tags: "" };
  const d = dialog({
    title: task ? "Edit task" : "New task",
    body: `<form>
      <label class="field"><span>Title</span><input type="text" name="title" value="${esc(v.title)}" required autofocus /></label>
      <label class="field"><span>Description</span><textarea name="description">${esc(v.description)}</textarea></label>
      <div class="field-row">
        <label class="field"><span>Column</span><select name="column">${TASK_COLUMNS.map(([id, l]) => `<option value="${id}" ${id === v.column ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field"><span>Priority</span><select name="priority">${["low", "medium", "high", "urgent"].map((p) => `<option ${p === v.priority ? "selected" : ""}>${p}</option>`).join("")}</select></label>
      </div>
      <div class="field-row">
        <label class="field"><span>Due</span><input type="datetime-local" name="due_at" value="${toLocalInput(v.due_at)}" /></label>
        <label class="field"><span>Reminder</span><input type="datetime-local" name="reminder_at" value="${toLocalInput(v.reminder_at)}" /></label>
      </div>
      <label class="field"><span>Tags (comma separated)</span><input type="text" name="tags" value="${esc(v.tags)}" /></label>
      <div class="error"></div></form>`,
    foot: `${task ? `<button class="btn danger" data-a="del">${icon("trash")}Delete</button>` : ""}${task && Object.keys(S.agents).length ? `<button class="btn" data-a="agent" title="Type this task into a running AI agent">${icon("sparkle")}Send to agent</button>` : ""}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" data-a="save">Save</button>`,
  });
  const form = $("form", d.el);
  const save = async (e) => {
    e?.preventDefault();
    const reminder = fromLocalInput(form.reminder_at.value);
    const body = {
      title: form.title.value,
      description: form.description.value,
      column: form.column.value,
      priority: form.priority.value,
      due_at: fromLocalInput(form.due_at.value),
      reminder_at: reminder,
      tags: form.tags.value,
    };
    if (task && reminder !== task.reminder_at) body.reminder_acknowledged = false;
    try {
      await api(task ? `/api/tasks/${task.id}` : "/api/tasks", { method: task ? "PUT" : "POST", body });
      if (reminder) requestNotify();
      d.close();
      onDone();
    } catch (err) {
      $(".error", d.el).textContent = err.message.replaceAll("_", " ");
    }
  };
  form.onsubmit = save;
  d.el.addEventListener("click", async (e) => {
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "save") save(e);
    if (a === "agent") {
      const items = Object.entries(S.agents).map(([sid, ag]) => {
        const s = findSession(sid);
        return { label: `${ag.label} · ${s ? sessionTitle(s) : sid}`, hint: s?.project.name || "", icon: "sparkle", onClick: () => sendTask(task, sid, ag).then((ok) => ok && (d.close(), onDone())) };
      });
      menu(e.target.closest("[data-a]"), [{ header: "Send to" }, ...items]);
    }
    if (a === "del" && (await confirmDialog(`Delete task "${task.title}"?`, { ok: "Delete", danger: true }))) {
      await api(`/api/tasks/${task.id}`, { method: "DELETE" }).catch(toastError);
      d.close();
      onDone();
    }
  });
}

/** Type a task into an agent's terminal (one line, so a TUI doesn't submit early) and mark it in progress. */
async function sendTask(task, sid, agent) {
  const text = `Task: ${task.title}${task.description ? ` - ${task.description}` : ""}`.replace(/\s+/g, " ").trim();
  try {
    await api(`/api/sessions/${sid}/input`, { method: "POST", body: { text } });
    if (task.column !== "inprogress" && task.column !== "done") await api(`/api/tasks/${task.id}/move`, { method: "POST", body: { column: "inprogress" } });
    toast({ title: `Sent to ${agent.label}`, body: task.title });
    return true;
  } catch (err) {
    toastError(err);
    return false;
  }
}

// ----------------------------------------------------------------- alarms

const alarmToasts = new Map();

function requestNotify() {
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
}

/** Three rising tones: task alarms and agents that need you. */
export function chime() {
  try {
    const ctx = new AudioContext();
    setTimeout(() => ctx.close(), 1200); // browsers cap open audio contexts
    [660, 880, 990].forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.18);
      g.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + i * 0.18 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.18 + 0.4);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + i * 0.18);
      o.stop(ctx.currentTime + i * 0.18 + 0.45);
    });
  } catch {
    /* audio blocked until user interaction */
  }
}

export function showAlarm(task, fresh) {
  if (alarmToasts.has(task.id)) return;
  if (fresh) {
    chime();
    if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
      new Notification(task.title, { body: task.due_at ? `Due ${fmtTime(task.due_at)}` : "Reminder", tag: task.id });
    }
  }
  const t = toast({
    title: task.title,
    body: task.due_at ? `Due ${fmtTime(task.due_at)}` : "Reminder",
    kind: "alarm",
    timeout: 0,
    actions: [
      { label: "Acknowledge", onClick: () => api(`/api/tasks/${task.id}/ack`, { method: "POST" }).catch(toastError) },
      { label: "Snooze 15 min", onClick: () => api(`/api/tasks/${task.id}/snooze`, { method: "POST", body: { minutes: 15 } }).catch(toastError) },
    ],
  });
  alarmToasts.set(task.id, t);
  new MutationObserver((_, obs) => {
    if (!t.el.isConnected) {
      alarmToasts.delete(task.id);
      obs.disconnect();
    }
  }).observe($("#toasts"), { childList: true });
}

// ---------------------------------------------------------- add project

export function addProjectDialog() {
  const d = dialog({
    title: "Add project",
    wide: true,
    body: `<form>
      <label class="field"><span>Folder path</span><input type="text" name="path" class="mono" placeholder="C:\\src\\my-app" autofocus /></label>
      <div class="row"><button type="button" class="btn sm" data-fs="up">${icon("up")}Up</button><span class="faint" data-fs="where"></span></div>
      <div class="fs-list" role="listbox" aria-label="Folders"></div>
      <label class="check" style="margin-top:12px"><input type="checkbox" name="open" checked /> Open a terminal in it</label>
      <div class="error"></div></form>`,
    foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-a="add">Add project</button>`,
  });
  const form = $("form", d.el);
  const list = $(".fs-list", d.el);
  let parent = null;
  const browse = async (path) => {
    try {
      const r = await api(`/api/fs/dirs?path=${encodeURIComponent(path)}`);
      parent = r.parent;
      if (r.path) form.path.value = r.path;
      $("[data-fs=where]", d.el).textContent = r.path || "Home and drives";
      const join = (name) => (r.path ? `${r.path.replace(/[\\/]$/, "")}${r.sep}${name}` : name);
      list.innerHTML = r.dirs.length
        ? r.dirs.map((n) => `<div class="fs-item" data-path="${esc(join(n))}">${icon("folder")}${esc(n)}</div>`).join("")
        : `<div class="placeholder">No sub-folders</div>`;
      $(".error", d.el).textContent = "";
    } catch (e) {
      $(".error", d.el).textContent = e.message.replaceAll("_", " ");
    }
  };
  list.addEventListener("click", (e) => {
    const it = e.target.closest(".fs-item");
    if (it) browse(it.dataset.path);
  });
  $("[data-fs=up]", d.el).onclick = () => parent !== null && browse(parent);
  form.path.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      browse(form.path.value.trim());
    }
  });
  const add = async (e) => {
    e?.preventDefault();
    try {
      const p = await api("/api/projects", { method: "POST", body: { path: form.path.value.trim() } });
      const open = form.open.checked;
      d.close();
      await refreshProjects();
      if (open) newTerminal(p.id);
    } catch (err) {
      $(".error", d.el).textContent = err.message.replaceAll("_", " ");
    }
  };
  form.onsubmit = add;
  $("[data-a=add]", d.el).onclick = add;
  browse("");
}

// --------------------------------------------------------------- settings

export async function saveSettings(patch) {
  try {
    S.settings = await api("/api/settings", { method: "PUT", body: patch });
    applySettings();
    return true;
  } catch (e) {
    toastError(e);
    return false;
  }
}

export async function settingsDialog() {
  let auth = { enabled: false };
  try {
    [S.shells, auth] = await Promise.all([api("/api/shells"), api("/api/auth/status")]);
  } catch {
    /* use cached */
  }
  const s = S.settings;
  const shellOpts = S.shells.kinds
    .map((k) => {
      const ok = S.shells.available.includes(k);
      return `<option value="${k}" ${k === s.default_shell ? "selected" : ""} ${ok ? "" : "disabled"}>${esc(shellLabel(k))}${ok ? "" : " (not installed)"}</option>`;
    })
    .join("");
  const distros = S.shells.wsl_distros;
  const d = dialog({
    title: "Settings",
    body: `<form>
      <div class="field-row">
        <label class="field"><span>Default shell</span><select name="default_shell">${shellOpts}</select></label>
        <label class="field"><span>WSL distribution</span><select name="wsl_distro" ${distros.length ? "" : "disabled"}>
          <option value="">${distros.length ? "System default" : "WSL not available"}</option>
          ${distros.map((x) => `<option ${x === s.wsl_distro ? "selected" : ""}>${esc(x)}</option>`).join("")}
        </select></label>
      </div>
      <div class="field-row">
        <label class="field"><span>Theme</span><select name="theme">${["dark", "light", "system"].map((t) => `<option ${t === s.theme ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <label class="field"><span>Terminal font size</span><input type="number" name="font_size" min="8" max="32" value="${esc(s.font_size)}" /></label>
      </div>
      <div class="field-row">
        <label class="field"><span>Pane layout</span><select name="layout_mode">
          <option value="tiled" ${s.layout_mode === "tiled" ? "selected" : ""}>Tiled: panes fill the window</option>
          <option value="free" ${s.layout_mode === "free" ? "selected" : ""}>Free: drag and resize windows</option>
        </select></label>
        <label class="field"><span>Project colors</span><select name="project_tint">
          <option value="on" ${s.project_tint !== "off" ? "selected" : ""}>Tint terminal background</option>
          <option value="off" ${s.project_tint === "off" ? "selected" : ""}>Off</option>
        </select></label>
      </div>
      <div class="field-row">
        <label class="field"><span>Terminal colors</span><select name="terminal_theme">${TERMINAL_THEMES.map(
          (t) => `<option value="${t}" ${t === (s.terminal_theme || "default") ? "selected" : ""}>${t === "default" ? "Default (follows theme)" : t.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</option>`,
        ).join("")}</select></label>
        <label class="field"><span>Terminal font</span><input name="font_family" list="font-list" placeholder="Default (Cascadia / Nerd Font)" value="${esc(s.font_family || "")}" />
          <datalist id="font-list">${["Cascadia Code", "Cascadia Mono", "CaskaydiaCove Nerd Font", "JetBrains Mono", "Fira Code", "Consolas", "Source Code Pro", "Hack", "Ubuntu Mono", "DejaVu Sans Mono", "Menlo"].map((f) => `<option value="${f}"></option>`).join("")}</datalist></label>
      </div>
      <div class="field-row">
        <label class="field"><span>Open file paths with</span><select name="editor">
          <option value="vscode" ${s.editor !== "system" ? "selected" : ""}>VS Code (at the line)</option>
          <option value="system" ${s.editor === "system" ? "selected" : ""}>System default app</option>
        </select></label>
        <span></span>
      </div>
      <p class="faint" style="margin:0 0 14px">New terminals use the default shell. Free layout scrolls when windows don't fit; use Tile all to arrange them.</p>
      <hr style="border:0;border-top:1px solid var(--border);margin:4px 0 14px" />
      <div style="margin-bottom:8px"><b>Change password</b> <span class="faint">· each browser locks after ${Math.round((auth.idle_timeout || 1800) / 60)} min idle; changing it signs out every other browser</span></div>
      <div class="field-row">
        <label class="field"><span>Current password</span><input type="password" name="current" autocomplete="current-password" /></label>
        <span></span>
      </div>
      <div class="field-row">
        <label class="field"><span>New password (8+ characters)</span><input type="password" name="password" autocomplete="new-password" /></label>
        <label class="field"><span>Confirm new password</span><input type="password" name="confirm" autocomplete="new-password" /></label>
      </div>
      <div class="row"><button type="button" class="btn sm" data-a="pw">Change password</button></div>
      <div class="error"></div>
    </form>`,
    foot: `<button class="btn" data-close>Close</button><button class="btn primary" data-a="save">Save</button>`,
  });
  const form = $("form", d.el);
  const save = async (e) => {
    e?.preventDefault();
    const ok = await saveSettings({
      default_shell: form.default_shell.value,
      wsl_distro: form.wsl_distro.value,
      theme: form.theme.value,
      font_size: form.font_size.value,
      layout_mode: form.layout_mode.value,
      project_tint: form.project_tint.value,
      terminal_theme: form.terminal_theme.value,
      editor: form.editor.value,
      font_family: form.font_family.value,
    });
    if (ok) {
      d.close();
      toast({ title: "Settings saved" });
    }
  };
  form.onsubmit = save;
  d.el.addEventListener("click", async (e) => {
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "save") save(e);
    if (a === "pw") {
      try {
        const btn = e.target.closest("[data-a]");
        btn.disabled = true;
        btn.textContent = "Changing…"; // password hashing takes about a second on purpose
        try {
          await api("/api/auth/password", { method: "PUT", body: { current: form.current.value, password: form.password.value, confirm: form.confirm.value } });
        } finally {
          btn.disabled = false;
          btn.textContent = "Change password";
        }
        toast({ title: "Password changed", body: "Other browsers were signed out." });
        d.close();
      } catch (err) {
        $(".error", d.el).textContent = authError(err);
      }
    }
  });
  hydrateIcons(d.el);
  withEyes(d.el);
}
