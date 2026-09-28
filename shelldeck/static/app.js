import { $, $$, api, authError, bus, closeMenu, confirmDialog, dialog, esc, hydrateIcons, icon, menu, promptDialog, store, toast, toastError, topDialog, withEyes } from "./ui.js";
import * as views from "./views.js";
import { startMonitor } from "./monitor.js";
import { searchAllDialog } from "./history.js";
import { showGitGraph } from "./gitgraph.js";

// ------------------------------------------------------------------ state

export const S = {
  projects: [],
  settings: { default_shell: "pwsh", wsl_distro: "", font_size: "13", theme: "dark" },
  shells: { kinds: [], available: [], wsl_distros: [] },
  terms: new Map(), // sid -> Term
  layout: store.get("layout", null), // {sid} | {dir, kids, sizes}
  free: store.get("free", {}), // free mode: sid -> {x, y, w, h, z}
  focused: store.get("focused", null),
  view: "terminals",
  collapsed: new Set(store.get("collapsed", [])),
  order: store.get("order", []),
  unread: new Set(),
  ports: {}, // sid -> listening TCP ports, from the stats poller
  online: true,
};

const SHELL_LABEL = { pwsh: "pwsh", powershell: "PowerShell", cmd: "cmd", wsl: "WSL" };
const mobile = matchMedia("(max-width: 900px)");
const isFree = () => S.settings.layout_mode === "free" && !mobile.matches;

// Project palette slots (db column projects.color). Tuned to read on dark and light.
const PROJECT_COLORS = ["#60a5fa", "#f472b6", "#34d399", "#fbbf24", "#a78bfa", "#f87171", "#22d3ee", "#fb923c"];

export function projectColor(p) {
  return PROJECT_COLORS[(p?.color ?? 0) % PROJECT_COLORS.length];
}

function mix(a, b, t) {
  const n = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [n(a), n(b)];
  return `#${x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("")}`;
}

export function shellLabel(kind) {
  kind = kind || S.settings.default_shell;
  return S.shells.labels?.[kind] || SHELL_LABEL[kind] || kind;
}

export function allSessions() {
  return S.projects.flatMap((p) => p.sessions.map((s) => ({ ...s, project: p })));
}

export function findSession(sid) {
  return allSessions().find((s) => s.id === sid);
}

export function sessionTitle(s) {
  return s.name || `${shellLabel(s.shell)} ${s.id.slice(0, 4)}`;
}

function focusedProject() {
  const s = S.focused && findSession(S.focused);
  return s?.project || orderedProjects()[0] || null;
}

// ------------------------------------------------------------------ theme

const XTERM_THEMES = {
  dark: {
    background: "#131316", foreground: "#e4e4e7", cursor: "#f4f4f5", cursorAccent: "#131316",
    selectionBackground: "rgba(167,139,250,0.32)",
    black: "#27272a", red: "#f87171", green: "#4ade80", yellow: "#facc15", blue: "#60a5fa", magenta: "#c084fc", cyan: "#22d3ee", white: "#e4e4e7",
    brightBlack: "#71717a", brightRed: "#fca5a5", brightGreen: "#86efac", brightYellow: "#fde047", brightBlue: "#93c5fd", brightMagenta: "#d8b4fe", brightCyan: "#67e8f9", brightWhite: "#fafafa",
  },
  light: {
    background: "#ffffff", foreground: "#27272a", cursor: "#18181b", cursorAccent: "#ffffff",
    selectionBackground: "rgba(124,58,237,0.2)",
    black: "#18181b", red: "#dc2626", green: "#16a34a", yellow: "#ca8a04", blue: "#2563eb", magenta: "#9333ea", cyan: "#0891b2", white: "#d4d4d8",
    brightBlack: "#71717a", brightRed: "#ef4444", brightGreen: "#22c55e", brightYellow: "#eab308", brightBlue: "#3b82f6", brightMagenta: "#a855f7", brightCyan: "#06b6d4", brightWhite: "#f4f4f5",
  },
};

// Terminal color presets (setting terminal_theme; keep names in sync with TERMINAL_THEMES in server.py).
// "default" follows the app theme. Backgrounds must be #rrggbb so project tint can mix in.
const pal = (bg, fg, cursor, sel, c) => {
  const [black, red, green, yellow, blue, magenta, cyan, white, bBlack, bRed, bGreen, bYellow, bBlue, bMagenta, bCyan, bWhite] = c.split(" ");
  return {
    background: bg, foreground: fg, cursor, cursorAccent: bg, selectionBackground: sel,
    black, red, green, yellow, blue, magenta, cyan, white,
    brightBlack: bBlack, brightRed: bRed, brightGreen: bGreen, brightYellow: bYellow, brightBlue: bBlue, brightMagenta: bMagenta, brightCyan: bCyan, brightWhite: bWhite,
  };
};
const PRESETS = {
  dracula: pal("#282a36", "#f8f8f2", "#f8f8f2", "rgba(68,71,90,0.9)", "#21222c #ff5555 #50fa7b #f1fa8c #bd93f9 #ff79c6 #8be9fd #f8f8f2 #6272a4 #ff6e6e #69ff94 #ffffa5 #d6acff #ff92df #a4ffff #ffffff"),
  "one-dark": pal("#282c34", "#abb2bf", "#528bff", "rgba(62,68,81,0.9)", "#3f4451 #e06c75 #98c379 #e5c07b #61afef #c678dd #56b6c2 #abb2bf #4f5666 #ff7b86 #b1e18b #efb074 #67cdff #e48bff #63d4e0 #ffffff"),
  nord: pal("#2e3440", "#d8dee9", "#d8dee9", "rgba(67,76,94,0.9)", "#3b4252 #bf616a #a3be8c #ebcb8b #81a1c1 #b48ead #88c0d0 #e5e9f0 #4c566a #bf616a #a3be8c #ebcb8b #81a1c1 #b48ead #8fbcbb #eceff4"),
  "gruvbox-dark": pal("#282828", "#ebdbb2", "#ebdbb2", "rgba(80,73,69,0.9)", "#282828 #cc241d #98971a #d79921 #458588 #b16286 #689d6a #a89984 #928374 #fb4934 #b8bb26 #fabd2f #83a598 #d3869b #8ec07c #ebdbb2"),
  "solarized-dark": pal("#002b36", "#839496", "#93a1a1", "rgba(7,54,66,0.9)", "#073642 #dc322f #859900 #b58900 #268bd2 #d33682 #2aa198 #eee8d5 #586e75 #cb4b16 #859900 #b58900 #268bd2 #6c71c4 #2aa198 #fdf6e3"),
  "solarized-light": pal("#fdf6e3", "#657b83", "#586e75", "rgba(147,161,161,0.3)", "#073642 #dc322f #859900 #b58900 #268bd2 #d33682 #2aa198 #eee8d5 #586e75 #cb4b16 #859900 #b58900 #268bd2 #6c71c4 #2aa198 #002b36"),
  "github-light": pal("#ffffff", "#24292f", "#0969da", "rgba(84,174,255,0.3)", "#24292f #cf222e #116329 #4d2d00 #0969da #8250df #1b7c83 #6e7781 #57606a #a40e26 #1a7f37 #633c01 #218bff #a475f9 #3192aa #8c959f"),
};
export const TERMINAL_THEMES = ["default", ...Object.keys(PRESETS)];

const DEFAULT_FONT = '"CaskaydiaCove Nerd Font", "CaskaydiaCove NF", "CaskaydiaMono Nerd Font", "Cascadia Code NF", "Cascadia Code", "Cascadia Mono", Consolas, monospace';

function fontFamily() {
  const f = (S.settings.font_family || "").trim();
  if (!f) return DEFAULT_FONT;
  return `${/[,"']/.test(f) ? f : `"${f}"`}, ${DEFAULT_FONT}`;
}

const prefersDark = matchMedia("(prefers-color-scheme: dark)");

function resolvedTheme() {
  const t = S.settings.theme;
  return t === "system" ? (prefersDark.matches ? "dark" : "light") : t === "light" ? "light" : "dark";
}

function themeFor(sid) {
  const base = PRESETS[S.settings.terminal_theme] || XTERM_THEMES[resolvedTheme()];
  const dark = parseInt(base.background.slice(1, 3), 16) < 0x80;
  const s = findSession(sid);
  if (!s || S.settings.project_tint === "off") return base;
  const bg = mix(base.background, projectColor(s.project), dark ? 0.09 : 0.07);
  return { ...base, background: bg, cursorAccent: bg };
}

let lastMode = null;
export function applySettings() {
  document.documentElement.dataset.theme = resolvedTheme();
  for (const t of S.terms.values()) {
    t.xterm.options.theme = themeFor(t.sid);
    t.xterm.options.fontSize = +S.settings.font_size || 13;
    t.xterm.options.fontFamily = fontFamily();
    t.fit();
  }
  for (const p of $$(".pane[data-sid]")) paintPane(p, p.dataset.sid);
  for (const b of $$("[data-action=split]")) b.hidden = isFree();
  $("[data-action=tile]").hidden = !isFree();
  const mode = S.settings.layout_mode;
  if (lastMode && mode !== lastMode) {
    if (mode === "free" && !Object.keys(S.free).length) {
      for (const sid of leaves(S.layout)) S.free[sid] = {};
      tileAll(false);
    }
    renderLayout();
  }
  lastMode = mode;
}

