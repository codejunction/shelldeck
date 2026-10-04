// AI agents page: coding agents running in terminals, every known agent CLI with its models, and launch.
import { ATTENTION, S, attentionOf, orderedProjects, projectColor, refreshProjects, showSession } from "./app.js";
import { api, esc, icon, promptDialog, toast, toastError } from "./ui.js";

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
    el.addEventListener("change", (e) => onCommand(e, el));
  }
  const kept = keep(el);
  try {
    const [agents, skills, handoffs, ints, stored] = await Promise.all([api("/api/agents"), api("/api/skills"), api("/api/handoffs"), api("/api/integrations"), api("/api/agent-sessions")]);
    data = { ...agents, skills: Object.fromEntries(skills.skills.map((x) => [x.agent, x.installed])), handoffs: handoffs.handoffs, stored: stored.sessions, integrations: ints.integrations.filter((i) => i.installable) };
    for (const key of new Set(data.running.map((r) => r.agent))) {
      if (!(key in COMMANDS)) COMMANDS[key] = (await api(`/api/agent-commands/${key}`)).commands;
    }
  } catch (e) {
    return toastError(e);
  }
  const rank = (r) => ATTENTION.indexOf(attentionOf(r.session_id) || r.status?.state || "unknown");
  const ordered = [...data.running].sort((a, b) => rank(a) - rank(b));
  const blocked = ordered.filter((r) => attentionOf(r.session_id) === "blocked");
  const done = ordered.filter((r) => attentionOf(r.session_id) === "done");
  const banner = blocked.length || done.length
    ? `<div class="card attn-banner" role="status">${blocked.length ? `<b>${blocked.length} need${blocked.length > 1 ? "" : "s"} you</b>` : ""}${done.length ? `<span>${done.length} finished, not seen yet</span>` : ""}
        <button class="btn sm primary" data-open-next="${(blocked[0] || done[0]).session_id}">${icon("sparkle")}Open next</button></div>`
    : "";
  const running = ordered.length
    ? ordered
        .map((r) => {
          const p = S.projects.find((x) => x.id === r.project_id);
          const parent = r.parent && S.projects.flatMap((x) => x.sessions).find((x) => x.id === r.parent);
          return `<tr data-sid="${r.session_id}" title="Open terminal">
            <td><b>${esc(r.nick || "")}</b>${parent ? `<br><span class="faint">sub-agent of ${esc(parent.nick || parent.id)}</span>` : ""}</td>
            <td><span class="ai-chip">${icon("sparkle")}<b>AI</b>${esc(r.label)}</span></td>
            <td>${statusCell({ ...r.status, ...(S.serverStatus[r.session_id] || {}) }, attentionOf(r.session_id))}</td>
            <td class="mono">${esc(r.model || "") || '<span class="faint">default</span>'}</td>
            <td><span class="proj-dot" style="background:${p ? projectColor(p) : "var(--faint)"}"></span>${esc(r.project || "")}</td>
            <td>${esc(r.name || "")} <span class="faint mono">${esc(r.session_id)}</span></td>
            <td>${commandMenu(r)}</td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="7" class="faint empty-row">No agent is running. Launch one below, or run claude, codex, devin... in any terminal.</td></tr>`;

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
    ${banner}
    <div class="card mon-table"><table>
      <thead><tr><th>Name</th><th>Agent</th><th>Status</th><th>Model</th><th>Project</th><th>Terminal</th><th></th></tr></thead>
      <tbody>${running}</tbody></table></div>
    ${handoffSection()}
    ${resumeSection()}
    ${integrationSection()}
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

const COMMANDS = {}; // agent -> its native commands (/api/agent-commands)

/** The agent's own slash commands, by shelldeck action name (compact -> /compress in Gemini). */
function commandMenu(r) {
  const cmds = (COMMANDS[r.agent] || []).filter((c) => !c.takes_arg);
  if (!cmds.length) return "";
  const opts = cmds.map((c) => `<option value="${c.action}">${esc(c.action)} (${esc(c.command)})${c.verified ? "" : " ?"}</option>`).join("");
  return `<button class="btn sm" data-message="${r.session_id}" title="Send a prompt (refused while it waits on a question)">Message</button>
    <select class="sm" data-agent-cmd="${r.session_id}" aria-label="Run a ${esc(r.label)} command"><option value="">Command…</option>${opts}</select>`;
}

async function onCommand(e, el) {
  const sel = e.target.closest("[data-agent-cmd]");
  if (!sel || !sel.value) return;
  const command = sel.value;
  sel.value = "";
  try {
    const r = await api(`/api/sessions/${sel.dataset.agentCmd}/agent-command`, { method: "POST", body: { command } });
    toast({ title: `Sent ${r.typed}`, body: `to ${r.agent}` });
  } catch (err) {
    const why = { agent_blocked: "It is waiting on a question: answer that first.", agent_working: "It is busy: wait until it finishes (or use sd agent cmd --force)." };
    const code = Object.keys(why).find((k) => String(err?.message || err).includes(k));
    if (code) toast({ title: "Command not sent", body: why[code], kind: "warn" });
    else toastError(err);
  }
}

async function onClick(e, el) {
  if (e.target.closest("select")) return; // the command menu, not "open terminal"
  const msgTo = e.target.closest("[data-message]")?.dataset.message;
  if (msgTo) {
    const text = await promptDialog("Message the agent", "", { label: "Prompt", ok: "Send" });
    if (!text?.trim()) return;
    try {
      await api("/api/agent-prompt", { method: "POST", body: { session_id: msgTo, text } });
      toast({ title: "Sent" });
    } catch (err) {
      if (String(err?.message || err).includes("agent_blocked")) toast({ title: "Not sent", body: "It is waiting on a question: answer that first.", kind: "warn" });
      else toastError(err);
    }
    return;
  }
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
  const next = e.target.closest("[data-open-next]")?.dataset.openNext;
  if (next) return showSession(next);
  const again = e.target.closest("[data-resume-agent]")?.dataset.resumeAgent;
  if (again) {
    try {
      await api(`/api/sessions/${again}/resume`, { method: "POST" });
      showSession(again);
      toast({ title: "Agent resumed" });
      return renderAgents(el);
    } catch (err) {
      return toastError(err);
    }
  }
  const integ = e.target.closest("[data-integration]");
  if (integ) {
    const { integration: agent, op } = integ.dataset;
    try {
      const r = await api(`/api/integrations/${agent}`, { method: op === "remove" ? "DELETE" : "POST" });
      toast({ title: op === "remove" ? "Integration removed" : "Integration installed", body: r.file + (agent === "opencode" && op !== "remove" ? "\nRestart OpenCode to load it." : "") });
      return renderAgents(el);
    } catch (err) {
      return toastError(err);
    }
  }
  const resume = e.target.closest("[data-resume]")?.dataset.resume;
  if (resume) return resumeDevin(data.devin_sessions.find((d) => d.id === resume));
  const key = e.target.closest("[data-launch]")?.dataset.launch;
  if (!key) return;
  const model = e.target.closest(".ag-card").querySelector("[data-model]")?.value;
  // the server builds the line with the agent's own flags (model, per-run hooks such as Claude's --settings)
  try {
    const r = await api("/api/agent-start", { method: "POST", body: { agent: key, project_id: el.querySelector("[data-project]")?.value, model } });
    await refreshProjects();
    const term = await showSession(r.session.id);
    term?.focus();
  } catch (err) {
    toastError(err);
  }
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
function statusCell(st, attention) {
  if (!st) return '<span class="faint">unknown</span>';
  // the screen can see a question the server can't (tab-side heuristic); "done" stays until you look
  const label = attention === "blocked" ? "needs you" : attention === "done" ? "done (not seen)" : st.meta?.state_label || st.state;
  const since = st.since ? ` · ${ago(st.since)}` : "";
  const src = st.source === "heuristic" ? "screen" : st.source;
  const why = st.reason ? ` · ${st.reason}` : "";
  return `<b>${esc(label)}</b>${esc(why)}<br><span class="faint">from ${esc(src)}${since}</span>`;
}

function ago(t) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** Lifecycle integrations (hooks/plugins that report state), not to be confused with the skill (instructions). */
function integrationSection() {
  const all = data.integrations || [];
  if (!all.length) return "";
  const label = { installed: "installed", outdated: "outdated: reinstall", not_installed: "not installed", error: "config unreadable" };
  const card = (i) => {
      const on = i.status === "installed" || i.status === "outdated";
      const btn = on
        ? `${i.status === "outdated" ? `<button class="btn sm" data-integration="${i.agent}" data-op="install">Reinstall</button>` : ""}<button class="btn sm" data-integration="${i.agent}" data-op="remove">Remove</button>`
        : `<button class="btn sm" data-integration="${i.agent}" data-op="install"${i.config_found ? "" : ' disabled title="Install the agent first (its config folder was not found)"'}>Install</button>`;
      return `<div class="card ag-card"><div class="ag-title"><b>${esc(i.agent)}</b><code class="faint">${esc(i.kind)}</code></div>
        <p class="faint">${esc(label[i.status] || i.status)}${i.lifecycle ? " · exact state" : " · session id only"}${i.session_restore ? " · resumable" : ""}${i.available ? "" : " · CLI not found"}</p><div class="row">${btn}</div></div>`;
  };
  const main = all.filter((i) => i.tier === "priority").map(card).join("");
  const later = all.filter((i) => i.tier !== "priority");
  return `<div class="ag-head"><h2>Integrations</h2><span class="faint">Hooks that report working / needs you / done exactly, instead of reading the screen. The skill only teaches commands.</span></div>
    <div class="ag-grid">${main}</div>
    ${later.length ? `<details class="ag-later"><summary class="faint">Preview: ${later.length} more agents (not fully supported yet)</summary><div class="ag-grid">${later.map(card).join("")}</div></details>` : ""}`;
}

const RESUME_WHY = { executable_not_found: "agent CLI not found", cwd_missing: "folder is gone", invalid_resume_argv: "stored command rejected", no_resume: "nothing to resume", agent_running: "running" };

/** Stored agent sessions (from integrations or the agent's own logs) whose terminal has no agent now. */
function resumeSection() {
  const sessions = S.projects.flatMap((p) => p.sessions.map((s) => ({ ...s, project: p })));
  const rows = (data.stored || []).filter((r) => !r.running && sessions.some((s) => s.id === r.session_id));
  if (!rows.length) return "";
  const body = rows
    .map((r) => {
      const s = sessions.find((x) => x.id === r.session_id);
      const action = r.can_resume ? `<button class="btn sm" data-resume-agent="${r.session_id}">Resume</button>` : `<span class="faint">${esc(RESUME_WHY[r.error] || r.error || "")}</span>`;
      return `<tr><td><b>${esc(s.nick || "")}</b></td><td>${esc(r.agent)}</td><td>${esc(r.last_state)}</td><td>${esc(s.project.name)}</td><td>${action}</td></tr>`;
    })
    .join("");
  return `<div class="ag-head"><h2>Resumable sessions</h2><span class="faint">Agent conversations shelldeck can start again in their terminal (Settings: resume ask / auto / never).</span></div>
    <div class="card mon-table"><table><thead><tr><th>Name</th><th>Agent</th><th>Last state</th><th>Project</th><th></th></tr></thead><tbody>${body}</tbody></table></div>`;
}
