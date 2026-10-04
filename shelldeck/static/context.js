// Context page: what agents keep about a project (context.py, local context.db). Read-first: a "where things stand"
// summary, facts, decisions and recent activity in plain words; editing opens only when asked. Not polled.
import { $, api, esc, icon, toast, toastError } from "./ui.js";
import { orderedProjects } from "./app.js";

let selected = null; // project name, as /api/context takes it
let last = null; // the loaded context package
let editing = false; // the task/state form is open
let search = null; // {q, agent, model, results, expanded} while showing search results
let helpers = null; // installed agents and their models, for "widen the search with" (/api/context/recall-agents)
let pickedAgent = null; // the user's choice in the search box ("off" = plain keywords)
let pickedModel = "";

const TASK_STATUSES = [["TODO", "To do"], ["IN_PROGRESS", "In progress"], ["BLOCKED", "Blocked"], ["DONE", "Done"], ["CANCELLED", "Cancelled"]];
const STATUS_LABEL = Object.fromEntries(TASK_STATUSES);

export async function renderContext(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.addEventListener("click", (e) => onClick(e, el));
    el.addEventListener("submit", (e) => onSubmit(e, el));
    el.addEventListener("change", (e) => {
      if (e.target.matches("[data-recall-agent]")) {
        pickedAgent = e.target.value;
        pickedModel = "";
        $("[data-recall-model]", el)?.replaceWith(modelSelect());
        return;
      }
      if (e.target.matches("[data-recall-model]")) {
        pickedModel = e.target.value;
        return;
      }
      if (!e.target.matches("[data-ctx-project]")) return;
      selected = e.target.value;
      editing = false;
      search = null;
      renderContext(el);
    });
  }
  const known = await api("/api/context/projects").then((r) => r.projects).catch(() => []);
  if (!helpers) {
    const r = await api("/api/context/recall-agents").catch(() => ({ agents: [], default: "off" }));
    helpers = r.agents;
    const def = r.default === "auto" ? helpers[0]?.agent : r.default;
    pickedAgent = helpers.some((h) => h.agent === def) ? def : "off";
  }
  const names = [...new Set([...orderedProjects().map((p) => p.name), ...known.map((p) => p.name)])];
  if (!selected || !names.includes(selected)) selected = names[0] || null;
  last = null;
  if (selected) last = await api(`/api/context?project=${encodeURIComponent(selected)}`).catch(() => null);

  el.innerHTML = `${header(names)}
    ${!selected ? empty("No projects yet", "Add a project from the sidebar. Agents fill this page as they work.")
      : search ? results()
      : !last ? empty("Nothing recorded yet", `Agents add to it with <code>sd remember</code> and <code>sd task update</code>, or add a fact below.`)
      : body(last)}`;
}

