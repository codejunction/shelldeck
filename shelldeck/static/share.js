// Share: the server's cloudflared tunnel for other devices (the iPhone app), its one-use link + QR,
// and allowing the devices that open it. Starting and stopping is host-only; any browser not on
// the tunnel can allow or deny.
import { deviceName } from "./devices.js";
import { chime, settingsDialog } from "./views.js";
import { $, api, bus, confirmDialog, dialog, esc, icon, toast, toastError } from "./ui.js";

let state = null; // ShareState from GET /api/share or the alarm socket
let host = false; // GET /api/share answered: this browser may start and stop sharing
let busy = false; // POST /api/share in flight; the tunnel can take 30s
let hint = ""; // the server's cloudflared install hint
let dlg = null;
let tick = 0;
const asks = new Map(); // pending device id -> its sticky toast

/** Devices waiting for approval, or null before any share state arrived. */
export const sharePending = () => state?.pending ?? null;
export const canShare = () => host;

export async function initShare() {
  try {
    const s = await api("/api/share");
    host = true;
    setShareState(s);
  } catch {
    /* 403 host_only: a LAN or tunnel browser, no share controls */
  }
}

export function setShareState(s) {
  state = s;
  const on = s.sharing || s.starting;
  $("#share-btn").hidden = !host || on;
  $("#share-pill").hidden = !host || !on;
  $("#share-pill span:last-child").textContent = s.sharing ? "Sharing" : "Starting…";
  syncAsks();
  if (dlg) render();
  bus.dispatchEvent(new Event("share"));
}

/** One sticky toast per newly pending device; it closes once the device is no longer pending. */
function syncAsks() {
  const list = state.pending || [];
  const ids = new Set(list.map((p) => p.id));
  for (const [id, t] of asks) {
    if (ids.has(id)) continue;
    t.close();
    asks.delete(id);
  }
  const fresh = list.filter((p) => !asks.has(p.id));
  for (const p of fresh) {
    asks.set(
      p.id,
      toast({
        title: "A device wants to connect",
        body: `${deviceName(p.agent)} · ${p.ip}`,
        kind: "alarm",
        timeout: 0,
        actions: [
          { label: "Allow", onClick: () => decide(p.id, true) },
          { label: "Deny", onClick: () => decide(p.id, false) },
        ],
      }),
    );
  }
  if (fresh.length) chime();
}

async function decide(id, allow) {
  try {
    await api("/api/share/decide", { method: "POST", body: { id, allow } });
  } catch (e) {
    if (e.message !== "not_found") toastError(e); // not_found: answered elsewhere or expired
  }
}

/** Rows with Allow/Deny, for the dialog and the Devices view. */
export function pendingHtml(list) {
  return list
    .map(
      (p) => `<div class="share-dev">
        <div class="share-dev-name"><b>${esc(deviceName(p.agent))}</b><div class="faint small">${esc(p.ip)} · ${esc(new Date(p.at * 1000).toLocaleTimeString())}</div></div>
        <button class="btn sm primary" data-decide="allow" data-id="${esc(p.id)}">Allow</button>
        <button class="btn sm" data-decide="deny" data-id="${esc(p.id)}">Deny</button>
      </div>`,
    )
    .join("");
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-decide]");
  if (!b) return;
  b.disabled = true;
  decide(b.dataset.id, b.dataset.decide === "allow");
});

export function shareDialog() {
  if (dlg) return;
  dlg = dialog({
    title: "Share",
    cls: "share-dialog",
    onClose: () => {
      dlg = null;
      clearInterval(tick);
    },
  });
  dlg.el.addEventListener("click", onClick);
  dlg.el.addEventListener("change", (e) => {
    if (e.target.dataset.share === "terms") $("[data-share=start]", dlg.el).disabled = !e.target.checked || state?.strong_password === false || state?.cloudflared === false;
  });
  tick = setInterval(countdown, 1000);
  render();
  api("/api/share").then(setShareState, () => {}); // fresh password/cloudflared checks
}

