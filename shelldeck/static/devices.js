// Devices page: every signed-in browser, which one is in use, and revoke.
import { pendingHtml, sharePending } from "./share.js";
import { api, bus, confirmDialog, esc, icon, toast, toastError } from "./ui.js";

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

/** "Edge on Windows" from a user agent; good enough to tell your own devices apart. */
export function deviceName(ua) {
  if (/CFNetwork|Darwin/.test(ua) && !/Mozilla/.test(ua)) return "iOS app"; // native fetch, before login
  const browser = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : /Firefox\//.test(ua) ? "Firefox"
    : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows"
    : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

function viaLabel(via) {
  if (via.startsWith("share:")) return `Share <span class="faint">${esc(via.slice(6))}</span>`;
  return via === "network" ? "Network" : "This machine";
}

export async function renderDevices(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.innerHTML = `<div class="page-head">
        <div><h1>Devices</h1><p>Browsers signed in to shelldeck. One device is in use at a time; logging in on another makes it the one in use and idles the rest until they log in again. Stopping a share signs out its devices.</p></div>
        <button class="btn" data-dev="others">${icon("power")}Sign out other devices</button>
      </div>
      <p class="dev-summary muted"></p>
      <div class="dev-pending" hidden><h3 class="share-h">Waiting for approval</h3><div class="card share-pending"></div></div>
      <div class="card mon-table dev-table"><table><thead><tr><th>Device</th><th>From</th><th>Signed in</th><th>Last active</th><th></th></tr></thead><tbody></tbody></table></div>`;
    el.addEventListener("click", (e) => onClick(e, el));
    bus.addEventListener("share", () => !el.hidden && renderPending(el));
  }
  let data;
  try {
    data = await api("/api/devices");
  } catch (e) {
    return toastError(e);
  }
  pendingFallback = data.pending || [];
  renderPending(el);
  const n = data.devices.length;
  el.querySelector(".dev-summary").textContent =
    `${n} signed in · ${data.devices.filter((d) => d.active).length} in use` + (data.share ? ` · sharing via ${data.share}` : "");
  el.querySelector("[data-dev=others]").disabled = n < 2;
  el.querySelector("tbody").innerHTML = data.devices
    .map(
      (d) => `<tr>
        <td><div class="dev-name">${d.kind === "remote" ? "iPhone app (remote)" : esc(deviceName(d.agent))}${d.current ? ' <span class="exit ok">this device</span>' : ""}</div>
          <div class="hist-meta">${d.active ? '<span class="dev-dot on"></span>In use' : '<span class="dev-dot"></span>Idle'}${d.sockets ? ` · ${d.sockets} connection${d.sockets > 1 ? "s" : ""}` : ""}</div></td>
        <td>${viaLabel(d.via)}<div class="hist-meta">${esc(d.ip)}</div></td>
        <td class="nowrap faint">${esc(ago(d.created_at))}</td>
        <td class="nowrap faint">${esc(ago(d.last_seen))}</td>
        <td class="nowrap"><button class="btn sm" data-dev="revoke" data-id="${esc(d.id)}" data-me="${d.current ? 1 : ""}">${d.current ? "Sign out" : "Revoke"}</button></td>
      </tr>`,
    )
    .join("");
}

let pendingFallback = []; // /api/devices `pending`, for browsers that get no share state

/** Share devices waiting for the host to allow them; Allow/Deny are handled in share.js. */
function renderPending(el) {
  const list = sharePending() ?? pendingFallback;
  el.querySelector(".dev-pending").hidden = !list.length;
  el.querySelector(".share-pending").innerHTML = pendingHtml(list);
}

async function onClick(e, el) {
  const b = e.target.closest("[data-dev]");
  if (!b) return;
  const me = b.dataset.me === "1";
  const others = b.dataset.dev === "others";
  const what = others ? "Sign out every other device?" : me ? "Sign out this device?" : "Revoke this device?";
  if (!(await confirmDialog(`${what} It will need the password to come back.`, { ok: "Sign out", danger: true }))) return;
  try {
    const r = await api(`/api/devices/${others ? "others" : encodeURIComponent(b.dataset.id)}`, { method: "DELETE" });
    if (me) return location.reload();
    toast(`${r.revoked} device${r.revoked === 1 ? "" : "s"} signed out`);
  } catch (err) {
    return toastError(err);
  }
  renderDevices(el);
}