function paintPane(pane, sid) {
  const s = findSession(sid);
  pane.style.setProperty("--proj", s ? projectColor(s.project) : "transparent");
  pane.style.background = themeFor(sid).background;
}
prefersDark.addEventListener("change", applySettings);

// -------------------------------------------------------------- terminals

class Term {
  constructor(sid) {
    this.sid = sid;
    this.el = document.createElement("div");
    this.el.className = "term-host";
    this.el.style.height = "100%";
    this.xterm = new Terminal({
      fontFamily: fontFamily(),
      fontSize: +S.settings.font_size || 13,
      lineHeight: 1.15,
      cursorBlink: true,
      scrollback: 10000,
      allowProposedApi: true,
      theme: themeFor(sid),
    });
    this.fitAddon = new FitAddon.FitAddon();
    this.xterm.loadAddon(this.fitAddon);
    this.xterm.loadAddon(new WebLinksAddon.WebLinksAddon((e, uri) => window.open(uri, "_blank", "noopener")));
    this.search = new SearchAddon.SearchAddon();
    this.xterm.loadAddon(this.search);
    this.serializer = new SerializeAddon.SerializeAddon();
    this.xterm.loadAddon(this.serializer);
    this.lastSnapshot = "";
    this.changed = false; // output since the last snapshot
    // shell integration (shelldeck/integration): 133 = prompt/command marks, 633 = command line + cwd
    this.integrated = false;
    this.prompts = []; // xterm markers at each prompt, for Ctrl+Shift+Up/Down
    this.running = null; // {cmd, at} from Enter until the shell reports the command finished
    this.xterm.parser.registerOscHandler(133, (data) => this.osc133(data));
    this.xterm.parser.registerOscHandler(633, (data) => this.osc633(data));
    this.opened = false;
    this.ws = null;
    this.connectedOnce = false;
    this.closed = false;
    this.retry = 0;
    this.size = null;
    this.readyWaiters = [];

    // ignore xterm's auto-replies (e.g. to DA queries) while replaying scrollback
    this.replaying = false;
    this.xterm.onData((data) => {
      if (this.replaying) return;
      this.track(data);
      this.send({ type: "input", data });
    });
    this.line = "";
    this.edited = false; // arrows/tab used: typed buffer no longer matches the line
    this.cmd = "";
    this.xterm.attachCustomKeyEventHandler((e) => this.keyHandler(e));
  }

  keyHandler(e) {
    if (e.type !== "keydown") return true;
    const k = e.key.toLowerCase();
    if (isAppShortcut(e)) return false;
    if (e.ctrlKey && !e.altKey && !e.shiftKey && k === "f") {
      e.preventDefault();
      this.openFind();
      return false;
    }
    if (e.ctrlKey && e.shiftKey && !e.altKey && (k === "arrowup" || k === "arrowdown")) {
      e.preventDefault();
      this.jumpPrompt(k === "arrowup" ? -1 : 1);
      return false;
    }
    if (e.ctrlKey && !e.altKey && (k === "c" || k === "insert") && this.xterm.hasSelection()) {
      navigator.clipboard.writeText(this.xterm.getSelection()).catch(() => {});
      this.xterm.clearSelection();
      e.preventDefault();
      return false;
    }
    if (e.ctrlKey && e.shiftKey && k === "c") {
      if (this.xterm.hasSelection()) navigator.clipboard.writeText(this.xterm.getSelection()).catch(() => {});
      e.preventDefault();
      return false;
    }
    // let the browser fire a paste event; xterm handles it
    if ((e.ctrlKey && !e.altKey && k === "v") || (e.shiftKey && k === "insert")) return false;
    return true;
  }

