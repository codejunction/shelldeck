// AI agents page: coding agents running in terminals, every known agent CLI with its models, and launch.
import { S, newTerminal, orderedProjects, projectColor, refreshProjects, showSession } from "./app.js";
import { api, esc, icon, toast, toastError } from "./ui.js";

// pasted into an agent so it knows how to reach the others in its project
const TEAM_PROMPT = `You are one of several AI coding agents working in this project, each in its own shelldeck terminal.
- \`sd agents\` lists the other agents here (terminal id, tool, model, folder).
- \`sd peek <id> -n 60\` shows the last lines of another agent's terminal, to check its progress.
- \`sd tell <id> "message"\` types a message into another agent's terminal; it arrives with your id so it can answer.
Split the work, say what you are taking before you start, avoid editing the same files, and report back with sd tell when you finish.`;

let data = null;

export async function renderAgents(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.addEventListener("click", (e) => onClick(e, el));
  }
  const kept = keep(el);
  try {
    data = await api("/api/agents");
  } catch (e) {
    return toastError(e);
  }
  const running = data.running.length
    ? data.running
        .map((r) => {
          const p = S.projects.find((x) => x.id === r.project_id);
          return `<tr data-sid="${r.session_id}" title="Open terminal">
            <td><span class="ai-chip">${icon("sparkle")}<b>AI</b>${esc(r.label)}</span></td>
            <td class="mono">${esc(r.model || "") || '<span class="faint">default</span>'}</td>
            <td><span class="proj-dot" style="background:${p ? projectColor(p) : "var(--faint)"}"></span>${esc(r.project || "")}</td>
            <td>${esc(r.name || "")} <span class="faint mono">${esc(r.session_id)}</span></td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="4" class="faint empty-row">No agent is running. Launch one below, or run claude, codex, devin... in any terminal.</td></tr>`;

  const projects = orderedProjects();
  const current = S.focused && projects.find((p) => p.sessions.some((s) => s.id === S.focused));
  const projectOpts = projects.map((p) => `<option value="${p.id}" ${p === current ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const known = [...data.agents].sort((a, b) => b.installed - a.installed);
  const cards = known
    .map((a) => {
      const models = a.models.length
        ? `<details class="ag-models"><summary>${a.models.length} model${a.models.length > 1 ? "s" : ""}</summary><div>${a.models.map((m) => `<code>${esc(m)}</code>`).join("")}</div></details>`
        : `<p class="faint">Models: whatever the tool is set up with.</p>`;
      const launch = a.installed
        ? `<div class="ag-launch">
            ${a.models.length ? `<select data-model aria-label="Model"><option value="">Default model${a.default_model ? ` (${esc(a.default_model)})` : ""}</option>${a.models.map((m) => `<option>${esc(m)}</option>`).join("")}</select>` : ""}
            <button class="btn primary sm" data-launch="${a.key}" ${projects.length ? "" : "disabled"}>${icon("play")}Launch</button>
          </div>`
        : `<p class="faint">Not installed (<code>${esc(a.command)}</code> is not on PATH).</p>`;
      return `<div class="card ag-card ${a.installed ? "" : "missing"}" data-key="${a.key}">
        <div class="ag-title">${icon("sparkle")}<b>${esc(a.label)}</b><code class="faint">${esc(a.command)}</code></div>
        ${a.default_model ? `<p class="muted">Configured model: <code>${esc(a.default_model)}</code></p>` : ""}
        ${models}${launch}
      </div>`;
    })
    .join("");

  el.innerHTML = `<div class="page-head"><div><h1>AI agents</h1>
      <p>Coding agents running in your terminals are marked AI in their header. Agents in the same project can see and message each other with <code>sd agents</code>, <code>sd peek</code> and <code>sd tell</code>.</p></div>
      <button class="btn" data-copy-prompt>${icon("clipboard")}Copy team prompt</button></div>
    <div class="card mon-table"><table>
      <thead><tr><th>Agent</th><th>Model</th><th>Project</th><th>Terminal</th></tr></thead>
      <tbody>${running}</tbody></table></div>
    <div class="ag-head"><h2>Available agents</h2>
      ${projects.length ? `<label class="muted">Launch in <select data-project>${projectOpts}</select></label>` : `<span class="faint">Add a project to launch agents.</span>`}</div>
    <div class="ag-grid">${cards}</div>
    ${outsideSection()}
    ${devinSection()}`;
  restore(el, kept);
}

/** Agents found elsewhere on this machine: desktop apps, other terminal windows. */
function outsideSection() {
  if (!data.outside?.length) return "";
  const rows = data.outside
    .map((a) => `<tr>
      <td><span class="ai-chip">${icon("sparkle")}<b>AI</b>${esc(a.label)}</span></td>
      <td class="mono">${esc(a.model || "") || '<span class="faint">default</span>'}</td>
      <td class="mono">${esc(a.cwd || "")}</td>
      <td class="faint">${esc(a.host || "")} <span class="mono">pid ${a.pid}</span></td>
    </tr>`)
    .join("");
  return `<div class="ag-head"><h2>Running outside shelldeck</h2></div>
    <div class="card mon-table"><table><thead><tr><th>Agent</th><th>Model</th><th>Folder</th><th>Started by</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

const SESSION_ID = /^[\w.-]+$/;

/** Every Devin session (CLI, or hosted by an app), resumable in a terminal in its folder. */
function devinSection() {
  if (!data.devin_sessions?.length) return "";
  const rows = data.devin_sessions
    .map((d) => `<tr>
      <td class="ag-clip" title="${esc(d.title || "")}">${esc(d.title || "Untitled")}<br><span class="faint mono">${esc(d.id)}</span></td>
      <td class="mono">${esc(d.model || "")}</td>
      <td class="mono">${esc(d.cwd || "")}</td>
      <td class="faint">${esc(d.backend || "")}</td>
      <td class="faint">${new Date(d.last_activity_at * 1000).toLocaleString()}</td>
      <td>${SESSION_ID.test(d.id) ? `<button class="btn sm" data-resume="${esc(d.id)}">${icon("play")}Resume</button>` : ""}</td>
    </tr>`)
    .join("");
  return `<div class="ag-head"><h2>Devin sessions</h2><span class="faint">From Devin's own session store, newest first</span></div>
    <div class="card mon-table"><table><thead><tr><th>Session</th><th>Model</th><th>Folder</th><th>Backend</th><th>Last active</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// the page re-renders every 5s: keep picked models, the project and open model lists
function keep(el) {
  return {
    project: el.querySelector("[data-project]")?.value,
    models: Object.fromEntries([...el.querySelectorAll(".ag-card")].map((c) => [c.dataset.key, c.querySelector("[data-model]")?.value])),
    open: new Set([...el.querySelectorAll(".ag-card details[open]")].map((d) => d.closest(".ag-card").dataset.key)),
  };
}

function restore(el, k) {
  const project = el.querySelector("[data-project]");
  if (project && k.project && [...project.options].some((o) => o.value === k.project)) project.value = k.project;
  for (const c of el.querySelectorAll(".ag-card")) {
    const sel = c.querySelector("[data-model]");
    if (sel && k.models[c.dataset.key]) sel.value = k.models[c.dataset.key];
    const det = c.querySelector("details");
    if (det && k.open.has(c.dataset.key)) det.open = true;
  }
}

async function onClick(e, el) {
  const sid = e.target.closest("tr[data-sid]")?.dataset.sid;
  if (sid) return showSession(sid);
  if (e.target.closest("[data-copy-prompt]")) {
    await navigator.clipboard.writeText(TEAM_PROMPT).catch(() => {});
    return toast({ title: "Team prompt copied", body: "Paste it into each agent." });
  }
  const resume = e.target.closest("[data-resume]")?.dataset.resume;
  if (resume) return resumeDevin(data.devin_sessions.find((d) => d.id === resume));
  const key = e.target.closest("[data-launch]")?.dataset.launch;
  if (!key) return;
  const agent = data.agents.find((a) => a.key === key);
  const model = e.target.closest(".ag-card").querySelector("[data-model]")?.value;
  // every listed CLI takes --model; quote it for cmd/pwsh/bash alike only when needed
  const command = agent.command + (model ? ` --model ${/^[\w.:/@-]+$/.test(model) ? model : `"${model}"`}` : "");
  const term = await newTerminal(el.querySelector("[data-project]")?.value);
  if (!term) return;
  await term.ready();
  term.send({ type: "input", data: `${command}\r` });
  term.focus();
}

/** Open a terminal in the session's folder (added as a project if new) and run `devin -r <id>`. */
async function resumeDevin(d) {
  if (!d || !SESSION_ID.test(d.id)) return;
  try {
    const s = await api("/api/sessions", { method: "POST", body: { cwd: d.cwd } });
    await refreshProjects();
    const term = await showSession(s.id);
    await term.ready();
    term.send({ type: "input", data: `devin -r ${d.id}\r` });
    term.focus();
  } catch (e) {
    toastError(e);
  }
}
