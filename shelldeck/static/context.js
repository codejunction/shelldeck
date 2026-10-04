// Context page: what agents remember about a project (context.py, local context.db). Task and state, memory with its
// verification status, decisions, related projects, and search across every project. Not polled: edits aren't lost.
import { $, api, esc, icon, toast, toastError } from "./ui.js";
import { orderedProjects } from "./app.js";

let selected = null; // project name or id, as /api/context takes it
let last = null; // the loaded context package

const STATUS = { NEW: "new", VERIFIED: "verified", STALE: "stale: source changed", REVIEWED: "reviewed", INVALIDATED: "invalidated" };
const TASK_STATUSES = ["TODO", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED"];

export async function renderContext(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.addEventListener("click", (e) => onClick(e, el));
    el.addEventListener("submit", (e) => onSubmit(e, el));
    el.addEventListener("change", (e) => {
      if (e.target.matches("[data-ctx-project]")) {
        selected = e.target.value;
        renderContext(el);
      }
    });
  }
  const known = await api("/api/context/projects").then((r) => r.projects).catch(() => []);
  const names = [...new Set([...orderedProjects().map((p) => p.name), ...known.map((p) => p.name)])];
  if (!selected) selected = names[0] || null;
  let body = `<div class="card empty-row faint">Add a project, or run <code>sd remember</code> in a folder, to start its context.</div>`;
  if (selected) {
    try {
      last = await api(`/api/context?project=${encodeURIComponent(selected)}`);
      body = page(last, names);
    } catch (e) {
      last = null;
      body = `<div class="card faint">No context for ${esc(selected)} yet: ${esc(e.message || e)}</div>`;
    }
  }
  el.innerHTML = `<div class="page-head"><div><h1>Context</h1>
      <p>What agents keep about each project: the task, where it stands, facts, decisions. Stored only on this machine
      (<code>context.db</code>); secrets are removed before anything is saved. Agents use <code>sd context</code>, <code>sd recall</code>, <code>sd remember</code>.</p></div>
      ${names.length ? `<label class="muted">Project <select data-ctx-project>${names.map((n) => `<option ${n === selected ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>` : ""}</div>
    <form class="card ctx-search" data-form="recall"><input name="q" placeholder="Search every project's knowledge and decisions, e.g. authentication" aria-label="Search context" />
      <label class="muted small" title="An installed agent's smallest model adds related keywords first (a few tokens per new query; cached)"><input type="checkbox" name="smart" /> smart</label>
      <button class="btn">${icon("search")}Search</button></form>
    <div class="ctx-results"></div>
    ${body}`;
}

function page(c, names) {
  const t = c.task || {};
  const s = c.state || {};
  const g = c.git_state || {};
  const others = names.filter((n) => n !== c.project.name);
  return `<div class="ctx-grid">
    <form class="card ctx-card" data-form="state"><h2>Task</h2>
      <label class="field"><span>Title</span><input name="task" value="${esc(t.title || "")}" placeholder="What is being worked on" /></label>
      <div class="field-row"><label class="field"><span>Status</span><select name="status">${TASK_STATUSES.map((x) => `<option ${x === (t.status || "IN_PROGRESS") ? "selected" : ""}>${x}</option>`).join("")}</select></label></div>
      <label class="field"><span>Objective</span><textarea name="objective" rows="2">${esc(t.objective || "")}</textarea></label>
      <label class="field"><span>Plan</span><textarea name="plan" rows="3">${esc(t.plan || "")}</textarea></label>
      <h2>State</h2>
      <label class="field"><span>Current step</span><input name="step" value="${esc(s.step || "")}" /></label>
      <label class="field"><span>Next action</span><input name="next_action" value="${esc(s.next_action || "")}" /></label>
      <label class="field"><span>Tests</span><input name="tests" value="${esc(s.tests || "")}" /></label>
      <label class="field"><span>Last error</span><input name="last_error" value="${esc(s.last_error || "")}" /></label>
      <p class="faint small">${g.branch ? `Git: ${esc(g.branch)} @ ${esc(g.commit || "")}${g.dirty ? `, ${g.changed.length} changed` : ", clean"}` : "Not a git checkout"}${s.updated_at ? ` · updated ${esc(s.updated_at.replace("T", " ").slice(0, 16))}${s.agent ? ` by ${esc(s.agent)}` : ""}` : ""}</p>
      <div class="row"><button class="btn primary">Save</button></div>
    </form>
    <div class="ctx-col">
      <div class="card ctx-card"><h2>Memory</h2>
        ${c.memory.length ? `<ul class="ctx-list">${c.memory.map(fact).join("")}</ul>` : `<p class="faint">No facts yet.</p>`}
        <form data-form="remember" class="ctx-add"><input name="text" placeholder="A durable fact, e.g. Refresh tokens live in Redis" aria-label="New fact" />
          <input name="files" placeholder="Source file (optional)" aria-label="Source file" /><button class="btn sm">Add</button></form>
      </div>
      <div class="card ctx-card"><h2>Decisions</h2>
        ${c.decisions.length ? `<ul class="ctx-list">${c.decisions.map((d) => `<li><b>${esc(d.title)}</b>${d.reason ? `<br><span class="faint">because ${esc(d.reason)}</span>` : ""}<br><span class="faint small">${esc(d.created_at.slice(0, 10))}${d.created_by ? ` · ${esc(d.created_by)}` : ""}</span></li>`).join("")}</ul>` : `<p class="faint">No decisions yet.</p>`}
        <form data-form="decide" class="ctx-add"><input name="title" placeholder="A settled decision" aria-label="New decision" /><input name="reason" placeholder="Reason" aria-label="Reason" /><button class="btn sm">Add</button></form>
      </div>
      <div class="card ctx-card"><h2>Related projects</h2>
        ${c.related_projects.length ? `<ul class="ctx-list">${c.related_projects.map((r) => `<li>${esc(r.relation)}: <b>${esc(r.name)}</b></li>`).join("")}</ul>` : `<p class="faint">None. Related projects rank higher in search.</p>`}
        ${others.length ? `<form data-form="relate" class="ctx-add"><select name="relation">${["related-to", "depends-on", "uses-pattern", "shares-database-with"].map((r) => `<option>${r}</option>`).join("")}</select>
          <select name="target">${others.map((n) => `<option>${esc(n)}</option>`).join("")}</select><button class="btn sm">Link</button></form>` : ""}
      </div>
      ${c.recent_events.length ? `<div class="card ctx-card"><h2>Recent events</h2><ul class="ctx-list small">${c.recent_events.map((e) => `<li>${esc(e.timestamp.replace("T", " ").slice(0, 16))} ${esc(e.type.toLowerCase().replace(/_/g, " "))}${e.agent ? ` · ${esc(e.agent)}` : ""}</li>`).join("")}</ul></div>` : ""}
    </div>
  </div>`;
}

function fact(k) {
  const verify = ["VERIFIED", "REVIEWED"].includes(k.status) ? "" : `<button class="btn sm" data-verify="${k.id}">${k.status === "STALE" ? "Reviewed" : "Verify"}</button>`;
  const actions = k.status === "INVALIDATED" ? "" : `${verify}
    <button class="btn sm" data-invalidate="${k.id}">Invalidate</button>`;
  return `<li class="ctx-fact ${k.status.toLowerCase()}"><span class="ctx-badge">${esc(STATUS[k.status] || k.status)}</span> ${esc(k.content)}
    ${k.sources.length ? `<br><span class="faint small">from ${k.sources.map((x) => esc(x.file_path)).join(", ")}</span>` : ""}
    <span class="faint small"> · ${esc(k.type)} · ${esc(k.id)}</span><div class="row">${actions}</div></li>`;
}

async function onClick(e, el) {
  const verify = e.target.closest("[data-verify]")?.dataset.verify;
  const invalid = e.target.closest("[data-invalidate]")?.dataset.invalidate;
  const id = verify || invalid;
  if (!id) return;
  const status = invalid ? "INVALIDATED" : last?.memory.find((k) => k.id === id)?.status === "STALE" ? "REVIEWED" : "VERIFIED";
  try {
    await api(`/api/context/knowledge/${id}/status`, { method: "POST", body: { status } });
    renderContext(el);
  } catch (err) {
    toastError(err);
  }
}

async function onSubmit(e, el) {
  const form = e.target.closest("form[data-form]");
  if (!form) return;
  e.preventDefault();
  const f = Object.fromEntries(new FormData(form).entries());
  const where = { project: selected };
  try {
    if (form.dataset.form === "recall") return showResults(el, f.q, !!f.smart);
    if (form.dataset.form === "state") await api("/api/context/state", { method: "POST", body: { ...where, ...f } });
    if (form.dataset.form === "remember") {
      if (!f.text.trim()) return;
      await api("/api/context/memory", { method: "POST", body: { ...where, text: f.text, files: f.files ? [f.files] : [] } });
    }
    if (form.dataset.form === "decide") {
      if (!f.title.trim()) return;
      await api("/api/context/decisions", { method: "POST", body: { ...where, title: f.title, reason: f.reason } });
    }
    if (form.dataset.form === "relate") await api("/api/context/relationships", { method: "POST", body: { ...where, target: f.target, relation: f.relation } });
    toast({ title: "Saved" });
    renderContext(el);
  } catch (err) {
    toastError(err);
  }
}

async function showResults(el, q, smart = false) {
  const box = $(".ctx-results", el);
  if (!q.trim()) {
    box.innerHTML = "";
    return;
  }
  const { results, expanded, agent } = await api(`/api/context/recall?q=${encodeURIComponent(q)}&project=${encodeURIComponent(selected || "")}${smart ? "&smart=true" : ""}`);
  const also = expanded?.length ? `<p class="faint small">Also searched (${esc(agent)}): ${expanded.map(esc).join(", ")}</p>` : smart ? `<p class="faint small">No agent available for smart search: keywords only.</p>` : "";
  box.innerHTML = results.length
    ? `<div class="card">${also}<p class="faint small">Reference material, best match first, with its project and source files: check the sources and adapt, don't copy.</p><ul class="ctx-list">${results
        .map((r) => `<li><b>${esc(r.project || "")}</b> · ${esc(r.title)} <span class="faint small">(${esc(r.type)}, relevance ${r.relevance}${r.status === "STALE" ? ", stale" : ""})</span><br>${esc(r.snippet)}${r.sources.length ? `<br><span class="faint small">from ${r.sources.map(esc).join(", ")}</span>` : ""}</li>`)
        .join("")}</ul></div>`
    : `<div class="card faint">${also}Nothing found for ${esc(q)}.</div>`;
}