  // ponytail: heuristic command tracking from keystrokes; shell integration (OSC 633) if it proves flaky.
  track(data) {
    for (const part of data.split(/(\r)/)) {
      if (part === "\r") this.commit();
      else if (part === "\x03") (this.line = ""), (this.edited = false);
      else if (part === "\x7f" || part === "\b") this.line = this.line.slice(0, -1);
      else if (/^\x1b\[[IO]$/.test(part)) continue; // focus in/out reports
      else if (part.startsWith("\x1b") || part.includes("\t")) this.edited = true;
      else if (part >= " ") this.line += part;
    }
  }

  commit() {
    let cmd = this.line;
    if (this.edited || !cmd.trim()) {
      const b = this.xterm.buffer.active;
      // join soft-wrapped rows so long commands read as one line
      let y = b.baseY + b.cursorY;
      let text = b.getLine(y)?.translateToString(true) || "";
      while (y > 0 && b.getLine(y)?.isWrapped) text = (b.getLine(--y)?.translateToString(true) || "") + text;
      cmd = text.match(/[>$#❯➜%]\s+(.+)$/)?.[1] || (this.edited ? "" : cmd);
    }
    this.line = "";
    this.edited = false;
    cmd = cmd.trim().split(/\s{3,}/)[0]; // drop right-aligned prompt segments
    if (!cmd) return;
    this.cmd = cmd;
    if (!this.running) this.running = { cmd, at: Date.now() };
    if (!this.integrated) this.send({ type: "command", cmd, at: new Date().toISOString() });
    this.exit = null;
    this.paintCmd();
  }

  paintCmd() {
    const el = document.querySelector(`.pane[data-sid="${this.sid}"] .title .cmd`);
    if (!el) return;
    el.textContent = shortCmd(this.cmd);
    el.title = this.exit ? `${this.cmd} (exit ${this.exit})` : this.cmd;
    el.classList.toggle("fail", !!this.exit);
  }

  osc133(data) {
    const [kind, code] = data.split(";");
    this.integrated = true;
    if (kind === "A") {
      const m = this.xterm.registerMarker(0);
      if (m) this.prompts.push(m);
      if (this.prompts.length > 500) this.prompts.shift().dispose();
    } else if (kind === "D") {
      const r = this.running;
      this.running = null;
      if (!r || this.replaying) return true;
      this.exit = code ? +code || 0 : null;
      this.paintCmd();
      this.send({ type: "command", cmd: this.cmd || r.cmd, exit: this.exit, ms: Date.now() - r.at, at: new Date(r.at).toISOString() });
      if (Date.now() - r.at >= LONG_CMD_MS) notifyDone(this, this.cmd || r.cmd, Date.now() - r.at);
    }
    return true;
  }

  osc633(data) {
    if (data.startsWith("E;")) {
      this.cmd = data.slice(2).trim() || this.cmd;
      this.paintCmd();
    }
    return true; // P;Cwd= is recorded server-side
  }

  /** Send the rendered screen to the server; it is what comes back after a restart. */
  snapshot() {
    if (!this.changed || this.replaying || this.closed) return;
    this.changed = false;
    // drop the trailing cursor moves / mode sets: the restore banner must land after the content
    const data = this.serializer.serialize({ scrollback: 2000 }).replace(/(?:\x1b\[[0-9;]*[ABCDGHf]|\x1b\[\?[0-9;]*[hl])+$/, "");
    if (data === this.lastSnapshot) return;
    this.lastSnapshot = data;
    this.send({ type: "snapshot", data });
  }

  jumpPrompt(dir) {
    const top = this.xterm.buffer.active.viewportY;
    const lines = this.prompts.filter((m) => !m.isDisposed && m.line >= 0).map((m) => m.line);
    const target = dir < 0 ? lines.filter((l) => l < top).pop() : lines.find((l) => l > top);
    if (target === undefined) return dir > 0 && this.xterm.scrollToBottom();
    this.xterm.scrollToLine(target);
  }

  openFind(query = "") {
    let bar = this.el.querySelector(".find-bar");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "find-bar";
      bar.innerHTML = `<input type="text" placeholder="Find" spellcheck="false" aria-label="Find in terminal" />
        <span class="find-count"></span>
        <button class="icon-btn sm" data-find="prev" title="Previous (Shift+Enter)" aria-label="Previous match">${icon("chevron-up")}</button>
        <button class="icon-btn sm" data-find="next" title="Next (Enter)" aria-label="Next match">${icon("chevron")}</button>
        <button class="icon-btn sm" data-find="close" title="Close (Esc)" aria-label="Close find">${icon("x")}</button>`;
      const input = bar.querySelector("input");
      const count = bar.querySelector(".find-count");
      this.search.onDidChangeResults(({ resultIndex, resultCount }) => {
        count.textContent = !input.value ? "" : resultCount ? `${resultIndex + 1}/${resultCount}` : "No results";
      });
      const go = (dir, incremental = false) => {
        if (!input.value) return this.search.clearDecorations();
        const opts = { incremental, decorations: FIND_DECORATIONS };
        dir < 0 ? this.search.findPrevious(input.value, opts) : this.search.findNext(input.value, opts);
      };
      input.addEventListener("input", () => go(1, true));
      input.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Enter") go(e.shiftKey ? -1 : 1);
        else if (e.key === "Escape") this.closeFind();
        else if (e.key.toLowerCase() === "f" && e.ctrlKey) {
          e.preventDefault();
          input.select();
        }
      });
      bar.addEventListener("click", (e) => {
        const a = e.target.closest("[data-find]")?.dataset.find;
        if (a === "close") this.closeFind();
        else if (a) go(a === "prev" ? -1 : 1);
      });
      this.el.append(bar);
    }
    const input = bar.querySelector("input");
    const sel = query || this.xterm.getSelection();
    if (sel && !sel.includes("\n")) input.value = sel;
    input.focus();
    input.select();
    if (input.value) this.search.findNext(input.value, { decorations: FIND_DECORATIONS });
  }

  closeFind() {
    this.search.clearDecorations();
    this.el.querySelector(".find-bar")?.remove();
    this.focus();
  }

  mount(container) {
    container.append(this.el);
    if (!this.opened) {
      this.xterm.open(this.el);
      try {
        const gl = new WebglAddon.WebglAddon();
        gl.onContextLoss(() => gl.dispose());
        this.xterm.loadAddon(gl);
      } catch {
        /* DOM renderer */
      }
      this.xterm.textarea.addEventListener("focus", () => setFocus(this.sid));
      this.xterm.registerLinkProvider(pathLinks(this));
      this.opened = true;
      this.connect();
    }
  }

  ready() {
    if (this.ws?.readyState === WebSocket.OPEN) return Promise.resolve();
    return new Promise((r) => this.readyWaiters.push(r));
  }

  connect() {
    if (this.closed) return;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/${this.sid}`);
    this.ws = ws;
    this.status("");
    this.heard = Date.now();
    ws.onopen = () => {
      this.heard = Date.now();
      if (this.connectedOnce) this.xterm.reset(); // server replays scrollback
      this.connectedOnce = true;
      this.retry = 0;
      this.size = null;
      this.fit(true);
      if (!this.gotOutput) this.status("Starting shell…");
      this.readyWaiters.splice(0).forEach((r) => r());
    };
    ws.onmessage = (ev) => {
      this.heard = Date.now();
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "output") {
        if (!this.gotOutput) {
          this.gotOutput = true;
          this.status("");
        }
        if (msg.replay) {
          this.replaying = true;
          this.xterm.write(msg.data, () => (this.replaying = false));
        } else {
          this.xterm.write(msg.data);
          this.changed = true;
          markUnread(this.sid);
        }
      }
      else if (msg.type === "exit") this.exited();
      else if (msg.type === "error") this.status(msg.message);
      else if (msg.type === "clipboard") this.send({ type: "input", data: msg.data });
    };
    ws.onclose = (ev) => {
      if (this.closed) return;
      if (ev.code === 4404 || ev.code === 1000) return this.exited();
      if (ev.code === 1008) return bus.dispatchEvent(new Event("locked")); // logging in reloads the page
      this.status("Reconnecting…");
      const delay = Math.min(10000, 500 * 2 ** this.retry++);
      clearTimeout(this.retryTimer);
      this.retryTimer = setTimeout(() => this.connect(), delay);
    };
  }

  /** Heartbeat: a socket silent for 45s is dead (sleep, Wi-Fi switch, VPN drop), so replace it. */
  heartbeat() {
    if (this.closed || this.ws?.readyState !== WebSocket.OPEN) return;
    if (Date.now() - this.heard > 45000) return this.ws.close();
    this.send({ type: "ping" });
  }

  /** Reconnect now instead of waiting out the backoff (network back, tab visible again). */
  reconnectNow() {
    if (this.closed || this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) return;
    clearTimeout(this.retryTimer);
    this.retry = 0;
    this.connect();
  }

  status(text) {
    let el = this.el.querySelector(".pane-status");
    if (!text) return el?.remove();
    if (!el) {
      el = document.createElement("div");
      el.className = "pane-status";
      this.el.append(el);
    }
    el.textContent = text;
  }

  send(msg) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  fit(force = false) {
    if (!this.opened || !this.el.offsetParent) return;
    try {
      this.fitAddon.fit();
    } catch {
      return;
    }
    const { rows, cols } = this.xterm;
    if (force || !this.size || this.size.rows !== rows || this.size.cols !== cols) {
      this.size = { rows, cols };
      this.send({ type: "resize", rows, cols });
    }
  }

  focus() {
    this.xterm.focus();
  }

  exited() {
    if (this.closed) return;
    this.dispose();
    removeSessionLocally(this.sid);
    api(`/api/sessions/${this.sid}`, { method: "DELETE" }).catch(() => {});
  }

  dispose() {
    this.closed = true;
    this.ws?.close();
    this.xterm.dispose();
    this.el.remove();
    S.terms.delete(this.sid);
  }
}

// file paths in output (src/app.py:12:5, C:\x\y.ts(3,4), File "a.py", line 7) that exist become links
const PATH_RE = /(?<![\w/\\.:-])((?:[A-Za-z]:[\\/]|~[\\/]|\.{1,2}[\\/]|[\\/])?(?:[\w.@+-]+[\\/])*[\w@+-][\w.@+-]*\.[A-Za-z][\w]{0,7})(?:(?::|\()(\d+)(?:[:,](\d+))?\)?|", line (\d+))?/g;
// `${sid}\n${raw}` -> {ok, at}; short-lived because relative paths change meaning with `cd`
const pathCache = new Map();
const PATH_TTL = 10000;

async function existing(sid, raws) {
  const now = Date.now();
  const fresh = (r) => now - (pathCache.get(`${sid}\n${r}`)?.at ?? 0) < PATH_TTL;
  const todo = raws.filter((r) => !fresh(r));
  if (todo.length) {
    try {
      const { files } = await api("/api/fs/check", { method: "POST", body: { session_id: sid, paths: todo } });
      for (const r of todo) pathCache.set(`${sid}\n${r}`, { ok: files.includes(r), at: now });
    } catch {
      return new Set();
    }
    if (pathCache.size > 5000) pathCache.clear();
  }
  return new Set(raws.filter((r) => pathCache.get(`${sid}\n${r}`)?.ok));
}

function pathLinks(term) {
  return {
    async provideLinks(y, done) {
      const text = term.xterm.buffer.active.getLine(y - 1)?.translateToString(true) || "";
      const found = [...text.matchAll(PATH_RE)].filter((m) => !/^https?:/i.test(text.slice(Math.max(0, m.index - 8), m.index + 1))).slice(0, 20);
      if (!found.length) return done(undefined);
      const ok = await existing(term.sid, [...new Set(found.map((m) => m[1]))]);
      const links = found
        .filter((m) => ok.has(m[1]))
        .map((m) => ({
          range: { start: { x: m.index + 1, y }, end: { x: m.index + m[0].length, y } },
          text: m[0],
          decorations: { underline: true, pointerCursor: true },
          activate: () =>
            api("/api/open", { method: "POST", body: { session_id: term.sid, path: m[1], line: +(m[2] || m[4] || 0), col: +(m[3] || 0) } })
              .catch((e) => toastError(e)),
        }));
      done(links.length ? links : undefined);
    },
  };
}

export function portChips(sid) {
  return (S.ports[sid] || []).map((p) => `<button class="port-chip" data-port="${p}" title="Open http://localhost:${p}">${icon("external")}${p}</button>`).join("");
}

const CMD_MAX = 32;
const LONG_CMD_MS = 10000; // commands at least this long notify when they finish out of view
// search decorations must be #rrggbb
const FIND_DECORATIONS = { matchBackground: "#3f3f46", activeMatchBackground: "#7c3aed", matchOverviewRuler: "#71717a", activeMatchColorOverviewRuler: "#a78bfa" };

function fmtDur(ms) {
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function notifyDone(t, cmd, ms) {
  const away = document.hidden || !document.hasFocus();
  if (!away && S.view === "terminals" && S.focused === t.sid) return;
  const s = findSession(t.sid);
  const title = `${t.exit ? "Failed" : "Finished"}: ${shortCmd(cmd) || "command"}`;
  const body = `${s ? `${sessionTitle(s)} · ` : ""}${fmtDur(ms)}${t.exit ? ` · exit ${t.exit}` : ""}`;
  const show = () => {
    window.focus();
    showSession(t.sid);
  };
  const canAsk = "Notification" in window && Notification.permission === "default";
  toast({
    title,
    body,
    kind: t.exit ? "error" : "",
    timeout: 10000,
    actions: [{ label: "Show", onClick: show }, ...(canAsk ? [{ label: "Enable desktop alerts", onClick: () => Notification.requestPermission() }] : [])],
  });
  if (away && "Notification" in window && Notification.permission === "granted") {
    const n = new Notification(title, { body, icon: "/static/icon-192.png", tag: t.sid });
    n.onclick = () => {
      show();
      n.close();
    };
  }
}

// sessions that printed output while out of view; cleared when shown
function isVisible(sid) {
  return S.view === "terminals" && leaves().includes(sid);
}

function markUnread(sid) {
  if (S.unread.has(sid) || isVisible(sid)) return;
  S.unread.add(sid);
  $(`.sess-row[data-sid="${sid}"]`)?.classList.add("unread");
}

function clearUnread() {
  for (const sid of [...S.unread]) {
    if (!isVisible(sid)) continue;
    S.unread.delete(sid);
    $(`.sess-row[data-sid="${sid}"]`)?.classList.remove("unread");
  }
}

function shortCmd(cmd) {
  if (!cmd) return "";
  return cmd.length > CMD_MAX ? `${cmd.slice(0, CMD_MAX - 1)}…` : cmd;
}

function getTerm(sid) {
  let t = S.terms.get(sid);
  if (!t) {
    t = new Term(sid);
    S.terms.set(sid, t);
  }
  return t;
}

// ----------------------------------------------------------------- layout

const isLeaf = (n) => n && "sid" in n;

export function leaves(n) {
  if (n === undefined) {
    if (isFree()) return Object.keys(S.free);
    n = S.layout;
  }
  if (!n) return [];
  return isLeaf(n) ? [n.sid] : n.kids.flatMap((k) => leaves(k));
}

function mapTree(n, fn) {
  if (!n) return null;
  if (isLeaf(n)) return fn(n);
  const kids = [];
  const sizes = [];
  n.kids.forEach((k, i) => {
    const r = mapTree(k, fn);
    if (r) {
      kids.push(r);
      sizes.push(n.sizes[i]);
    }
  });
  if (!kids.length) return null;
  if (kids.length === 1) return kids[0];
  const total = sizes.reduce((a, b) => a + b, 0);
  return { dir: n.dir, kids, sizes: sizes.map((s) => s / total) };
}

function removeLeaf(sid) {
  delete S.free[sid];
  S.layout = mapTree(S.layout, (l) => (l.sid === sid ? null : l));
}

function replaceLeaf(targetSid, sid) {
  S.layout = mapTree(S.layout, (l) => (l.sid === targetSid ? { sid } : l));
}

/** Put `sid` next to `targetSid`. side: left|right|top|bottom */
function splitAt(targetSid, sid, side) {
  const dir = side === "left" || side === "right" ? "row" : "col";
  const before = side === "left" || side === "top";
  S.layout = mapTree(S.layout, (l) => {
    if (l.sid !== targetSid) return l;
    const kids = before ? [{ sid }, l] : [l, { sid }];
    return { dir, kids, sizes: [0.5, 0.5] };
  });
  // flatten same-direction nesting so gutters stay simple
  S.layout = flatten(S.layout);
}

function flatten(n) {
  if (isLeaf(n)) return n;
  const kids = [];
  const sizes = [];
  n.kids.forEach((k, i) => {
    const f = flatten(k);
    if (!isLeaf(f) && f.dir === n.dir) {
      f.kids.forEach((kk, j) => {
        kids.push(kk);
        sizes.push(n.sizes[i] * f.sizes[j]);
      });
    } else {
      kids.push(f);
      sizes.push(n.sizes[i]);
    }
  });
  return { dir: n.dir, kids, sizes };
}

function saveLayout() {
  store.set("layout", S.layout);
  store.set("free", S.free);
  store.set("focused", S.focused);
}

export function renderLayout() {
  const root = $("#layout");
  const hold = $("#term-store");
  for (const t of S.terms.values()) hold.append(t.el);
  root.innerHTML = "";
  if (!leaves().includes(S.focused)) S.focused = leaves()[0] || null;
  // small screens and maximize show only the focused pane
  const single = mobile.matches || $("#app").classList.contains("maximized");
  const free = !single && isFree();
  if (free) root.append(buildFree());
  else if (S.focused) root.append(single ? buildPane(S.focused) : buildNode(S.layout));
  root.classList.toggle("free", free);
  $("#empty").hidden = leaves().length > 0;
  markFocus();
  saveLayout();
  clearUnread();
  fitVisible(true);
}

function buildNode(n) {
  if (isLeaf(n)) return buildPane(n.sid);
  const el = document.createElement("div");
  el.className = `split split-${n.dir}`;
  n.kids.forEach((k, i) => {
    if (i) el.append(buildGutter(n, i, el));
    const child = buildNode(k);
    child.style.flex = `${n.sizes[i]} 1 0`;
    el.append(child);
  });
  return el;
}

function buildGutter(node, i, splitEl) {
  const g = document.createElement("div");
  g.className = "gutter";
  g.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    g.setPointerCapture(e.pointerId);
    g.classList.add("dragging");
    const horizontal = node.dir === "row";
    const kids = [...splitEl.children].filter((c) => !c.classList.contains("gutter"));
    const a = kids[i - 1].getBoundingClientRect();
    const b = kids[i].getBoundingClientRect();
    const start = horizontal ? e.clientX : e.clientY;
    const sizeA = horizontal ? a.width : a.height;
    const sizeB = horizontal ? b.width : b.height;
    const share = node.sizes[i - 1] + node.sizes[i];
    const move = (ev) => {
      const delta = (horizontal ? ev.clientX : ev.clientY) - start;
      const min = 80;
      const na = Math.max(min, Math.min(sizeA + sizeB - min, sizeA + delta));
      node.sizes[i - 1] = (share * na) / (sizeA + sizeB);
      node.sizes[i] = share - node.sizes[i - 1];
      kids[i - 1].style.flex = `${node.sizes[i - 1]} 1 0`;
      kids[i].style.flex = `${node.sizes[i]} 1 0`;
      fitVisible();
    };
    const up = () => {
      g.classList.remove("dragging");
      g.removeEventListener("pointermove", move);
      saveLayout();
      fitVisible();
    };
    g.addEventListener("pointermove", move);
    g.addEventListener("pointerup", up, { once: true });
  });
  g.addEventListener("dblclick", () => {
    node.sizes = node.sizes.map(() => 1 / node.sizes.length);
    renderLayout();
  });
  return g;
}

let fitTimer = 0;
/** Debounced by default for continuous resize (drag); pass true for a one-shot structural
 * change (opening/closing/splitting a pane), where the 60ms coalescing delay just shows up
 * as the terminal snapping to size after the layout already moved. */
function fitVisible(immediate = false) {
  clearTimeout(fitTimer);
  const run = () => requestAnimationFrame(() => {
    for (const sid of leaves()) S.terms.get(sid)?.fit();
  });
  if (immediate) run();
  else fitTimer = setTimeout(run, 60);
}

function buildPane(sid) {
  const s = findSession(sid);
  const pane = document.createElement("div");
  pane.className = "pane";
  pane.dataset.sid = sid;
  pane.innerHTML = `<div class="pane-head" draggable="true" title="Drag to move or split">
      ${icon("terminal")}
      <span class="title">${esc(s ? sessionTitle(s) : sid)}<span class="cmd${S.terms.get(sid)?.exit ? " fail" : ""}" title="${esc(S.terms.get(sid)?.cmd || "")}">${esc(shortCmd(S.terms.get(sid)?.cmd))}</span><small>${esc(s?.project.name || "")}</small></span>
      <span class="ports">${portChips(sid)}</span>
      <button class="icon-btn sm" data-pane="max" title="Maximize (Ctrl+Alt+Enter)" aria-label="Maximize">${icon($("#app").classList.contains("maximized") ? "minimize" : "maximize")}</button>
      <button class="icon-btn sm" data-pane="menu" title="More" aria-label="More">${icon("more")}</button>
      <button class="icon-btn sm danger" data-pane="kill" title="Close terminal (Ctrl+Alt+Q)" aria-label="Close terminal">${icon("x")}</button>
    </div><div class="pane-body"></div>`;
  const head = pane.firstElementChild;
  head.addEventListener("click", (e) => {
    const port = e.target.closest("[data-port]")?.dataset.port;
    if (port) return window.open(`http://localhost:${port}`, "_blank", "noopener");
    const b = e.target.closest("[data-pane]");
    setFocus(sid);
    if (!b) return;
    if (b.dataset.pane === "max") toggleMaximize();
    if (b.dataset.pane === "kill") killSession(sid);
    if (b.dataset.pane === "menu") paneMenu(b, sid);
  });
  head.addEventListener("dblclick", (e) => {
    if (!e.target.closest("button")) toggleMaximize();
  });
  head.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("application/x-shelldeck-session", sid);
    e.dataTransfer.effectAllowed = "move";
  });
  pane.addEventListener("mousedown", () => setFocus(sid));
  paintPane(pane, sid);
  if (!isFree()) wireDrop(pane, sid);
  const term = getTerm(sid);
  term.mount(pane.querySelector(".pane-body"));
  new ResizeObserver(() => fitVisible()).observe(pane.querySelector(".pane-body"));
  return pane;
}

