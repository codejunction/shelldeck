// AI agents page: coding agents running in terminals, every known agent CLI with its models, and launch.
import { ATTENTION, S, attentionOf, machineProjects, projectColor, refreshProjects, sessionOnMachine, showSession } from "./app.js";
import { $, api, dialog, esc, icon, promptDialog, store, toast, toastError } from "./ui.js";

// pasted into an agent so it knows how to reach the others in its project
const TEAM_PROMPT = `You are one of several AI coding agents working in this project, each in its own shelldeck terminal with a person's name ($SHELLDECK_NICK).
- \`sd agents\` lists the other agents here (name, tool, model, folder).
- \`sd peek <name> -n 60\` shows another agent's screen, to check its progress.
- \`sd tell <name> "message"\` types a message into another agent's terminal; it arrives with your name so it can answer.
- \`sd spawn "task" --model small|medium|large\` starts a sub-agent on a task; \`sd handoff <name> "task"\` gives one to a running agent; \`sd done <id> "summary"\` closes a hand-off you got.
Split the work, say what you are taking before you start, avoid editing the same files, and finish hand-offs with sd done.`;

let data = null;
// one thing at a time: what runs, who handed what to whom, starting an agent, one-time setup
const TABS = [["running", "Running"], ["handoffs", "Hand-offs"], ["launch", "Launch"], ["setup", "Setup"]];
let tab = store.get("agentsTab", "running");

export async function renderAgents(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.addEventListener("click", (e) => onClick(e, el));
    el.addEventListener("change", (e) => onCommand(e, el));
    el.addEventListener("input", (e) => {
      const key = e.target.dataset?.tq;
      if (!key) return;
      Object.assign(tables[key], { q: e.target.value, limit: PAGE });
      redrawTable(el, key, 0);
    });
    // lazy loading: scrolling near the end of a history table shows the next page
    el.addEventListener("scroll", (e) => {
      const box = e.target.closest?.("[data-tbl]");
      if (box && box.scrollTop + box.clientHeight > box.scrollHeight - 40 && box.querySelector("[data-more]")) moreRows(el, box.dataset.tbl);
    }, true);
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
  // the machine picked in the sidebar: its terminals' agents only
  const ordered = data.running.filter((r) => sessionOnMachine(r.session_id)).sort((a, b) => rank(a) - rank(b));
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
    : S.machine === "local"
      ? `<tr><td colspan="7" class="faint empty-row">No agent is running. Launch one below, or run claude, codex, devin... in any terminal.</td></tr>`
      : `<tr><td colspan="7" class="faint empty-row">No agent detected on this machine. Agents running inside an SSH session aren't detected yet (process detection only sees this computer).</td></tr>`;

  const projects = machineProjects().filter((p) => !p.remote_id); // launching is local-only for now
  const current = S.focused && projects.find((p) => p.sessions.some((s) => s.id === S.focused));
  const projectOpts = projects.map((p) => `<option value="${p.id}" ${p === current ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const known = [...data.agents].sort((a, b) => b.installed - a.installed);
  const card = (a) => {
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
        ${launch}
      </div>`;
  };
  const missing = known.filter((a) => !a.installed);

  if (!TABS.some(([k]) => k === tab)) tab = "running";
  const openHandoffs = (data.handoffs || []).filter((h) => h.status === "open" && machineProjects().some((p) => p.id === h.project_id)).length;
  const counts = { running: ordered.length, handoffs: openHandoffs };
  const panes = {
    running: () => `<div class="card mon-table"><table>
        <thead><tr><th>Name</th><th>Agent</th><th>Status</th><th>Model</th><th>Project</th><th>Terminal</th><th></th></tr></thead>
        <tbody>${running}</tbody></table></div>
      ${resumeSection()}${outsideSection()}`,
    handoffs: () => handoffSection() || `<div class="card ag-empty"><b>No hand-offs yet</b>
        <p class="faint">Agents hand work to each other with <code>sd spawn</code> and <code>sd handoff</code>. You can too: Running, then Actions, then Hand off a task.</p></div>`,
    launch: () => `<div class="ag-head"><h2>Start an agent</h2>
        ${projects.length ? `<label class="muted">in <select data-project>${projectOpts}</select></label>` : `<span class="faint">Add a project to launch agents.</span>`}</div>
      <div class="ag-grid">${known.filter((a) => a.installed).map(card).join("") || `<p class="faint">No agent CLI found on PATH.</p>`}</div>
      ${missing.length ? `<details class="ag-later"><summary class="faint">${missing.length} more not installed</summary><div class="ag-grid">${missing.map(card).join("")}</div></details>` : ""}
      ${devinSection()}`,
    setup: () => `<div class="card ag-intro"><p>Each terminal has a name. Agents in a project see, message and hand work to each other with <code>sd agents</code>, <code>peek</code>, <code>tell</code>, <code>spawn</code>, <code>handoff</code> and <code>done</code>; the skill (Launch tab) teaches them.</p>
        <button class="btn" data-copy-prompt>${icon("clipboard")}Copy team prompt</button></div>
      ${integrationSection()}`,
  };
  el.innerHTML = `<div class="page-head ag-top"><h1>AI agents</h1>
      <div class="seg ag-tabs" role="tablist">${TABS.map(([k, label]) => `<button role="tab" data-tab="${k}" aria-selected="${k === tab}" class="${k === tab ? "on" : ""}">${label}${counts[k] ? `<span>${counts[k]}</span>` : ""}</button>`).join("")}</div></div>
    ${banner}
    ${panes[tab]()}`;
  restore(el, kept);
}

