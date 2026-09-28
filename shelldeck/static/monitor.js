// Resource usage: top-bar meters and the Task manager page. One poller feeds both.
import { S, findSession, portChips, projectColor, sessionTitle, shellLabel, showSession } from "./app.js";
import { $, api, esc } from "./ui.js";

const POLL_MS = 2000;
const HISTORY = 60; // samples kept for sparklines (2 minutes)
const hist = { cpu: [], mem: [], gpu: [] };
let stats = null;
let pageEl = null;

export function startMonitor() {
  poll();
  setInterval(() => !document.hidden && poll(), POLL_MS);
}

async function poll() {
  try {
    stats = await api("/api/stats");
  } catch {
    return;
  }
  const s = stats.system;
  push("cpu", s.cpu);
  push("mem", (100 * s.mem_used) / s.mem_total);
  if (s.gpu) push("gpu", s.gpu.util);
  renderMeters();
  updatePorts();
  if (pageEl && !pageEl.hidden) renderMonitor(pageEl);
}

// dev servers etc. listening in a terminal: chips in its pane header
function updatePorts() {
  const next = {};
  for (const [sid, u] of Object.entries(stats.sessions)) if (u.ports?.length) next[sid] = u.ports;
  if (JSON.stringify(next) === JSON.stringify(S.ports)) return;
  S.ports = next;
  for (const el of document.querySelectorAll(".pane[data-sid] .pane-head .ports")) {
    el.innerHTML = portChips(el.closest(".pane").dataset.sid);
  }
}

function push(k, v) {
  hist[k].push(v);
  if (hist[k].length > HISTORY) hist[k].shift();
}