// ---------------------------------------------------------- free layout

let topZ = 1;

function buildFree() {
  const canvas = document.createElement("div");
  canvas.className = "free-canvas";
  let maxX = 0;
  let maxY = 0;
  for (const [sid, r] of Object.entries(S.free)) {
    topZ = Math.max(topZ, r.z || 1);
    const pane = buildPane(sid);
    pane.classList.add("floating");
    place(pane, r);
    wireFloating(pane, sid);
    canvas.append(pane);
    maxX = Math.max(maxX, r.x + r.w);
    maxY = Math.max(maxY, r.y + r.h);
  }
  const spacer = document.createElement("div");
  spacer.className = "free-spacer";
  spacer.style.left = `${maxX + 8}px`;
  spacer.style.top = `${maxY + 8}px`;
  canvas.append(spacer);
  return canvas;
}

function place(pane, r) {
  Object.assign(pane.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`, zIndex: r.z || 1 });
}

function raise(sid) {
  const r = S.free[sid];
  if (!r) return;
  r.z = ++topZ;
  const pane = $(`.pane.floating[data-sid="${sid}"]`);
  if (pane) pane.style.zIndex = r.z;
}

/** Pointer-drag the title to move, the corner grip to resize. */
function wireFloating(pane, sid) {
  const head = pane.querySelector(".pane-head");
  head.draggable = false;
  const grip = document.createElement("div");
  grip.className = "grip";
  grip.title = "Drag to resize";
  pane.append(grip);
  const drag = (el, apply) =>
    el.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button")) return;
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      raise(sid);
      const r = S.free[sid];
      const start = { x: e.clientX, y: e.clientY, r: { ...r } };
      let frame = 0;
      pane.classList.add("dragging");
      const move = (ev) => {
        apply(r, start.r, ev.clientX - start.x, ev.clientY - start.y);
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
          if (r.w === start.r.w && r.h === start.r.h) {
            pane.style.transform = `translate(${r.x - start.r.x}px, ${r.y - start.r.y}px)`;
          } else place(pane, r);
        });
      };
      el.addEventListener("pointermove", move);
      el.addEventListener(
        "pointerup",
        () => {
          el.removeEventListener("pointermove", move);
          cancelAnimationFrame(frame);
          pane.classList.remove("dragging");
          pane.style.transform = "";
          saveLayout();
          renderLayout();
          S.terms.get(sid)?.focus();
        },
        { once: true },
      );
    });
  drag(head, (r, o, dx, dy) => {
    r.x = Math.max(0, o.x + dx);
    r.y = Math.max(0, o.y + dy);
  });
  drag(grip, (r, o, dx, dy) => {
    r.w = Math.max(280, o.w + dx);
    r.h = Math.max(160, o.h + dy);
  });
}

function addFree(sid) {
  const box = $("#layout").getBoundingClientRect();
  const n = Object.keys(S.free).length;
  const w = Math.round(Math.min(760, Math.max(360, box.width * 0.55)));
  const h = Math.round(Math.min(460, Math.max(220, box.height * 0.55)));
  const step = 30;
  S.free[sid] = {
    x: 12 + ((n * step) % Math.max(step, box.width - w - 24)),
    y: 12 + ((n * step) % Math.max(step, box.height - h - 24)),
    w,
    h,
    z: ++topZ,
  };
}

/** Arrange free windows in a grid; the canvas scrolls when they don't fit. */
export function tileAll(render = true) {
  const sids = Object.keys(S.free);
  if (!sids.length) return;
  const box = $("#layout").getBoundingClientRect();
  const gap = 10;
  const W = Math.max(400, box.width - gap);
  const H = Math.max(300, box.height - gap);
  const cols = Math.max(1, Math.min(Math.ceil(Math.sqrt(sids.length)), Math.floor(W / 360)));
  const rows = Math.ceil(sids.length / cols);
  const w = Math.floor(W / cols) - gap;
  const h = Math.max(240, Math.floor(H / rows) - gap);
  sids.forEach((sid, i) => {
    S.free[sid] = { x: gap + (i % cols) * (w + gap), y: gap + Math.floor(i / cols) * (h + gap), w, h, z: 1 };
  });
  if (render) renderLayout();
}

function paneMenu(anchor, sid) {
  menu(anchor, [
    { label: "Rename", icon: "pencil", onClick: () => renameSession(sid) },
    { label: "Split right", icon: "split-h", hint: "Ctrl+Alt+\\", onClick: () => splitNew("row", sid) },
    { label: "Split down", icon: "split-v", hint: "Ctrl+Alt+-", onClick: () => splitNew("col", sid) },
    { label: "Hide pane (keep running)", icon: "eye", onClick: () => hidePane(sid) },
    { label: "Paste shared clipboard", icon: "clipboard", onClick: () => S.terms.get(sid)?.send({ type: "clipboard_get" }) },
    { label: "Copy selection to shared clipboard", icon: "clipboard", onClick: () => copyToShared(sid) },
    "sep",
    { label: "Close terminal", icon: "trash", danger: true, onClick: () => killSession(sid) },
  ]);
}

function copyToShared(sid) {
  const t = S.terms.get(sid);
  const text = t?.xterm.getSelection() || "";
  if (!text) return toast({ title: "Select text in the terminal first" });
  t.send({ type: "clipboard_set", data: text });
  toast({ title: "Copied to shared clipboard" });
}

// Drag a pane head or a sidebar session onto a pane: edges split, centre swaps.
function wireDrop(pane, sid) {
  let hint = null;
  const zoneOf = (e) => {
    const r = pane.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    const d = { left: x, right: 1 - x, top: y, bottom: 1 - y };
    const [side, dist] = Object.entries(d).sort((a, b) => a[1] - b[1])[0];
    return dist < 0.28 ? side : "center";
  };
  const clear = () => {
    hint?.remove();
    hint = null;
  };
  pane.addEventListener("dragover", (e) => {
    if (!e.dataTransfer.types.includes("application/x-shelldeck-session")) return;
    e.preventDefault();
    const zone = zoneOf(e);
    if (!hint) {
      hint = document.createElement("div");
      hint.className = "drop-hint";
      pane.append(hint);
    }
    const pos = { left: "0 50% 0 0", right: "0 0 0 50%", top: "0 0 50% 0", bottom: "50% 0 0 0", center: "0 0 0 0" }[zone];
    hint.style.inset = pos;
  });
  pane.addEventListener("dragleave", (e) => {
    if (!pane.contains(e.relatedTarget)) clear();
  });
  pane.addEventListener("drop", (e) => {
    const src = e.dataTransfer.getData("application/x-shelldeck-session");
    const zone = zoneOf(e);
    clear();
    if (!src || src === sid) return;
    e.preventDefault();
    const inLayout = leaves().includes(src);
    if (zone === "center") {
      if (inLayout) {
        S.layout = mapTree(S.layout, (l) => (l.sid === sid ? { sid: src } : l.sid === src ? { sid } : l));
      } else replaceLeaf(sid, src);
    } else {
      if (inLayout) removeLeaf(src);
      splitAt(sid, src, zone);
    }
    S.focused = src;
    renderLayout();
    S.terms.get(src)?.focus();
  });
}

function markFocus() {
  for (const p of $$(".pane")) p.classList.toggle("focused", p.dataset.sid === S.focused);
  renderCrumbs();
  renderSidebarActive();
}

export function setFocus(sid) {
  if (isFree()) raise(sid);
  if (S.focused === sid) return;
  S.focused = sid;
  markFocus();
  saveLayout();
}

function toggleMaximize(force) {
  const app = $("#app");
  app.classList.toggle("maximized", force);
  renderLayout();
  S.terms.get(S.focused)?.focus();
}

/** Show a session: focus if visible, else open it in the focused pane (or split with `split`). */
export function showSession(sid, { split = null } = {}) {
  switchView("terminals");
  if (mobile.matches) $("#app").classList.remove("sb-mobile-open");
  if (isFree()) {
    if (!S.free[sid]) addFree(sid);
    raise(sid);
  } else if (leaves().includes(sid)) {
    S.focused = sid;
  } else if (!S.layout) {
    S.layout = { sid };
  } else if (split) {
    splitAt(S.focused || leaves()[0], sid, split === "row" ? "right" : "bottom");
  } else {
    replaceLeaf(S.focused || leaves()[0], sid);
  }
  S.focused = sid;
  renderLayout();
  const t = S.terms.get(sid);
  setTimeout(() => t?.focus(), 0);
  return t;
}

function autoSplitDir() {
  // Split the largest pane on its long side; panes have min sizes, so #layout scrolls past that.
  if (mobile.matches) return null;
  let best = null;
  for (const p of $$("#layout .pane[data-sid]")) {
    const r = p.getBoundingClientRect();
    if (!best || r.width * r.height > best.area) best = { sid: p.dataset.sid, area: r.width * r.height, r };
  }
  if (!best) return null;
  S.focused = best.sid;
  // only split sideways while both halves stay wide; otherwise stack (vertical scroll only)
  return best.r.width >= 760 && best.r.width >= best.r.height * 1.3 ? "row" : "col";
}

export async function newTerminal(projectId, shell = "", { split } = {}) {
  const project = S.projects.find((p) => p.id === projectId) || focusedProject();
  if (!project) return views.addProjectDialog();
  try {
    const s = await api("/api/sessions", { method: "POST", body: { project_id: project.id, shell } });
    await refreshProjects();
    S.collapsed.delete(project.id);
    return showSession(s.id, { split: split === undefined ? autoSplitDir() : split });
  } catch (e) {
    toastError(e);
  }
}

function splitNew(dir, sid = S.focused) {
  const s = sid && findSession(sid);
  if (sid) S.focused = sid;
  return newTerminal(s?.project.id, s?.shell || "", { split: dir });
}

function hidePane(sid) {
  removeLeaf(sid);
  renderLayout();
}

async function killSession(sid) {
  const t = S.terms.get(sid);
  if (t) t.dispose();
  removeSessionLocally(sid);
  try {
    await api(`/api/sessions/${sid}`, { method: "DELETE" });
  } catch (e) {
    toastError(e);
  }
  refreshProjects();
}

function removeSessionLocally(sid) {
  removeLeaf(sid);
  for (const p of S.projects) p.sessions = p.sessions.filter((s) => s.id !== sid);
  renderLayout();
  renderSidebar();
  S.terms.get(S.focused)?.focus();
}

async function renameSession(sid) {
  const s = findSession(sid);
  const name = await promptDialog("Rename terminal", s ? sessionTitle(s) : "");
  if (!name) return;
  try {
    await api(`/api/sessions/${sid}`, { method: "PATCH", body: { name } });
    await refreshProjects();
    renderLayout();
  } catch (e) {
    toastError(e);
  }
}

function focusDirection(dir) {
  const cur = $(`.pane[data-sid="${S.focused}"]`);
  if (!cur) return;
  const a = cur.getBoundingClientRect();
  const ac = { x: a.left + a.width / 2, y: a.top + a.height / 2 };
  let best = null;
  for (const p of $$(".pane")) {
    if (p === cur) continue;
    const b = p.getBoundingClientRect();
    const bc = { x: b.left + b.width / 2, y: b.top + b.height / 2 };
    const dx = bc.x - ac.x;
    const dy = bc.y - ac.y;
    const ok = { left: dx < -1, right: dx > 1, up: dy < -1, down: dy > 1 }[dir];
    if (!ok) continue;
    const dist = Math.hypot(dx, dy);
    if (!best || dist < best.dist) best = { sid: p.dataset.sid, dist };
  }
  if (best) {
    setFocus(best.sid);
    S.terms.get(best.sid)?.focus();
  }
}

// ---------------------------------------------------------------- sidebar

export function orderedProjects() {
  const idx = (id) => {
    const i = S.order.indexOf(id);
    return i < 0 ? Infinity : i;
  };
  return [...S.projects].sort((a, b) => idx(a.id) - idx(b.id) || a.name.localeCompare(b.name));
}

export async function refreshProjects() {
  try {
    const { projects } = await api("/api/projects");
    S.projects = projects;
    setOnline(true);
  } catch (e) {
    if (e.message !== "locked") setOnline(false);
    return;
  }
  // drop panes whose sessions are gone
  const known = new Set(allSessions().map((s) => s.id));
  const before = leaves().length;
  S.layout = mapTree(S.layout, (l) => (known.has(l.sid) ? l : null));
  for (const sid of Object.keys(S.free)) if (!known.has(sid)) delete S.free[sid];
  for (const [sid, t] of S.terms) if (!known.has(sid)) t.dispose();
  if (leaves().length !== before) renderLayout();
  renderSidebar();
  renderCrumbs();
}

function setOnline(online) {
  S.online = online;
  const c = $("#conn");
  c.classList.toggle("down", !online);
  c.title = online ? "Connected to server" : "Server unreachable";
}

const seenRows = new Set();
const seenPanes = new Set();
let firstSidebar = true;

export function renderSidebar() {
  const nav = $("#projects");
  if (!firstSidebar) nav.classList.add("settled");
  const visible = new Set(leaves());
  const projects = orderedProjects();
  if (!projects.length) {
    nav.innerHTML = `<div class="sb-empty">No projects yet. Add a folder to get started.</div>`;
    return;
  }
  nav.innerHTML = projects
    .map(
      (p, i) => `<div style="--i:${i}" class="proj ${S.collapsed.has(p.id) ? "collapsed" : ""} ${p.exists ? "" : "missing"}" data-pid="${p.id}">
      <div class="proj-row" draggable="true" data-act="toggle" title="${esc(p.path)}">
        <span class="chev">${icon("chevron")}</span>
        <span class="proj-dot" style="background:${projectColor(p)}"></span>
        <span class="name">${esc(p.name)}</span>
        <span class="count">${p.sessions.length || ""}</span>
        <span class="row-actions">
          ${p.has_git ? `<button class="icon-btn sm" data-act="git" title="Git graph" aria-label="Git graph">${icon("git-branch")}</button>` : ""}
          <button class="icon-btn sm" data-act="new" title="New ${esc(shellLabel(""))} terminal" aria-label="New terminal">${icon("plus")}</button>
          <button class="icon-btn sm" data-act="menu" title="More" aria-label="Project actions">${icon("more")}</button>
        </span>
      </div>
      <div class="sessions"><div class="sessions-inner">
        ${p.sessions
          .map(
            (s) => `<div class="sess-row ${S.unread.has(s.id) ? "unread" : ""} ${visible.has(s.id) ? "visible" : ""} ${s.id === S.focused && S.view === "terminals" ? "active" : ""}" data-sid="${s.id}" draggable="true" data-act="open" title="${esc(s.cwd || "")}">
              <span class="dot ${s.alive ? "alive" : ""}"></span>
              <span class="name">${esc(sessionTitle(s))}</span>
              <span class="badge">${esc(shellLabel(s.shell))}</span>
              <span class="row-actions">
                <button class="icon-btn sm" data-act="smenu" title="More" aria-label="Terminal actions">${icon("more")}</button>
                <button class="icon-btn sm danger" data-act="kill" title="Close terminal" aria-label="Close terminal">${icon("x")}</button>
              </span>
            </div>`,
          )
          .join("")}
      </div></div>
    </div>`,
    )
    .join("");
  markSeen();
}

function markSeen() {
  for (const p of S.projects) for (const s of p.sessions) seenRows.add(s.id);
  firstSidebar = false;
}

function renderSidebarActive() {
  const visible = new Set(leaves());
  for (const r of $$(".sess-row")) {
    r.classList.toggle("active", r.dataset.sid === S.focused && S.view === "terminals");
    r.classList.toggle("visible", visible.has(r.dataset.sid));
  }
}

function projectMenu(anchor, p) {
  const shellItems = S.shells.available.map((k) => ({
    label: `New ${shellLabel(k)} terminal`,
    icon: "terminal",
    hint: k === S.settings.default_shell ? "default" : "",
    onClick: () => newTerminal(p.id, k),
  }));
  menu(anchor, [
    ...shellItems,
    "sep",
    { label: "Rename project", icon: "pencil", onClick: () => renameProject(p) },
    { label: "Copy path", icon: "clipboard", onClick: () => navigator.clipboard.writeText(p.path) },
    "sep",
    { label: "Remove project", icon: "trash", danger: true, onClick: () => removeProject(p) },
  ]);
}

async function renameProject(p) {
  const name = await promptDialog("Rename project", p.name);
  if (!name) return;
  try {
    await api(`/api/projects/${p.id}`, { method: "PATCH", body: { name } });
    refreshProjects();
  } catch (e) {
    toastError(e);
  }
}

async function removeProject(p) {
  const n = p.sessions.length;
  const ok = await confirmDialog(`Remove "${p.name}" from shelldeck?${n ? ` Its ${n} terminal${n > 1 ? "s" : ""} will be closed.` : ""} Files on disk are not touched.`, { ok: "Remove", danger: true });
  if (!ok) return;
  for (const s of p.sessions) S.terms.get(s.id)?.dispose();
  for (const s of p.sessions) removeLeaf(s.id);
  try {
    await api(`/api/projects/${p.id}`, { method: "DELETE" });
  } catch (e) {
    toastError(e);
  }
  renderLayout();
  refreshProjects();
}

function wireSidebar() {
  const nav = $("#projects");
  nav.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (!act) return;
    const pid = e.target.closest(".proj")?.dataset.pid;
    const sid = e.target.closest(".sess-row")?.dataset.sid;
    const p = S.projects.find((x) => x.id === pid);
    switch (act.dataset.act) {
      case "toggle":
        S.collapsed.has(pid) ? S.collapsed.delete(pid) : S.collapsed.add(pid);
        store.set("collapsed", [...S.collapsed]);
        act.closest(".proj").classList.toggle("collapsed");
        break;
      case "new":
        e.stopPropagation();
        newTerminal(pid);
        break;
      case "git":
        e.stopPropagation();
        showGitGraph(p);
        break;
      case "menu":
        e.stopPropagation();
        projectMenu(act, p);
        break;
      case "open":
        showSession(sid, { split: e.ctrlKey || e.metaKey ? "row" : null });
        break;
      case "smenu":
        e.stopPropagation();
        menu(act, [
          { label: "Open in split", icon: "split-h", onClick: () => showSession(sid, { split: "row" }) },
          { label: "Rename", icon: "pencil", onClick: () => renameSession(sid) },
          "sep",
          { label: "Close terminal", icon: "trash", danger: true, onClick: () => killSession(sid) },
        ]);
        break;
      case "kill":
        e.stopPropagation();
        killSession(sid);
        break;
    }
  });
  nav.addEventListener("auxclick", (e) => {
    const sid = e.target.closest(".sess-row")?.dataset.sid;
    if (e.button === 1 && sid) showSession(sid, { split: "row" });
  });
  nav.addEventListener("contextmenu", (e) => {
    const sid = e.target.closest(".sess-row")?.dataset.sid;
    const pid = e.target.closest(".proj")?.dataset.pid;
    if (!pid) return;
    e.preventDefault();
    if (sid) {
      menu({ x: e.clientX, y: e.clientY }, [
        { label: "Open in split", icon: "split-h", onClick: () => showSession(sid, { split: "row" }) },
        { label: "Rename", icon: "pencil", onClick: () => renameSession(sid) },
        "sep",
        { label: "Close terminal", icon: "trash", danger: true, onClick: () => killSession(sid) },
      ]);
    } else projectMenu({ x: e.clientX, y: e.clientY }, S.projects.find((x) => x.id === pid));
  });

  // drag: sessions onto panes, projects to reorder
  let dragPid = null;
  nav.addEventListener("dragstart", (e) => {
    const sid = e.target.closest(".sess-row")?.dataset.sid;
    if (sid) {
      e.dataTransfer.setData("application/x-shelldeck-session", sid);
      e.dataTransfer.effectAllowed = "move";
      return;
    }
    dragPid = e.target.closest(".proj")?.dataset.pid;
    e.dataTransfer.setData("text/plain", dragPid || "");
    e.dataTransfer.effectAllowed = "move";
  });
  nav.addEventListener("dragover", (e) => {
    if (!dragPid) return;
    e.preventDefault();
    $$(".proj", nav).forEach((p) => p.classList.remove("drop-before"));
    e.target.closest(".proj")?.classList.add("drop-before");
  });
  nav.addEventListener("drop", (e) => {
    if (!dragPid) return;
    e.preventDefault();
    const target = e.target.closest(".proj")?.dataset.pid;
    const ids = orderedProjects().map((p) => p.id).filter((id) => id !== dragPid);
    const at = target ? ids.indexOf(target) : ids.length;
    ids.splice(at < 0 ? ids.length : at, 0, dragPid);
    S.order = ids;
    store.set("order", ids);
    renderSidebar();
  });
  nav.addEventListener("dragend", () => {
    dragPid = null;
    $$(".proj", nav).forEach((p) => p.classList.remove("drop-before"));
  });

  // resizable sidebar
  const handle = $(".sb-resize");
  const app = $("#app");
  const w = store.get("sidebarWidth", 264);
  app.style.setProperty("--sb-w", `${w}px`);
  if (store.get("sidebarHidden", false)) app.classList.add("sb-hidden");
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    const move = (ev) => {
      const width = Math.max(200, Math.min(480, ev.clientX));
      app.style.setProperty("--sb-w", `${width}px`);
      fitVisible();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener(
      "pointerup",
      () => {
        handle.classList.remove("dragging");
        handle.removeEventListener("pointermove", move);
        store.set("sidebarWidth", parseInt(getComputedStyle(app).getPropertyValue("--sb-w"), 10));
      },
      { once: true },
    );
  });
}

function toggleSidebar() {
  const app = $("#app");
  if (mobile.matches) {
    app.classList.toggle("sb-mobile-open");
    return;
  }
  app.classList.toggle("sb-hidden");
  store.set("sidebarHidden", app.classList.contains("sb-hidden"));
  fitVisible();
}

// ------------------------------------------------------------- top bar/views

const VIEW_TITLES = { bookmarks: "Bookmarks", scheduler: "Scheduler", tasks: "Tasks", monitor: "Task manager", history: "Command history" };

export function switchView(view) {
  if (S.view === view) return;
  S.view = view;
  for (const v of ["terminals", "bookmarks", "scheduler", "tasks", "monitor", "history"]) $(`#view-${v}`).hidden = v !== view;
  for (const b of $$(".sb-link[data-view]")) b.classList.toggle("active", b.dataset.view === view && view !== "terminals");
  $("#term-actions").hidden = view !== "terminals";
  if (mobile.matches) $("#app").classList.remove("sb-mobile-open");
  if (view === "terminals") {
    clearUnread();
    fitVisible();
    S.terms.get(S.focused)?.focus();
  } else views.show(view, $(`#view-${view}`));
  renderCrumbs();
  renderSidebarActive();
}

function renderCrumbs() {
  const c = $("#crumbs");
  if (S.view !== "terminals") {
    c.innerHTML = `<b>${esc(VIEW_TITLES[S.view])}</b>`;
    return;
  }
  const s = S.focused && findSession(S.focused);
  c.innerHTML = s
    ? `<span>${esc(s.project.name)}</span><span class="sep">/</span><b>${esc(sessionTitle(s))}</b><span class="path">${esc(s.cwd || s.project.path)}</span>`
    : `<b>Terminals</b>`;
}

// --------------------------------------------------------------- palette

function fuzzy(q, text) {
  if (!q) return 1;
  text = text.toLowerCase();
  let score = 0;
  let pos = 0;
  for (const ch of q.toLowerCase()) {
    const i = text.indexOf(ch, pos);
    if (i < 0) return 0;
    score += i === pos ? 2 : 1;
    pos = i + 1;
  }
  return score + (text.includes(q.toLowerCase()) ? 10 : 0);
}

/** Generic palette. items: {group, label, hint, icon, run(e)} ; multi: allow checking several. */
export function palette({ placeholder = "Type a command or search…", items, multi = null, footer = "" }) {
  const d = dialog({
    cls: "palette",
    body: `<input type="search" placeholder="${esc(placeholder)}" autofocus aria-label="Search" /><div class="palette-list" role="listbox"></div>${footer ? `<div class="palette-foot">${footer}</div>` : ""}`,
  });
  d.el.querySelector(".dialog-body").style.padding = "0";
  const input = $("input", d.el);
  const list = $(".palette-list", d.el);
  const checked = new Set();
  let shown = [];
  let sel = 0;
  const render = () => {
    const q = input.value.trim();
    shown = items
      .map((it) => ({ it, score: fuzzy(q, `${it.label} ${it.hint || ""} ${it.group || ""}`) }))
      .filter((x) => x.score > 0)
      .sort((a, b) => (q ? b.score - a.score : 0))
      .map((x) => x.it);
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    let group = null;
    list.innerHTML = shown.length
      ? shown
          .map((it, i) => {
            const head = !q && it.group && it.group !== group ? `<div class="palette-group">${esc((group = it.group))}</div>` : "";
            return `${head}<div class="palette-item ${i === sel ? "sel" : ""}" data-i="${i}" role="option">
              ${multi ? `<input type="checkbox" tabindex="-1" ${checked.has(it) ? "checked" : ""} />` : ""}
              ${icon(it.icon || "terminal")}<span class="label">${esc(it.label)}</span><span class="hint">${esc(it.hint || "")}</span></div>`;
          })
          .join("")
      : `<div class="placeholder">No matches</div>`;
    list.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
  };
  const run = (e) => {
    const picked = multi && checked.size ? [...checked] : shown[sel] ? [shown[sel]] : [];
    if (!picked.length) return;
    d.close();
    if (multi) multi(picked, e);
    else picked[0].run(e);
  };
  input.addEventListener("input", () => {
    sel = 0;
    render();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + shown.length) % Math.max(1, shown.length);
      render();
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(e);
    } else if (e.key === " " && multi && !input.value) {
      e.preventDefault();
      const it = shown[sel];
      if (it) checked.has(it) ? checked.delete(it) : checked.add(it);
      render();
    }
  });
  list.addEventListener("mousemove", (e) => {
    const el = e.target.closest(".palette-item");
    if (el && +el.dataset.i !== sel) {
      sel = +el.dataset.i;
      $$(".palette-item", list).forEach((x) => x.classList.toggle("sel", +x.dataset.i === sel));
    }
  });
  list.addEventListener("click", (e) => {
    const el = e.target.closest(".palette-item");
    if (!el) return;
    sel = +el.dataset.i;
    const it = shown[sel];
    if (multi && (e.ctrlKey || e.metaKey || e.target.type === "checkbox")) {
      checked.has(it) ? checked.delete(it) : checked.add(it);
      render();
      return;
    }
    run(e);
  });
  render();
  return d;
}

