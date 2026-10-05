// Command history page and search across every terminal's output.
import { S, findSession, orderedProjects, projectColor, sessionTitle, showSession } from "./app.js";
import { runBookmarks } from "./views.js";
import { $, api, confirmDialog, dialog, esc, fmtTime, icon, loadingHTML, toast, toastError } from "./ui.js";

function fmtMs(ms) {
  if (ms == null) return "";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(s < 10 ? 1 : 0)} s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function terminalLabel(sid, projectId) {
  const s = sid && findSession(sid);
  if (s) return `<span class="proj-dot" style="background:${projectColor(s.project)}"></span>${esc(s.project.name)} · ${esc(sessionTitle(s))}`;
  const p = S.projects.find((x) => x.id === projectId);
  return p ? `<span class="proj-dot" style="background:${projectColor(p)}"></span>${esc(p.name)} <span class="faint">· closed</span>` : '<span class="faint">closed terminal</span>';
}

// ------------------------------------------------------------ history page

let rows = [];

export function renderHistory(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.innerHTML = `<div class="page-head">
        <div><h1>Command history</h1><p>Every command run in shelldeck, with its folder, exit code and duration. Insert types it into the focused terminal; Run also presses Enter.</p></div>
        <button class="btn" data-h="clear">${icon("trash")}Clear history</button>
      </div>
      <div class="row hist-filters">
        <input type="search" class="hist-q" placeholder="Search commands…" spellcheck="false" aria-label="Search commands" />
        <select class="hist-project" aria-label="Project"><option value="">All projects</option></select>
        <label class="hist-failed"><input type="checkbox" /> Failed only</label>
      </div>
      <div class="card hist-table"><table><tbody></tbody></table></div>`;
    let timer = 0;
    const reload = () => {
      clearTimeout(timer);
      timer = setTimeout(() => loadHistory(el), 200);
    };
    el.querySelector(".hist-q").addEventListener("input", reload);
    el.querySelector(".hist-project").addEventListener("change", reload);
    el.querySelector(".hist-failed input").addEventListener("change", reload);
    el.addEventListener("click", (e) => onHistoryClick(e, el));
  }
  const sel = el.querySelector(".hist-project");
  const current = sel.value;
  sel.innerHTML = `<option value="">All projects</option>${orderedProjects().map((p) => `<option value="${p.id}" ${p.id === current ? "selected" : ""}>${esc(p.name)}</option>`).join("")}`;
  loadHistory(el);
}

async function loadHistory(el) {
  const params = new URLSearchParams({
    q: el.querySelector(".hist-q").value,
    project_id: el.querySelector(".hist-project").value,
    failed: el.querySelector(".hist-failed input").checked,
  });
  try {
    rows = (await api(`/api/history?${params}`)).commands;
  } catch (e) {
    return toastError(e);
  }
  el.querySelector(".hist-table tbody").innerHTML = rows.length
    ? rows
        .map(
          (r, i) => `<tr>
        <td class="faint nowrap">${esc(fmtTime(r.started_at))}</td>
        <td class="hist-cmd"><code title="${esc(r.command)}">${esc(r.command)}</code>
          <div class="hist-meta">${terminalLabel(r.session_id, r.project_id)}${r.cwd ? `<span class="faint"> · ${esc(r.cwd)}</span>` : ""}</div></td>
        <td class="nowrap">${r.exit_code == null ? "" : `<span class="exit ${r.exit_code ? "bad" : "ok"}" title="Exit code">${r.exit_code ? `exit ${r.exit_code}` : "ok"}</span>`}</td>
        <td class="num faint nowrap">${fmtMs(r.duration_ms)}</td>
        <td class="nowrap hist-actions">
          <button class="icon-btn sm" data-h="insert" data-i="${i}" title="Insert into focused terminal" aria-label="Insert">${icon("terminal")}</button>
          <button class="icon-btn sm" data-h="run" data-i="${i}" title="Run in focused terminal" aria-label="Run">${icon("play")}</button>
          <button class="icon-btn sm" data-h="copy" data-i="${i}" title="Copy" aria-label="Copy">${icon("clipboard")}</button>
          <button class="icon-btn sm" data-h="bookmark" data-i="${i}" title="Save as bookmark" aria-label="Save as bookmark">${icon("bookmark")}</button>
        </td>
      </tr>`,
        )
        .join("")
    : `<tr><td class="faint empty-row">No commands yet. Commands appear here once you run them in a terminal.</td></tr>`;
}