export function fmtBytes(b) {
  return b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)} GB` : `${Math.round(b / 2 ** 20)} MB`;
}

const level = (pct) => (pct >= 85 ? "high" : pct >= 60 ? "mid" : "");

function meter(label, pct, text, title) {
  return `<span class="meter" data-level="${level(pct)}" title="${esc(title)}">
    <span class="m-label">${label}</span><span class="m-bar"><i style="width:${Math.min(100, pct).toFixed(1)}%"></i></span><span class="m-val">${esc(text)}</span>
  </span>`;
}

function renderMeters() {
  const el = $("#sysmon");
  if (!el || !stats) return;
  const s = stats.system;
  const memPct = (100 * s.mem_used) / s.mem_total;
  el.innerHTML =
    meter("CPU", s.cpu, `${Math.round(s.cpu)}%`, `CPU ${s.cpu.toFixed(1)}%`) +
    meter("RAM", memPct, `${(s.mem_used / 2 ** 30).toFixed(1)}/${Math.round(s.mem_total / 2 ** 30)}G`, `Memory ${fmtBytes(s.mem_used)} of ${fmtBytes(s.mem_total)} (${Math.round(memPct)}%)`) +
    (s.gpu ? meter("GPU", s.gpu.util, `${Math.round(s.gpu.util)}%`, `${s.gpu.name}: ${s.gpu.util}% · VRAM ${fmtBytes(s.gpu.mem_used)} of ${fmtBytes(s.gpu.mem_total)}`) : "");
}

function spark(values) {
  if (values.length < 2) return `<svg class="spark" viewBox="0 0 100 32" preserveAspectRatio="none"></svg>`;
  const step = 100 / (HISTORY - 1);
  const x0 = 100 - step * (values.length - 1);
  const pts = values.map((v, i) => `${(x0 + i * step).toFixed(2)},${(31 - (Math.min(100, v) / 100) * 30).toFixed(2)}`);
  return `<svg class="spark" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true">
    <polygon points="${x0.toFixed(2)},32 ${pts.join(" ")} 100,32" /><polyline points="${pts.join(" ")}" />
  </svg>`;
}

function card(label, value, sub, key, pct) {
  return `<div class="card mon-card" data-level="${level(pct)}">
    <div class="mon-label">${label}</div>
    <div class="mon-value">${esc(value)}</div>
    <div class="mon-sub">${esc(sub)}</div>
    ${key ? spark(hist[key]) : ""}
  </div>`;
}

export function renderMonitor(el) {
  pageEl = el;
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.addEventListener("click", (e) => {
      const port = e.target.closest("[data-port]")?.dataset.port;
      if (port) return window.open(`http://localhost:${port}`, "_blank", "noopener");
      const sid = e.target.closest("tr[data-sid]")?.dataset.sid;
      if (sid) showSession(sid);
    });
  }
  const head = `<div class="page-head"><div><h1>Task manager</h1>
    <p>Live usage of this machine and of each running terminal (its shell plus every process started from it). Updates every 2 seconds.</p></div></div>`;
  if (!stats) {
    el.innerHTML = `${head}<p class="faint">Loading…</p>`;
    return;
  }
  const s = stats.system;
  const memPct = (100 * s.mem_used) / s.mem_total;
  const cards = [
    card("CPU", `${s.cpu.toFixed(0)}%`, `${navigator.hardwareConcurrency || "?"} logical cores`, "cpu", s.cpu),
    card("Memory", `${fmtBytes(s.mem_used)} / ${fmtBytes(s.mem_total)}`, `${Math.round(memPct)}% in use`, "mem", memPct),
    s.gpu
      ? card("GPU", `${Math.round(s.gpu.util)}%`, `${s.gpu.name} · VRAM ${fmtBytes(s.gpu.mem_used)} / ${fmtBytes(s.gpu.mem_total)}`, "gpu", s.gpu.util)
      : card("GPU", "n/a", "No NVIDIA GPU found (usage comes from nvidia-smi)", null, 0),
  ].join("");

  const rows = Object.entries(stats.sessions)
    .map(([sid, u]) => ({ sid, u, s: findSession(sid) }))
    .filter((r) => r.s)
    .sort((a, b) => b.u.cpu - a.u.cpu || b.u.mem - a.u.mem);
  const total = rows.reduce((t, r) => ({ cpu: t.cpu + r.u.cpu, mem: t.mem + r.u.mem, procs: t.procs + r.u.procs }), { cpu: 0, mem: 0, procs: 0 });
  const body = rows.length
    ? rows
        .map(
          ({ sid, u, s: sess }) => `<tr data-sid="${sid}" title="Open terminal">
        <td><span class="proj-dot" style="background:${projectColor(sess.project)}"></span>${esc(sessionTitle(sess))}<span class="faint"> · ${esc(shellLabel(sess.shell))}</span></td>
        <td class="faint">${esc(sess.project.name)}</td>
        <td class="num"><span class="cell-bar" data-level="${level(u.cpu)}"><i style="width:${Math.min(100, u.cpu)}%"></i></span>${u.cpu.toFixed(1)}%</td>
        <td class="num">${fmtBytes(u.mem)}</td>
        <td class="num">${u.procs}</td>
        <td class="mono">${esc((u.top || "").replace(/\.exe$/i, "")) || '<span class="faint" title="No processes besides the shell">—</span>'}</td>
        <td>${portChips(sid)}</td>
      </tr>`,
        )
        .join("")
    : `<tr><td colspan="7" class="faint empty-row">No running terminals. A terminal starts when you open it.</td></tr>`;

  el.innerHTML = `${head}<div class="mon-cards">${cards}</div>
    <div class="card mon-table"><table>
      <thead><tr><th>Terminal</th><th>Project</th><th class="num">CPU</th><th class="num">Memory</th><th class="num">Processes</th><th>Busiest process</th><th>Ports</th></tr></thead>
      <tbody>${body}</tbody>
      ${rows.length ? `<tfoot><tr><td>All terminals</td><td></td><td class="num">${total.cpu.toFixed(1)}%</td><td class="num">${fmtBytes(total.mem)}</td><td class="num">${total.procs}</td><td></td><td></td></tr></tfoot>` : ""}
    </table></div>`;
}
