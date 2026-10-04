// AI agents page: coding agents running in terminals, every known agent CLI with its models, and launch.
import { S, newTerminal, orderedProjects, projectColor, refreshProjects, showSession } from "./app.js";
import { api, esc, icon, toast, toastError } from "./ui.js";

// pasted into an agent so it knows how to reach the others in its project
const TEAM_PROMPT = `You are one of several AI coding agents working in this project, each in its own shelldeck terminal with a person's name ($SHELLDECK_NICK).
- \`sd agents\` lists the other agents here (name, tool, model, folder).
- \`sd peek <name> -n 60\` shows another agent's screen, to check its progress.
- \`sd tell <name> "message"\` types a message into another agent's terminal; it arrives with your name so it can answer.
- \`sd spawn "task" --model small|medium|large\` starts a sub-agent on a task; \`sd handoff <name> "task"\` gives one to a running agent; \`sd done <id> "summary"\` closes a hand-off you got.
Split the work, say what you are taking before you start, avoid editing the same files, and finish hand-offs with sd done.`;

let data = null;

export async function renderAgents(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.addEventListener("click", (e) => onClick(e, el));
  }
  const kept = keep(el);
  try {
    const [agents, skills, handoffs] = await Promise.all([api("/api/agents"), api("/api/skills"), api("/api/handoffs")]);
    data = { ...agents, skills: Object.fromEntries(skills.skills.map((x) => [x.agent, x.installed])), handoffs: handoffs.handoffs };
  } catch (e) {
    return toastError(e);
  }
  const running = data.running.length
    ? data.running
        .map((r) => {
          const p = S.projects.find((x) => x.id === r.project_id);
          const parent = r.parent && S.projects.flatMap((x) => x.sessions).find((x) => x.id === r.parent);
          return `<tr data-sid="${r.session_id}" title="Open terminal">
            <td><b>${esc(r.nick || "")}</b>${parent ? `<br><span class="faint">sub-agent of ${esc(parent.nick || parent.id)}</span>` : ""}</td>
            <td><span class="ai-chip">${icon("sparkle")}<b>AI</b>${esc(r.label)}</span></td>
            <td>${statusCell(r.status)}</td>
            <td class="mono">${esc(r.model || "") || '<span class="faint">default</span>'}</td>
            <td><span class="proj-dot" style="background:${p ? projectColor(p) : "var(--faint)"}"></span>${esc(r.project || "")}</td>
            <td>${esc(r.name || "")} <span class="faint mono">${esc(r.session_id)}</span></td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="6" class="faint empty-row">No agent is running. Launch one below, or run claude, codex, devin... in any terminal.</td></tr>`;

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
      const skill = a.key in data.skills
        ? data.skills[a.key]
          ? `<span class="ag-skill on" title="It knows sd agents, peek, tell, spawn, handoff and done">skill installed</span>`
          : `<button class="btn sm" data-skill="${a.key}" title="Teach it the sd team commands">Install skill</button>`
        : "";
      return `<div class="card ag-card ${a.installed ? "" : "missing"}" data-key="${a.key}">
        <div class="ag-title">${icon("sparkle")}<b>${esc(a.label)}</b><code class="faint">${esc(a.command)}</code>${a.installed ? skill : ""}</div>
        ${a.default_model ? `<p class="muted">Configured model: <code>${esc(a.default_model)}</code></p>` : ""}
        ${models}${launch}
      </div>`;
    })
    .join("");

  el.innerHTML = `<div class="page-head"><div><h1>AI agents</h1>
      <p>Coding agents running in your terminals are marked AI in their header. Each terminal has a name; agents in a project can see, message and hand work to each other with <code>sd agents</code>, <code>peek</code>, <code>tell</code>, <code>spawn</code>, <code>handoff</code> and <code>done</code>.</p></div>
      <button class="btn" data-copy-prompt>${icon("clipboard")}Copy team prompt</button></div>
    <div class="card mon-table"><table>
      <thead><tr><th>Name</th><th>Agent</th><th>Status</th><th>Model</th><th>Project</th><th>Terminal</th></tr></thead>
      <tbody>${running}</tbody></table></div>
    ${handoffSection()}
    <div class="ag-head"><h2>Available agents</h2>
      ${projects.length ? `<label class="muted">Launch in <select data-project>${projectOpts}</select></label>` : `<span class="faint">Add a project to launch agents.</span>`}</div>
    <div class="ag-grid">${cards}</div>
    ${outsideSection()}
    ${devinSection()}`;
  restore(el, kept);
}

/** Tasks handed between terminals (sd spawn / handoff / done), newest first. */
function handoffSection() {
  if (!data.handoffs?.length) return "";
  const rows = data.handoffs
    .slice(0, 30)
    .map((h) => {
      const p = S.projects.find((x) => x.id === h.project_id);
      return `<tr ${h.to_sid && S.projects.some((x) => x.sessions.some((s) => s.id === h.to_sid)) ? `data-sid="${h.to_sid}" title="Open ${esc(h.to_nick)}'s terminal"` : ""}>
        <td class="mono">${esc(h.id)}</td>
        <td><span class="ho-status" data-status="${esc(h.status)}">${esc(h.status)}</span></td>
        <td>${esc(h.from_nick || "you")} → <b>${esc(h.to_nick || "")}</b><br><span class="faint">${esc(p?.name || "")}${h.model ? ` · ${esc(h.model)}` : ""}</span></td>
        <td class="ag-clip" title="${esc(h.task)}">${esc(h.task)}</td>
        <td class="ag-clip" title="${esc(h.result || "")}">${esc(h.result || "") || '<span class="faint">pending</span>'}</td>
      </tr>`;
    })
    .join("");
  return `<div class="ag-head"><h2>Hand-offs</h2><span class="faint">Also in each project's .shelldeck/handoff.md</span></div>
    <div class="card mon-table"><table><thead><tr><th>ID</th><th>Status</th><th>From → to</th><th>Task</th><th>Result</th></tr></thead><tbody>${rows}</tbody></table></div>`;
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
  const skill = e.target.closest("[data-skill]")?.dataset.skill;
  if (skill) {
    try {
      const { results } = await api("/api/skills", { method: "POST", body: { agents: [skill] } });
      toast({ title: "Skill installed", body: results.map((r) => r.path).join("\n") });
      return renderAgents(el);
    } catch (err) {
      return toastError(err);
    }
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

/** Lifecycle state as text (never colour alone) plus where it came from: integration, screen/activity, unknown. */
function statusCell(st) {
  if (!st) return '<span class="faint">unknown</span>';
  const label = st.meta?.state_label || st.state;
  const src = st.source === "heuristic" ? "screen" : st.source;
  const why = st.reason ? ` · ${st.reason}` : "";
  return `<b>${esc(label)}</b>${esc(why)}<br><span class="faint">from ${esc(src)}</span>`;
}