function commandPalette() {
  const items = [];
  for (const s of allSessions()) {
    items.push({ group: "Terminals", label: sessionTitle(s), hint: s.project.name, icon: "terminal", run: () => showSession(s.id) });
  }
  for (const p of orderedProjects()) {
    items.push({ group: "New terminal", label: `New terminal in ${p.name}`, hint: shellLabel(""), icon: "plus", run: () => newTerminal(p.id) });
  }
  for (const b of views.bookmarkCache()) {
    items.push({ group: "Bookmarks", label: b.name, hint: b.command, icon: "bookmark", run: (e) => views.runBookmarks([b], { execute: e?.shiftKey }) });
  }
  const cmds = [
    ["Add project…", "folder-plus", () => views.addProjectDialog()],
    ["Split right", "split-h", () => splitNew("row"), "Ctrl+Alt+\\"],
    ["Split down", "split-v", () => splitNew("col"), "Ctrl+Alt+-"],
    ["Toggle maximize pane", "maximize", () => toggleMaximize(), "Ctrl+Alt+Enter"],
    ["Layout: tiled (fit window)", "split-h", () => views.saveSettings({ layout_mode: "tiled" })],
    ["Layout: free windows", "grid", () => views.saveSettings({ layout_mode: "free" })],
    ["Tile all windows", "grid", () => (isFree() ? tileAll() : toast({ title: "Tiling applies to the free layout" }))],
    ["Toggle sidebar", "panel", () => toggleSidebar(), "Ctrl+Alt+E"],
    ["Search all terminals", "search", () => searchAllDialog(), "Ctrl+Shift+F"],
    ["Command history", "history", () => switchView("history"), "Ctrl+Alt+R"],
    ["Task manager", "activity", () => switchView("monitor"), "Ctrl+Alt+M"],
    ["Open terminals", "terminal", () => switchView("terminals")],
    ["Open bookmarks", "bookmark", () => switchView("bookmarks")],
    ["Open scheduler", "clock", () => switchView("scheduler"), "Ctrl+Alt+S"],
    ["Open tasks", "kanban", () => switchView("tasks"), "Ctrl+Alt+T"],
    ["Settings", "settings", () => views.settingsDialog(), "Ctrl+Alt+,"],
    ["Toggle light/dark theme", "sun", () => views.saveSettings({ theme: resolvedTheme() === "dark" ? "light" : "dark" })],
    ["Keyboard shortcuts", "keyboard", () => shortcutsDialog(), "Ctrl+Alt+H"],
    ["Lock", "lock", () => lockNow(), "Ctrl+Alt+L"],
  ];
  for (const [label, ic, run, hint] of cmds) items.push({ group: "Commands", label, icon: ic, run, hint });
  palette({ items, footer: "↑↓ to navigate · Enter to open · Shift+Enter runs a bookmark · Esc to close" });
}