function render() {
  const s = state || {};
  const body = $(".dialog-body", dlg.el);
  if (busy || s.starting) {
    body.innerHTML = `<div class="share-wait"><span class="spinner"></span><p>Starting the tunnel…</p><p class="faint small">Cloudflare can take up to 30 seconds.</p></div>`;
  } else if (!s.sharing) {
    const win = /Windows/.test(navigator.userAgent);
    body.innerHTML = `<p class="muted share-intro">Opens a temporary Cloudflare tunnel so another device, such as your phone, can reach this machine. Each device scans a one-use link and you allow it here; it still needs the password.</p>
      ${s.strong_password === false ? `<div class="share-hint">Sharing needs a password of 12 or more characters. <button class="btn sm" data-share="settings">${icon("settings")}Open Settings</button></div>` : ""}
      ${s.cloudflared === false ? `<div class="share-hint">cloudflared is not installed. Install it, then try again: <code>${esc(hint || (win ? "winget install Cloudflare.cloudflared" : "see developers.cloudflare.com, cloudflared downloads"))}</code></div>` : ""}
      ${
        s.terms_accepted === false
          ? `<div class="share-terms"><h3 class="share-h">Terms of sharing</h3><ul>${(s.terms || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
        <label class="check"><input type="checkbox" data-share="terms" /> I have read and accept these terms and share at my own risk</label></div>`
          : ""
      }
      <div class="row end"><button class="btn primary" data-share="start" ${s.strong_password === false || s.cloudflared === false || s.terms_accepted === false ? "disabled" : ""}>${icon("share")}Start sharing</button></div>`;
  } else {
    const l = s.link;
    body.innerHTML = `<p class="faint small share-url">Tunnel <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc((s.url || "").replace(/^https:\/\//, ""))}</a></p>
      ${
        l
          ? `<div class="share-qr" role="img" aria-label="QR code of the share link">${l.qr_svg}</div>
        <div class="share-link"><input type="text" readonly value="${esc(l.url)}" aria-label="Share link" /><button class="btn sm" data-share="copy">${icon("clipboard")}Copy</button></div>
        <p class="faint small share-note">Scan it or open it on the other device. One use, expires in <b data-left></b>.</p>`
          : `<div class="share-hint">The link was used or has expired. Make a new one for another device.</div>`
      }
      ${s.pending?.length ? `<h3 class="share-h">Waiting for approval</h3><div class="share-pending">${pendingHtml(s.pending)}</div>` : ""}
      <div class="row end share-actions"><button class="btn danger" data-share="stop">${icon("power")}Stop sharing</button><button class="btn primary" data-share="link">${icon("refresh")}New link</button></div>`;
    countdown();
  }
}

function countdown() {
  const el = dlg && $("[data-left]", dlg.el);
  if (!el) return;
  const left = Math.max(0, Math.round(state.link.expires_at - Date.now() / 1000));
  el.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

async function onClick(e) {
  const act = e.target.closest("[data-share]")?.dataset.share;
  if (act === "settings") {
    dlg.close();
    settingsDialog();
  } else if (act === "copy") {
    const input = $(".share-link input", dlg.el);
    input.select();
    try {
      await navigator.clipboard.writeText(input.value);
      toast({ title: "Link copied" });
    } catch {
      /* selected; Ctrl+C works */
    }
  } else if (act === "start") {
    const terms = $("[data-share=terms]", dlg.el);
    busy = true;
    render();
    try {
      if (terms?.checked) setShareState(await api("/api/share/terms", { method: "POST", body: { accept: true } }));
      setShareState(await api("/api/share", { method: "POST", body: {} }));
    } catch (err) {
      if (err.message === "weak_password") state = { ...state, strong_password: false };
      else if (err.message === "no_cloudflared") {
        hint = err.data?.hint || "";
        state = { ...state, cloudflared: false };
      } else if (err.message === "tunnel_timeout") toast({ title: "The tunnel didn't come up", body: "Cloudflare took over 30 seconds. Try again.", kind: "error" });
      else toastError(err);
    }
    busy = false;
    if (dlg) render();
  } else if (act === "link") {
    try {
      setShareState(await api("/api/share/link", { method: "POST", body: {} }));
    } catch (err) {
      toastError(err);
    }
  } else if (act === "stop") {
    if (!(await confirmDialog("Stop sharing? Devices connected through the tunnel are signed out.", { ok: "Stop sharing", danger: true }))) return;
    try {
      await api("/api/share", { method: "DELETE" });
      setShareState(await api("/api/share"));
    } catch (err) {
      toastError(err);
    }
  }
}