/** Tasks handed between terminals (sd spawn / handoff / done), newest first. */
function handoffSection() {
  // the picked machine and workspace, like the agents above
  const shown = new Set(machineProjects().map((p) => p.id));
  const all = (data.handoffs || []).filter((h) => shown.has(h.project_id));
  if (!all.length) return "";
  const count = (s) => all.filter((h) => h.status === s).length;
  // a hand-off hangs under the task its sender was given (the newest earlier hand-off to that terminal): a chain.
  // The rest start at their sender, grouped per project. `all` is newest first.
  const parentOf = (h) => h.from_sid && all.find((x) => x.to_sid === h.from_sid && x.id !== h.id && x.created_at <= h.created_at);
  const kids = new Map();
  const groups = new Map();
  for (const h of all) {
    const up = parentOf(h);
    const [map, key] = up ? [kids, up.id] : [groups, `${h.project_id} ${h.from_sid || ""}`];
    map.set(key, [...(map.get(key) || []), h]);
  }
  const byAge = (x, y) => x.created_at.localeCompare(y.created_at); // drawn in the order the work was given
  const branch = (h) => `<div class="ho-branch">${handoffCard(h)}${kids.has(h.id) ? `<div class="ho-kids">${kids.get(h.id).sort(byAge).map(branch).join("")}</div>` : ""}</div>`;
  // groups with open work first; closed history folds to HO_RECENT per sender until "Show all"
  const open = (list) => list.some((h) => h.status === "open");
  const html = [...groups.values()].sort((a, b) => open(b) - open(a)).map((list) => {
    const first = list[0];
    const p = S.projects.find((x) => x.id === first.project_id);
    const visible = hoAll ? list : [...list.filter((h) => h.status === "open"), ...list.filter((h) => h.status !== "open").slice(0, HO_RECENT)];
    const who = first.from_nick || "You";
    return `<div class="ho-group">
      <div class="ho-node ${first.from_sid ? "" : "me"}"><span class="ho-avatar" style="${p && first.from_sid ? `background:${projectColor(p)}` : ""}">${esc(who[0].toUpperCase())}</span>
        <div><b>${esc(who)}</b><span class="faint">${esc(p?.name || "")}</span><span class="faint">${list.length} hand-off${list.length === 1 ? "" : "s"}</span></div></div>
      <div class="ho-kids">${visible.sort(byAge).map(branch).join("")}${list.length > visible.length ? `<div class="ho-branch"><button class="btn sm ho-older" data-ho-all>${list.length - visible.length} older…</button></div>` : ""}</div>
    </div>`;
  }).join("");
  return `<div class="ag-head"><h2>Hand-offs</h2>
      <span class="faint tbl-sub">Who gave what to whom. Click a card for the whole task and result; also in each project's .shelldeck/handoff.md</span>
      <span class="ho-counts">${["open", "done", "failed", "exited", "cancelled"].filter(count).map((s) => `<span class="ho-status" data-status="${s}">${count(s)} ${s}</span>`).join("")}
        ${hoAll ? `<button class="btn sm" data-ho-all>Show recent</button>` : ""}</span></div>
    <div class="ho-flow">${html}</div>`;
}