function shortcutsDialog() {
  const rows = [
    ["Ctrl+Shift+P", "Command palette (Ctrl+K outside terminals)"],
    ["Ctrl+Alt+N", "New terminal in current project"],
    ["Ctrl+Alt+\\  /  Ctrl+Alt+-", "Split right / down"],
    ["Ctrl+Alt+Arrows", "Focus pane in direction"],
    ["Ctrl+Alt+1…9", "Focus pane 1–9"],
    ["Ctrl+Alt+Enter", "Maximize / restore pane"],
    ["Ctrl+Alt+Q", "Close focused terminal"],
    ["Ctrl+Alt+B", "Bookmark picker (Space selects several)"],
    ["Ctrl+Alt+E", "Toggle sidebar"],
    ["Ctrl+Alt+S  /  Ctrl+Alt+T", "Scheduler / Tasks"],
    ["Ctrl+Alt+M", "Task manager (CPU, RAM, GPU per terminal)"],
    ["Ctrl+Alt+R", "Command history"],
    ["Ctrl+Shift+F", "Search all terminals"],
    ["Ctrl+Alt+,", "Settings"],
    ["Ctrl+Alt+L", "Lock"],
    ["Ctrl+C  /  Ctrl+V", "Copy selection / paste"],
    ["Ctrl+F", "Find in terminal (Enter / Shift+Enter: next / previous)"],
    ["Ctrl+Shift+Up  /  Down", "Jump to previous / next prompt"],
    ["Drag pane title", "Move pane; drop on an edge to split"],
    ["Ctrl+click terminal", "Open in split (sidebar)"],
  ];
  dialog({
    title: "Keyboard shortcuts",
    body: `<table style="width:100%;border-collapse:collapse">${rows
      .map(([k, v]) => `<tr><td style="padding:5px 12px 5px 0;white-space:nowrap"><kbd>${esc(k)}</kbd></td><td class="muted">${esc(v)}</td></tr>`)
      .join("")}</table>`,
  });
}