async function onHistoryClick(e, el) {
  const b = e.target.closest("[data-h]");
  if (!b) return;
  const a = b.dataset.h;
  if (a === "clear") {
    if (await confirmDialog("Delete every saved command? Terminals are not affected.", { ok: "Clear history", danger: true })) {
      await api("/api/history", { method: "DELETE" });
      loadHistory(el);
    }
    return;
  }
  const r = rows[+b.dataset.i];
  if (!r) return;
  if (a === "copy") {
    await navigator.clipboard.writeText(r.command).catch(() => {});
    toast({ title: "Copied", body: r.command, timeout: 2000 });
  } else if (a === "insert" || a === "run") {
    const focused = S.focused && findSession(S.focused);
    if (focused) showSession(focused.id);
    runBookmarks([{ command: r.command, project_id: focused ? null : r.project_id }], { execute: a === "run" });
  } else if (a === "bookmark") {
    try {
      await api("/api/bookmarks", { method: "POST", body: { name: r.command.slice(0, 40), command: r.command, project_id: r.project_id || null } });
      toast({ title: "Saved as bookmark", body: r.command, timeout: 2500 });
    } catch (err) {
      toastError(err);
    }
  }
}

// ------------------------------------------------------ search all terminals

function highlight(text, q) {
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return esc(text);
  return `${esc(text.slice(0, i))}<mark>${esc(text.slice(i, i + q.length))}</mark>${esc(text.slice(i + q.length))}`;
}

export function searchAllDialog() {
  const d = dialog({
    title: "Search all terminals",
    wide: true,
    cls: "search-all",
    body: `<input type="search" class="sa-q" placeholder="Text in any terminal's output (2+ characters)…" spellcheck="false" aria-label="Search text" />
      <div class="sa-results"><p class="faint">Searches the saved output of every terminal, including ones not on screen.</p></div>`,
  });
  const input = $(".sa-q", d.el);
  const out = $(".sa-results", d.el);
  let timer = 0;
  let seq = 0;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      const mine = ++seq;
      if (q.length < 2) {
        out.innerHTML = `<p class="faint">Type at least 2 characters.</p>`;
        return;
      }
      let results;
      out.innerHTML = loadingHTML("Searching…");
      try {
        results = (await api(`/api/search?q=${encodeURIComponent(q)}`)).results;
      } catch (e) {
        return toastError(e);
      }
      if (mine !== seq) return;
      out.innerHTML = results.length
        ? results
            .map(
              (r) => `<div class="sa-group">
          <div class="sa-head">${terminalLabel(r.session_id)}<span class="faint"> · ${r.count} match${r.count === 1 ? "" : "es"}</span></div>
          ${r.hits.map((h) => `<button class="sa-hit" data-sid="${r.session_id}"><code>${highlight(h.text, q)}</code></button>`).join("")}
        </div>`,
            )
            .join("")
        : `<p class="faint">No matches.</p>`;
    }, 200);
  });
  out.addEventListener("click", (e) => {
    const hit = e.target.closest(".sa-hit");
    if (!hit) return;
    const q = input.value.trim();
    d.close();
    const t = showSession(hit.dataset.sid);
    setTimeout(() => (t || S.terms.get(hit.dataset.sid))?.openFind(q), 50);
  });
  input.focus();
}
