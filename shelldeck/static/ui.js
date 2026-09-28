// Shared UI helpers: API, icons, dialogs, menus, toasts.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export const bus = new EventTarget();

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (res.status === 401 && (data?.error === "locked" || data?.error === "setup_required")) bus.dispatchEvent(new Event("locked"));
  if (!res.ok) {
    const e = new Error(data?.error || `HTTP ${res.status}`);
    e.data = data;
    throw e;
  }
  return data;
}

const AUTH_ERRORS = {
  invalid_password: "Wrong password.",
  wrong_current_password: "The current password is wrong.",
  password_too_short: "Use at least 8 characters.",
  passwords_do_not_match: "The new password and its confirmation don't match.",
  invalid_setup_code: "That setup code is wrong. It is printed where shelldeck runs (and saved in its data folder as setup-code).",
  password_already_set: "A password already exists. Reload to log in.",
};

/** Human text for an auth API error. */
export function authError(e) {
  if (e.message === "too_many_attempts") return `Too many attempts. Try again in ${e.data?.retry_after || "a few"} seconds.`;
  return AUTH_ERRORS[e.message] || e.message;
}

/** Give every password field in `root` a show/hide eye button. */
export function withEyes(root) {
  for (const input of root.querySelectorAll('input[type="password"]:not([data-eye])')) {
    input.dataset.eye = "1";
    const wrap = document.createElement("span");
    wrap.className = "pw-wrap";
    input.replaceWith(wrap);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-eye";
    btn.setAttribute("aria-label", "Show password");
    btn.setAttribute("aria-pressed", "false");
    btn.title = "Show password";
    btn.innerHTML = icon("eye");
    btn.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.innerHTML = icon(show ? "eye-off" : "eye");
      btn.setAttribute("aria-pressed", String(show));
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      btn.title = show ? "Hide password" : "Show password";
      input.focus();
    });
    wrap.append(input, btn);
  }
}

// Icons: lucide-style strokes (ISC). Inline so the app works offline.
const P = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  "folder-plus": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M12 10v6M9 13h6"/>',
  terminal: '<path d="m4 17 6-6-6-6M12 19h8"/>',
  bookmark: '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2Z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  kanban: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 7v7M12 7v4M16 7v9"/>',
  settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
  "split-h": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18"/>',
  "split-v": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 12h18"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  "chevron-up": '<path d="m18 15-6-6-6 6"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
  minimize: '<path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/>',
  play: '<path d="M6 3 20 12 6 21Z"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  pencil: '<path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  clipboard: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
  keyboard: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h8M7 16h10"/>',
  power: '<path d="M12 2v10M18.4 6.6a9 9 0 1 1-12.77.04"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  "eye-off": '<path d="M9.9 4.2A10 10 0 0 1 12 4c6.5 0 10 8 10 8a17 17 0 0 1-2.2 3.2M6.6 6.6C3.9 8.3 2 12 2 12s3.5 8 10 8a9.7 9.7 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2M2 2l20 20"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  "git-branch": '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.36M21 3v6h-6"/>',
};

export function icon(name) {
  return `<i data-icon="${name}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${P[name] || ""}</svg></i>`;
}

export function hydrateIcons(root = document) {
  for (const el of $$("i[data-icon]:empty", root)) el.outerHTML = icon(el.dataset.icon);
}

// ---------------------------------------------------------------- dialogs

const dialogs = [];

export function topDialog() {
  return dialogs.at(-1);
}

/** Opens a dialog. `body` is HTML. Returns {el, close}. */
export function dialog({ title = "", body = "", foot = "", wide = false, cls = "", onClose } = {}) {
  const bg = document.createElement("div");
  bg.className = "dialog-bg";
  bg.innerHTML = `<div class="dialog ${wide ? "wide" : ""} ${cls}" role="dialog" aria-modal="true" aria-label="${esc(title)}" tabindex="-1">
    ${title ? `<div class="dialog-head"><h2>${esc(title)}</h2><button class="icon-btn" data-close aria-label="Close">${icon("x")}</button></div>` : ""}
    <div class="dialog-body">${body}</div>
    ${foot ? `<div class="dialog-foot">${foot}</div>` : ""}
  </div>`;
  const prevFocus = document.activeElement;
  const d = {
    el: bg.firstElementChild,
    close() {
      if (!bg.isConnected) return;
      bg.remove();
      dialogs.splice(dialogs.indexOf(d), 1);
      onClose?.();
      prevFocus?.focus?.();
    },
  };
  bg.addEventListener("mousedown", (e) => {
    if (e.target === bg) d.close();
  });
  bg.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) d.close();
  });
  $("#dialog-root").append(bg);
  dialogs.push(d);
  hydrateIcons(bg);
  // focus the dialog itself when it has no field, or keys (Escape) keep going to the terminal behind it
  setTimeout(() => ($("[autofocus], input, select, textarea, button.primary", d.el) || d.el).focus(), 0);
  return d;
}