// -------------------------------------------------------------- keyboard

function isAppShortcut(e) {
  const k = e.key.toLowerCase();
  if (e.ctrlKey && e.shiftKey && !e.altKey && (k === "p" || k === "f")) return true;
  if (e.ctrlKey && e.altKey && !e.shiftKey) return /^(n|b|e|s|t|m|r|l|h|q|,|\\|-|enter|arrow(left|right|up|down)|[1-9])$/.test(k);
  return false;
}

function onKey(e) {
  if (!$("#lock").hidden) return;
  const k = e.key.toLowerCase();
  const inTerm = !!e.target.closest?.(".xterm");
  if (e.ctrlKey && !e.altKey && !e.shiftKey && k === "k" && !inTerm && !topDialog()) {
    e.preventDefault();
    return commandPalette();
  }
  if (!isAppShortcut(e) || topDialog()) return;
  e.preventDefault();
  e.stopPropagation();
  if (e.shiftKey && k === "p") return commandPalette();
  if (e.shiftKey && k === "f") return searchAllDialog();
  const map = {
    n: () => newTerminal(),
    b: () => views.bookmarkPicker(),
    e: () => toggleSidebar(),
    s: () => switchView("scheduler"),
    t: () => switchView("tasks"),
    m: () => switchView(S.view === "monitor" ? "terminals" : "monitor"),
    r: () => switchView(S.view === "history" ? "terminals" : "history"),
    l: () => lockNow(),
    h: () => shortcutsDialog(),
    q: () => S.focused && killSession(S.focused),
    ",": () => views.settingsDialog(),
    "\\": () => splitNew("row"),
    "-": () => splitNew("col"),
    enter: () => toggleMaximize(),
    arrowleft: () => focusDirection("left"),
    arrowright: () => focusDirection("right"),
    arrowup: () => focusDirection("up"),
    arrowdown: () => focusDirection("down"),
  };
  if (/^[1-9]$/.test(k)) {
    const sid = leaves()[+k - 1];
    if (sid) {
      switchView("terminals");
      setFocus(sid);
      S.terms.get(sid)?.focus();
    }
    return;
  }
  closeMenu();
  map[k]?.();
}