const HO_RECENT = 3;
let hoAll = false; // show every closed hand-off, not just the latest per sender
const HO_WORD = { open: "working", done: "done", failed: "failed", exited: "terminal closed", cancelled: "cancelled" };

function handoffCard(h) {
  const label = data.agents.find((a) => a.key === h.agent)?.label || h.agent || "";
  return `<button type="button" class="ho-card" data-ho="${esc(h.id)}" data-status="${esc(h.status)}" aria-label="Hand-off ${esc(h.id)} to ${esc(h.to_nick || "")}: ${esc(HO_WORD[h.status] || h.status)}">
    <span class="ho-top"><span class="ho-status" data-status="${esc(h.status)}">${esc(HO_WORD[h.status] || h.status)}</span>
      <span class="faint mono">${esc(h.id)}</span><span class="faint ho-when">${ago(Date.parse(h.updated_at) / 1000)} ago</span></span>
    <span class="ho-to"><span class="ho-arrow" aria-hidden="true">→</span><b>${esc(h.to_nick || "")}</b>${label ? `<span class="ai-chip">${icon("sparkle")}${esc(label)}</span>` : ""}${h.model ? `<span class="faint mono">${esc(h.model)}</span>` : ""}</span>
    <span class="ho-task">${esc(h.task)}</span>
    ${h.result ? `<span class="ho-result">${esc(h.result)}</span>` : ""}
  </button>`;
}

/** One hand-off in full: the task, the result, its terminal, and Cancel while it is open. */
function handoffDialog(h, el) {
  const p = S.projects.find((x) => x.id === h.project_id);
  const alive = S.projects.some((x) => x.sessions.some((s) => s.id === h.to_sid));
  const d = dialog({
    title: `Hand-off ${h.id}`,
    wide: true,
    body: `<p><span class="ho-status" data-status="${esc(h.status)}">${esc(h.status)}</span>
        ${esc(h.from_nick || "you")} → <b>${esc(h.to_nick || "")}</b> <span class="faint">· ${esc(p?.name || "")}${h.model ? ` · ${esc(h.model)}` : ""} · ${esc(new Date(h.created_at).toLocaleString())}</span></p>
      <h3>Task</h3><div class="ho-text">${esc(h.task)}</div>
      <h3>Result</h3><div class="ho-text">${esc(h.result || "") || '<span class="faint">Pending: the agent closes it with sd done.</span>'}</div>
      ${p ? `<p class="faint mono">${esc(p.path)}/.shelldeck/handoffs/${esc(h.id)}.md</p>` : ""}`,
    foot: `${h.status === "open" ? `<button class="btn danger" data-ho-cancel>Cancel hand-off</button>` : ""}
      ${alive ? `<button class="btn" data-ho-open>Open ${esc(h.to_nick || "terminal")}</button>` : ""}<button class="btn primary" data-close>Close</button>`,
  });
  $("[data-ho-open]", d.el)?.addEventListener("click", () => {
    d.close();
    showSession(h.to_sid);
  });
  $("[data-ho-cancel]", d.el)?.addEventListener("click", async () => {
    try {
      await api(`/api/handoffs/${h.id}/done`, { method: "POST", body: { cancelled: true } });
      d.close();
      renderAgents(el);
    } catch (err) {
      toastError(err);
    }
  });
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
  return pagedTable("devin", {
    title: "Devin sessions",
    sub: "From Devin's own session store, newest first",
    cols: ["Session", "Model", "Folder", "Backend", "Last active", ""],
    items: data.devin_sessions,
    text: (d) => [d.title, d.id, d.model, d.cwd, d.backend].join(" "),
    row: (d) => `<tr>
      <td class="ag-clip" title="${esc(d.title || "")}">${esc(d.title || "Untitled")}<br><span class="faint mono">${esc(d.id)}</span></td>
      <td class="mono">${esc(d.model || "")}</td>
      <td class="mono">${esc(d.cwd || "")}</td>
      <td class="faint">${esc(d.backend || "")}</td>
      <td class="faint">${new Date(d.last_activity_at * 1000).toLocaleString()}</td>
      <td>${SESSION_ID.test(d.id) ? `<button class="btn sm" data-resume="${esc(d.id)}">${icon("play")}Resume</button>` : ""}</td>
    </tr>`,
  });
}