export function confirmDialog(message, { ok = "Confirm", danger = false } = {}) {
  return new Promise((resolve) => {
    let result = false;
    const d = dialog({
      title: "Are you sure?",
      body: `<p class="muted" style="margin:0">${esc(message)}</p>`,
      foot: `<button class="btn" data-close>Cancel</button><button class="btn primary ${danger ? "danger" : ""}" data-ok>${esc(ok)}</button>`,
      onClose: () => resolve(result),
    });
    $("[data-ok]", d.el).onclick = () => {
      result = true;
      d.close();
    };
    setTimeout(() => $("[data-ok]", d.el).focus(), 0);
  });
}

export function promptDialog(title, value = "", { label = "Name", ok = "Save" } = {}) {
  return new Promise((resolve) => {
    let result = null;
    const d = dialog({
      title,
      body: `<form><label class="field"><span>${esc(label)}</span><input type="text" name="v" value="${esc(value)}" autofocus required /></label></form>`,
      foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-ok>${esc(ok)}</button>`,
      onClose: () => resolve(result),
    });
    const submit = (e) => {
      e?.preventDefault();
      const v = $("input", d.el).value.trim();
      if (!v) return;
      result = v;
      d.close();
    };
    $("form", d.el).onsubmit = submit;
    $("[data-ok]", d.el).onclick = submit;
    setTimeout(() => $("input", d.el).select(), 0);
  });
}

// ------------------------------------------------------------------ menus

let openMenu = null;

export function closeMenu() {
  openMenu?.remove();
  openMenu = null;
}

/** items: {label, icon, danger, onClick, hint} | "sep" | {header} */
export function menu(anchor, items) {
  closeMenu();
  const m = document.createElement("div");
  m.className = "menu";
  m.setAttribute("role", "menu");
  m.innerHTML = items
    .map((it, i) => {
      if (it === "sep") return "<hr />";
      if (it.header) return `<div class="menu-label">${esc(it.header)}</div>`;
      return `<button role="menuitem" data-i="${i}" class="${it.danger ? "danger" : ""}">${it.icon ? icon(it.icon) : ""}<span style="flex:1">${esc(it.label)}</span>${it.hint ? `<span class="faint">${esc(it.hint)}</span>` : ""}</button>`;
    })
    .join("");
  m.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (!b) return;
    closeMenu();
    items[+b.dataset.i].onClick?.();
  });
  $("#menu-root").append(m);
  const r = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: anchor.x, right: anchor.x, top: anchor.y, bottom: anchor.y };
  const mw = m.offsetWidth;
  const mh = m.offsetHeight;
  const left = Math.min(r.left, innerWidth - mw - 8);
  const top = r.bottom + 4 + mh > innerHeight ? Math.max(8, r.top - mh - 4) : r.bottom + 4;
  m.style.left = `${Math.max(8, left)}px`;
  m.style.top = `${top}px`;
  openMenu = m;
  m.querySelector("button")?.focus();
  return m;
}

document.addEventListener("pointerdown", (e) => {
  if (openMenu && !openMenu.contains(e.target)) closeMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && openMenu) {
    closeMenu();
    e.stopPropagation();
  } else if (e.key === "Escape" && dialogs.length) {
    e.preventDefault();
    e.stopPropagation();
    topDialog().close();
  }
});

// ----------------------------------------------------------------- toasts

export function toast({ title, body = "", kind = "", actions = [], timeout = 5000 }) {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.innerHTML = `<div class="t-title">${esc(title)}</div>${body ? `<div class="t-body">${esc(body)}</div>` : ""}${
    actions.length ? `<div class="row">${actions.map((a, i) => `<button class="btn sm ${i === 0 ? "primary" : ""}" data-i="${i}">${esc(a.label)}</button>`).join("")}</div>` : ""
  }`;
  const close = () => t.remove();
  t.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (!b) return;
    actions[+b.dataset.i].onClick?.();
    close();
  });
  $("#toasts").append(t);
  if (timeout) setTimeout(close, timeout);
  return { el: t, close };
}

export function toastError(err) {
  toast({ title: "Something went wrong", body: err?.message || String(err), kind: "error" });
}

// ------------------------------------------------------------------ misc

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(+d)) return iso;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** ISO string -> value for <input type="datetime-local"> in local time. */
export function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(+d)) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(value) {
  return value ? new Date(value).toISOString() : null;
}

export const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(`shelldeck.${key}`);
      return v === null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    localStorage.setItem(`shelldeck.${key}`, JSON.stringify(value));
  },
};