// ------------------------------------------------------------- lock/alarms

async function lockNow() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (e) {
    return toastError(e);
  }
  showAuth();
}

let authMode = null; // "login" | "setup" while the lock screen is up

/** Lock screen: log in, or create the first password. */
async function showAuth(status = null) {
  status ||= await api("/api/auth/status").catch(() => ({ has_password: true }));
  const mode = status.has_password ? "login" : "setup";
  if (!$("#lock").hidden && authMode === mode) return;
  authMode = mode;
  const form = $("#lock-form");
  form.innerHTML =
    mode === "setup"
      ? `<img src="/static/icon.svg" alt="" width="40" height="40" />
      <h2>Create a password</h2>
      <p class="muted">shelldeck needs a password before it opens. You'll use it to unlock the app in any browser, including over the network.</p>
      <input type="password" name="password" placeholder="New password (8+ characters)" autocomplete="new-password" minlength="${status.min_length || 8}" required />
      <input type="password" name="confirm" placeholder="Confirm password" autocomplete="new-password" required />
      ${status.setup_code_required ? `<input type="text" name="code" placeholder="Setup code from the host" autocomplete="off" spellcheck="false" required />
      <p class="faint small">You are connecting from another machine. The setup code is printed where shelldeck runs, and saved in its data folder as <code>setup-code</code>.</p>` : ""}
      <div class="error" id="lock-error"></div>
      <button class="btn primary" type="submit">Create password</button>`
      : `<img src="/static/icon.svg" alt="" width="40" height="40" />
      <h2>shelldeck is locked</h2>
      <input type="password" name="password" placeholder="Password" autocomplete="current-password" required />
      <div class="error" id="lock-error"></div>
      <button class="btn primary" type="submit">Unlock</button>
      <p class="faint small">Forgot it? On the machine running shelldeck, run <code>shelldeck login-link</code> or <code>shelldeck reset-password</code>.</p>`;
  withEyes(form);
  $("#lock").hidden = false;
  form.querySelector("input").focus();
}

function wireLock() {
  bus.addEventListener("locked", () => showAuth());
  $("#lock-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      if (authMode === "setup") {
        await api("/api/auth/setup", { method: "POST", body: { password: f.password.value, confirm: f.confirm.value, code: f.code?.value || "" } });
      } else {
        await api("/api/auth/login", { method: "POST", body: { password: f.password.value } });
      }
      location.reload();
    } catch (err) {
      $("#lock-error").textContent = authError(err);
      btn.disabled = false;
    }
  });
}

let alarmSocket = null;
function connectAlarms() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/alarms`);
  alarmSocket = ws;
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "alarm") views.showAlarm(msg.task, true);
    if (msg.type === "alarm_snapshot") msg.alarms.forEach((t) => views.showAlarm(t, false));
  };
  ws.onclose = (ev) => {
    if (ev.code !== 1008) setTimeout(connectAlarms, 3000);
  };
}

// ----------------------------------------------------------------- init

function wireGlobal() {
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-action]");
    if (!a) return;
    const act = a.dataset.action;
    if (act === "toggle-sidebar") toggleSidebar();
    else if (act === "new-terminal") newTerminal();
    else if (act === "palette") commandPalette();
    else if (act === "add-project") views.addProjectDialog();
    else if (act === "view") switchView(S.view === a.dataset.view ? "terminals" : a.dataset.view);
    else if (act === "settings") views.settingsDialog();
    else if (act === "split") splitNew(a.dataset.dir);
    else if (act === "bookmark-picker") views.bookmarkPicker();
    else if (act === "tile") tileAll();
  });
  window.addEventListener("resize", () => fitVisible());
  const snapshotAll = () => S.terms.forEach((t) => t.snapshot());
  setInterval(() => S.terms.forEach((t) => t.heartbeat()), 15000);
  const reconnectAll = () => S.terms.forEach((t) => t.reconnectNow());
  window.addEventListener("online", reconnectAll);
  document.addEventListener("visibilitychange", () => !document.hidden && reconnectAll());
  setInterval(snapshotAll, 15000);
  document.addEventListener("visibilitychange", () => document.hidden && snapshotAll());
  window.addEventListener("pagehide", snapshotAll);
  mobile.addEventListener("change", () => {
    $("#app").classList.remove("sb-mobile-open");
    renderLayout();
  });
}

const bootStart = performance.now();

/** Resolves when the boot animation has had its time; heavy DOM work waits for this. */
function finishBoot() {
  const boot = $("#boot");
  if (!boot) return Promise.resolve();
  const wait = Math.max(0, 2000 - (performance.now() - bootStart));
  return new Promise((resolve) =>
    setTimeout(() => {
      boot.classList.add("done");
      setTimeout(() => boot.remove(), 600);
      requestAnimationFrame(() => resolve());
    }, wait),
  );
}

async function init() {
  hydrateIcons();
  wireGlobal();
  wireSidebar();
  wireLock();
  views.init();
  const status = await api("/api/auth/status").catch(() => null);
  if (!status?.authenticated) {
    await finishBoot();
    return showAuth(status);
  }
  try {
    [S.settings, S.shells] = await Promise.all([api("/api/settings"), api("/api/shells")]);
  } catch (e) {
    toastError(e);
  }
  applySettings();
  await refreshProjects();
  startMonitor();
  await finishBoot();
  // `?session=<id>` from `shelldeck open`
  const want = new URLSearchParams(location.search).get("session");
  if (want && findSession(want)) {
    history.replaceState(null, "", "/");
    showSession(want, { split: S.layout && !leaves().includes(want) ? autoSplitDir() : null });
  } else {
    renderLayout();
    S.terms.get(S.focused)?.focus();
  }
  views.loadBookmarks();
  connectAlarms();
  setInterval(refreshProjects, 5000);
}

init();