function header(names) {
  const related = last?.related_projects || [];
  return `<div class="cx-head">
      <div class="cx-title"><h1>Context</h1>
        ${names.length ? `<select class="cx-project" data-ctx-project aria-label="Project">${names.map((n) => `<option ${n === selected ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>` : ""}</div>
      <p class="faint">What agents remember about this project, so the next one can pick up where the last stopped. Kept only on this computer.</p>
      ${selected ? `<form class="cx-search" data-form="recall" role="search">
        ${icon("search")}<input name="q" value="${esc(search?.q || "")}" placeholder="Search what every project knows…" aria-label="Search context" />
        ${helpers?.length ? `<span class="cx-widen" title="The chosen agent and model suggest related words before searching (a few tokens per new search; cached)">
          <select data-recall-agent aria-label="Widen the search with"><option value="off">Exact words</option>${helpers.map((h) => `<option value="${h.agent}" ${h.agent === pickedAgent ? "selected" : ""}>+ ${esc(h.label)}</option>`).join("")}</select>
          ${modelSelect().outerHTML}</span>` : ""}
      </form>` : ""}
      ${related.length ? `<p class="cx-related faint">Related: ${related.map((r) => `<b>${esc(r.name)}</b> <span>(${esc(r.relation.replace(/-/g, " "))})</span>`).join(", ")}</p>` : ""}
    </div>`;
}

function empty(title, text) {
  return `<div class="card cx-empty"><h2>${title}</h2><p class="faint">${text}</p>
    <form data-form="remember" class="cx-add"><input name="text" placeholder="Add a fact about this project, then press Enter" aria-label="New fact" /></form></div>`;
}

function body(c) {
  return `<div class="cx-grid">
    <div class="cx-main">${standing(c)}${facts(c)}</div>
    <div class="cx-side">${decisions(c)}${activity(c)}${relate(c)}</div>
  </div>`;
}

// ---------------------------------------------------------------- where things stand

function standing(c) {
  const t = c.task || {};
  const s = c.state || {};
  const g = c.git_state || {};
  if (editing) return editForm(t, s);
  const tests = s.tests ? `<span class="cx-pill ${/^failed/i.test(s.tests) ? "bad" : "good"}">${/^failed/i.test(s.tests) ? "Tests failing" : "Tests passing"}</span>` : "";
  const rows = [
    ["Next", s.next_action],
    ["Now", s.step],
    ["Tests", s.tests && s.tests.replace(/^(passed|failed):\s*/i, "")],
    ["Last error", s.last_error],
  ].filter(([, v]) => v);
  return `<section class="card cx-standing" aria-label="Where things stand">
    <div class="cx-standing-top">
      <div><span class="cx-label">Working on</span>
        <h2>${t.title ? esc(t.title) : `<span class="faint">No task set</span>`}</h2></div>
      <div class="cx-chips">${t.status ? `<span class="cx-pill">${esc(STATUS_LABEL[t.status] || t.status)}</span>` : ""}${tests}
        <button class="btn sm" data-cx="edit">${icon("pencil")}Edit</button></div>
    </div>
    ${t.objective ? `<p class="cx-objective">${esc(t.objective)}</p>` : ""}
    ${rows.length ? `<dl class="cx-rows">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : `<p class="faint">Nothing recorded about progress yet.</p>`}
    <p class="cx-meta faint">${g.branch ? `${esc(g.branch)} · ${g.dirty ? `${g.changed.length} file${g.changed.length === 1 ? "" : "s"} changed` : "clean"}` : "Not a git repository"}${s.updated_at ? ` · updated ${ago(s.updated_at)}${s.agent ? ` by ${esc(s.agent)}` : ""}` : ""}</p>
  </section>`;
}

function editForm(t, s) {
  const f = (name, label, value, hint = "") => `<label class="field"><span>${label}</span><input name="${name}" value="${esc(value || "")}" placeholder="${esc(hint)}" /></label>`;
  return `<form class="card cx-standing" data-form="state" aria-label="Edit where things stand">
    <h2>Where things stand</h2>
    ${f("task", "Working on", t.title, "e.g. Add login")}
    <div class="field-row">
      <label class="field"><span>Status</span><select name="status">${TASK_STATUSES.map(([v, l]) => `<option value="${v}" ${v === (t.status || "IN_PROGRESS") ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      ${f("next_action", "Next step", s.next_action, "What the next agent should do first")}
    </div>
    ${f("step", "Doing now", s.step)}
    <label class="field"><span>Goal</span><textarea name="objective" rows="2">${esc(t.objective || "")}</textarea></label>
    <details class="cx-more-fields"><summary class="faint">Plan, tests and errors</summary>
      <label class="field"><span>Plan</span><textarea name="plan" rows="3">${esc(t.plan || "")}</textarea></label>
      ${f("tests", "Tests", s.tests, "e.g. passed: pytest -q")}
      ${f("last_error", "Last error", s.last_error)}
    </details>
    <div class="row"><button class="btn primary">Save</button><button type="button" class="btn" data-cx="cancel">Cancel</button></div>
  </form>`;
}

// ---------------------------------------------------------------- facts & decisions

function facts(c) {
  const badge = { STALE: ["warn", "May be out of date"], VERIFIED: ["good", "Checked"], REVIEWED: ["good", "Reviewed"] };
  const items = c.memory.map((k) => {
    const b = badge[k.status];
    const src = k.sources.length ? `<span class="faint"> · ${k.sources.map((x) => esc(x.file_path)).join(", ")}</span>` : "";
    const verify = ["VERIFIED", "REVIEWED"].includes(k.status) ? "" : `<button class="icon-btn sm" data-verify="${k.id}" title="${k.status === "STALE" ? "Still true" : "Mark as checked"}" aria-label="Mark as checked">${icon("eye")}</button>`;
    return `<li class="cx-item">
      <div class="cx-text">${b ? `<span class="cx-tag ${b[0]}">${b[1]}</span> ` : ""}${esc(k.content)}${src}</div>
      <div class="cx-actions">${verify}<button class="icon-btn sm" data-invalidate="${k.id}" title="Not true any more" aria-label="Remove">${icon("trash")}</button></div>
    </li>`;
  }).join("");
  return `<section class="card cx-section" aria-label="Facts">
    <h2>Facts <span class="faint">${c.memory.length || ""}</span></h2>
    ${items ? `<ul class="cx-list">${items}</ul>` : `<p class="faint">No facts yet. Agents add what they learn; you can too.</p>`}
    <form data-form="remember" class="cx-add"><input name="text" placeholder="Add a fact, then press Enter" aria-label="New fact" /></form>
  </section>`;
}

function decisions(c) {
  const items = c.decisions.map((d) => `<li class="cx-item"><div class="cx-text"><b>${esc(d.title)}</b>${d.reason ? `<br><span class="faint">${esc(d.reason)}</span>` : ""}</div>
    <span class="faint small">${esc(d.created_at.slice(0, 10))}</span></li>`).join("");
  return `<section class="card cx-section" aria-label="Decisions">
    <h2>Decisions <span class="faint">${c.decisions.length || ""}</span></h2>
    ${items ? `<ul class="cx-list">${items}</ul>` : `<p class="faint">Settled choices go here, so no one reopens them.</p>`}
    <form data-form="decide" class="cx-add"><input name="title" placeholder="Add a decision, then press Enter" aria-label="New decision" /></form>
  </section>`;
}

// ---------------------------------------------------------------- activity in plain words

function describe(e) {
  let p = {};
  try {
    p = JSON.parse(e.payload || "{}");
  } catch {}
  const who = e.agent ? cap(e.agent) : "Someone";
  switch (e.type) {
    case "SESSION_START": return [`${who} started`, ""];
    case "SESSION_STOP": return [`${who} finished`, ""];
    case "TEST_RESULT": return [p.ok ? "Tests passed" : "Tests failed", p.command, p.ok ? "good" : "bad"];
    case "ERROR": return ["A command failed", p.command, "bad"];
    case "COMMAND": return ["Ran a command", p.command];
    case "FILE_EDIT": return ["Edited a file", p.file];
    case "FILE_CREATE": return ["Created a file", p.file];
    case "NOTE": case "DISCOVERY": return ["Added a fact", ""];
    case "DECISION": return ["Recorded a decision", ""];
    case "TASK_START": return ["Started a task", ""];
    case "TASK_COMPLETE": return ["Finished the task", "", "good"];
    case "TASK_UPDATE": return ["Updated progress", ""];
    default: return [cap(e.type.toLowerCase().replace(/_/g, " ")), ""];
  }
}

function activity(c) {
  if (!c.recent_events.length) return "";
  const items = c.recent_events.slice(0, 8).map((e) => {
    const [what, detail, tone] = describe(e);
    return `<li class="cx-event ${tone || ""}"><span class="cx-dot" aria-hidden="true"></span><div><span>${esc(what)}</span>${detail ? `<code>${esc(detail)}</code>` : ""}</div><time class="faint">${ago(e.timestamp)}</time></li>`;
  }).join("");
  return `<section class="card cx-section" aria-label="Recent activity"><h2>Recent activity</h2><ul class="cx-events">${items}</ul></section>`;
}

function relate(c) {
  const others = orderedProjects().map((p) => p.name).filter((n) => n !== c.project.name);
  if (!others.length) return "";
  return `<details class="card cx-section cx-relate"><summary><h2>Link another project</h2></summary>
    <p class="faint">Linked projects rank higher in search, so an agent here finds their knowledge first.</p>
    <form data-form="relate" class="cx-add">
      <select name="target" aria-label="Project">${others.map((n) => `<option>${esc(n)}</option>`).join("")}</select>
      <select name="relation" aria-label="How they relate">${[["related-to", "is related to"], ["depends-on", "depends on"], ["uses-pattern", "uses a pattern from"], ["shares-database-with", "shares a database with"]].map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <button class="btn sm">Link</button></form></details>`;
}

// ---------------------------------------------------------------- search

function results() {
  const s = search;
  const also = s.expanded?.length ? `<p class="faint small">Also searched for ${s.expanded.map(esc).join(", ")} (suggested by ${esc(s.agent)} ${esc(s.model || "")})</p>`
    : s.agent ? `<p class="faint small">${esc(s.agent)} suggested no extra words (or couldn't run); matched the words as typed.</p>` : "";
  const items = s.results.map((r) => `<li class="cx-item"><div class="cx-text">
      <span class="cx-tag">${esc(r.project || "")}</span> <b>${esc(r.title)}</b>${r.status === "STALE" ? ` <span class="cx-tag warn">may be out of date</span>` : ""}
      ${r.snippet !== r.title ? `<br><span>${esc(r.snippet)}</span>` : ""}
      ${r.sources.length ? `<br><span class="faint small">from ${r.sources.map(esc).join(", ")}</span>` : ""}</div></li>`).join("");
  return `<section class="card cx-section">
    <div class="cx-results-head"><h2>${s.results.length} result${s.results.length === 1 ? "" : "s"} for “${esc(s.q)}”</h2><button class="btn sm" data-cx="back">Back</button></div>
    ${also}
    ${items ? `<ul class="cx-list">${items}</ul><p class="faint small">From other projects these are references: check the source and adapt, don't copy.</p>` : `<p class="faint">Nothing found. Try other words, or let an agent widen the search.</p>`}
  </section>`;
}

// ---------------------------------------------------------------- events

async function onClick(e, el) {
  const act = e.target.closest("[data-cx]")?.dataset.cx;
  if (act === "edit" || act === "cancel") {
    editing = act === "edit";
    return renderContext(el);
  }
  if (act === "back") {
    search = null;
    return renderContext(el);
  }
  const id = e.target.closest("[data-verify]")?.dataset.verify || e.target.closest("[data-invalidate]")?.dataset.invalidate;
  if (!id) return;
  const invalid = !!e.target.closest("[data-invalidate]");
  const status = invalid ? "INVALIDATED" : last?.memory.find((k) => k.id === id)?.status === "STALE" ? "REVIEWED" : "VERIFIED";
  try {
    await api(`/api/context/knowledge/${id}/status`, { method: "POST", body: { status } });
    if (invalid) toast({ title: "Removed from facts" });
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
    if (form.dataset.form === "recall") {
      if (!f.q.trim()) {
        search = null;
        return renderContext(el);
      }
      const params = new URLSearchParams({ q: f.q, project: selected || "", agent: pickedAgent || "off" });
      if (pickedAgent !== "off" && pickedModel) params.set("model", pickedModel);
      const r = await api(`/api/context/recall?${params}`);
      search = { q: f.q, ...r };
      return renderContext(el);
    }
    if (form.dataset.form === "state") {
      await api("/api/context/state", { method: "POST", body: { ...where, ...f } });
      editing = false;
    }
    if (form.dataset.form === "remember") {
      if (!f.text.trim()) return;
      await api("/api/context/memory", { method: "POST", body: { ...where, text: f.text } });
    }
    if (form.dataset.form === "decide") {
      if (!f.title.trim()) return;
      await api("/api/context/decisions", { method: "POST", body: { ...where, title: f.title } });
    }
    if (form.dataset.form === "relate") await api("/api/context/relationships", { method: "POST", body: { ...where, target: f.target, relation: f.relation } });
    toast({ title: "Saved" });
    await renderContext(el);
    $(`form[data-form="${form.dataset.form}"] input`, el)?.focus();
  } catch (err) {
    toastError(err);
  }
}

// ---------------------------------------------------------------- helpers

/** Models of the agent picked in the search box; its cheapest first (the default). Hidden for exact-word search. */
function modelSelect() {
  const sel = document.createElement("select");
  sel.dataset.recallModel = "";
  sel.setAttribute("aria-label", "Model");
  const h = helpers?.find((x) => x.agent === pickedAgent);
  sel.hidden = !h;
  sel.innerHTML = (h?.models || []).map((m) => `<option value="${esc(m)}" ${m === (pickedModel || h.default) ? "selected" : ""}>${esc(m)}${m === h.default ? " (cheapest)" : ""}</option>`).join("");
  return sel;
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function ago(iso) {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (!Number.isFinite(s)) return "";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