// the page re-renders every 5s: keep picked models, the project and open model lists
function keep(el) {
  return {
    project: el.querySelector("[data-project]")?.value,
    models: Object.fromEntries([...el.querySelectorAll(".ag-card")].map((c) => [c.dataset.key, c.querySelector("[data-model]")?.value])),
    open: new Set([...el.querySelectorAll(".ag-card details[open]")].map((d) => d.closest(".ag-card").dataset.key)),
    search: el.contains(document.activeElement) && document.activeElement.dataset.tq ? [document.activeElement.dataset.tq, document.activeElement.selectionStart] : null,
    scroll: Object.fromEntries([...el.querySelectorAll("[data-tbl]")].map((b) => [b.dataset.tbl, b.scrollTop])),
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
  for (const b of el.querySelectorAll("[data-tbl]")) b.scrollTop = k.scroll[b.dataset.tbl] || 0;
  const input = k.search && el.querySelector(`[data-tq="${k.search[0]}"]`);
  if (input) {
    input.focus();
    input.setSelectionRange(k.search[1], k.search[1]);
  }
}

const COMMANDS = {}; // agent -> its native commands (/api/agent-commands)

/** The agent's own slash commands, by shelldeck action name (compact -> /compress in Gemini). */
function commandMenu(r) {
  const cmds = (COMMANDS[r.agent] || []).filter((c) => !c.takes_arg);
  const opts = cmds.map((c) => `<option value="cmd:${c.action}">${esc(c.action)} (${esc(c.command)})</option>`).join("");
  return `<select class="sm ag-actions" data-agent-cmd="${r.session_id}" aria-label="Actions for ${esc(r.nick || r.label)}">
    <option value="">Actions…</option>
    <option value="extract">Extract facts</option>
    <option value="message">Message…</option>
    <option value="handoff">Hand off a task…</option>
    ${opts ? `<optgroup label="${esc(r.label)} commands">${opts}</optgroup>` : ""}</select>`;
}

const BUSY = { agent_blocked: "It is waiting on a question: answer that first.", agent_working: "It is busy: try when it finishes (or use sd agent cmd --force)." };

function busyToast(err, title) {
  const code = Object.keys(BUSY).find((k) => String(err?.message || err).includes(k));
  if (code) toast({ title, body: BUSY[code], kind: "warn" });
  else toastError(err);
}

/** The row's Actions menu: extract facts, message, or one of the agent's own slash commands. */
async function onCommand(e, el) {
  const sel = e.target.closest("[data-agent-cmd]");
  if (!sel || !sel.value) return;
  const sid = sel.dataset.agentCmd;
  const choice = sel.value;
  sel.value = "";
  try {
    if (choice === "extract") {
      await api(`/api/sessions/${sid}/extract`, { method: "POST" });
      return toast({ title: "Asked it to save its facts", body: "They appear on the Context page." });
    }
    if (choice === "message") {
      const text = await promptDialog("Message the agent", "", { label: "Prompt", ok: "Send" });
      if (!text?.trim()) return;
      await api("/api/agent-prompt", { method: "POST", body: { session_id: sid, text } });
      return toast({ title: "Sent" });
    }
    if (choice === "handoff") {
      // tracked, unlike a message: it gets a task file, a status here, and the agent closes it with sd done
      const task = await promptDialog("Hand off a task", "", { label: "Task (the agent reports back with sd done)", ok: "Hand off" });
      if (!task?.trim()) return;
      const h = await api("/api/handoffs", { method: "POST", body: { to: sid, task } });
      toast({ title: `Hand-off ${h.id} sent to ${h.to_nick}` });
      return renderAgents(el);
    }
    const r = await api(`/api/sessions/${sid}/agent-command`, { method: "POST", body: { command: choice.slice(4) } });
    toast({ title: `Sent ${r.typed}`, body: `to ${r.agent}` });
  } catch (err) {
    busyToast(err, "Not sent");
  }
}

async function onClick(e, el) {
  if (e.target.closest("select")) return; // the command menu, not "open terminal"
  const picked = e.target.closest("[data-tab]")?.dataset.tab;
  if (picked) {
    store.set("agentsTab", (tab = picked));
    return renderAgents(el);
  }
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
  if (e.target.closest("[data-ho-all]")) {
    hoAll = !hoAll;
    return renderAgents(el);
  }
  const ho = e.target.closest("[data-ho]")?.dataset.ho;
  if (ho) return handoffDialog(data.handoffs.find((h) => h.id === ho), el);
  const next = e.target.closest("[data-open-next]")?.dataset.openNext;
  if (next) return showSession(next);
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
  const more = e.target.closest("[data-more]")?.dataset.more;
  if (more) return moreRows(el, more);
  const resume = e.target.closest("[data-resume]")?.dataset.resume;
  if (resume) return resumeDevin(data.devin_sessions.find((d) => d.id === resume));
  const key = e.target.closest("[data-launch]")?.dataset.launch;
  if (!key) {
    const sid = e.target.closest("tr[data-sid]")?.dataset.sid;
    return sid ? showSession(sid) : undefined;
  }
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
  const of = (r) => sessions.find((x) => x.id === r.session_id);
  return pagedTable("resume", {
    title: "Resumable sessions",
    sub: "Agent conversations shelldeck can start again in their terminal (Settings: resume ask / auto / never).",
    cols: ["Name", "Agent", "Last state", "Project", ""],
    items: rows,
    text: (r) => [of(r).nick, r.agent, r.last_state, of(r).project.name].join(" "),
    row: (r) => {
      const s = of(r);
      const action = r.can_resume ? `<button class="btn sm" data-resume-agent="${r.session_id}">Resume</button>` : `<span class="faint">${esc(RESUME_WHY[r.error] || r.error || "")}</span>`;
      return `<tr><td><b>${esc(s.nick || "")}</b></td><td>${esc(r.agent)}</td><td>${esc(r.last_state)}</td><td>${esc(s.project.name)}</td><td>${action}</td></tr>`;
    },
  });
}

// History tables: fixed height, searchable, PAGE rows at a time (more on scroll or "Load more").
const PAGE = 25;
const tables = {}; // key -> { q, limit }, kept across the 5s re-render
const specs = {}; // key -> { cols, items, row, text } from the latest render

function pagedTable(key, { title, sub = "", ...spec }) {
  specs[key] = spec;
  const t = (tables[key] ||= { q: "", limit: PAGE });
  return `<div class="ag-head"><h2>${title}</h2>${sub ? `<span class="faint tbl-sub">${sub}</span>` : ""}
      <input type="search" class="tbl-search" data-tq="${key}" value="${esc(t.q)}" placeholder="Search" aria-label="Search ${esc(title)}" /></div>
    <div class="card mon-table tbl-fixed" data-tbl="${key}">${tableBody(key)}</div>`;
}

function tableBody(key) {
  const { cols, items, row, text } = specs[key];
  const t = tables[key];
  const q = t.q.trim().toLowerCase();
  const hits = q ? items.filter((x) => text(x).toLowerCase().includes(q)) : items;
  const shown = hits.slice(0, t.limit);
  const rows = shown.map(row).join("") || `<tr><td colspan="${cols.length}" class="faint empty-row">Nothing matches "${esc(t.q.trim())}".</td></tr>`;
  const foot = hits.length > shown.length
    ? `<div class="tbl-more"><span class="faint">${shown.length} of ${hits.length}</span><button class="btn sm" data-more="${key}">Load more</button></div>`
    : hits.length > PAGE ? `<div class="tbl-more faint">All ${hits.length} shown</div>` : "";
  return `<table><thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>${foot}`;
}

function redrawTable(el, key, top) {
  const box = el.querySelector(`[data-tbl="${key}"]`);
  if (!box) return;
  box.innerHTML = tableBody(key);
  box.scrollTop = top;
}

function moreRows(el, key) {
  tables[key].limit += PAGE;
  redrawTable(el, key, el.querySelector(`[data-tbl="${key}"]`)?.scrollTop || 0);
}
