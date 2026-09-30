// Built-in file editor and viewer: `sd edit` / `sd view`, clickable paths (editor setting "shelldeck"),
// and "Open file…" in the command palette. A textarea with line numbers, not an IDE.
import { $, api, confirmDialog, dialog, esc, icon, toast, toastError } from "./ui.js";
import { markdown } from "./md.js";

const isMd = (p) => /\.(md|markdown|mdx)$/i.test(p);
const baseName = (p) => p.split(/[\\/]/).pop();
const fmtSize = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);

export async function openFile(path, { sid = "", mode = "edit", line = 0 } = {}) {
  const q = `path=${encodeURIComponent(path)}&session_id=${encodeURIComponent(sid)}`;
  let f;
  try {
    f = await api(`/api/fs/file?${q}`);
  } catch (e) {
    return toastError(e);
  }
  const md = isMd(f.path);
  let view = f.image ? "image" : mode === "view" && md ? "preview" : "text";
  let editing = mode === "edit" && !f.readonly && !f.image;
  let saved = f.text ?? "";
  const info = f.image ? fmtSize(f.size) : [f.new ? "new file" : fmtSize(f.size), "UTF-8" + (f.bom ? " BOM" : ""), f.crlf ? "CRLF" : "LF", f.readonly ? "read-only (not UTF-8)" : ""].filter(Boolean).join(" · ");
  const d = dialog({
    title: baseName(f.path),
    wide: true,
    cls: "editor-dialog",
    body: `<div class="ed-bar"><span class="ed-path" title="${esc(f.path)}">${esc(f.path)}</span>
        ${md ? `<div class="seg" role="group" aria-label="View"><button data-view="text">Source</button><button data-view="preview">Preview</button></div>` : ""}
      </div>
      <div class="ed-main">
        <pre class="ed-gutter" aria-hidden="true"></pre>
        <textarea class="ed-text" spellcheck="false" wrap="off" aria-label="File contents"></textarea>
        <div class="ed-preview md" tabindex="0"></div>
        ${f.image ? `<div class="ed-image"><img alt="${esc(baseName(f.path))}" src="/api/fs/raw?${q}"></div>` : ""}
      </div>`,
    foot: `<span class="ed-info muted">${esc(info)}</span><span class="ed-dirty" hidden>unsaved</span>
      ${f.image || f.readonly ? "" : `<button class="btn" data-ed="edit">${icon("pencil")}Edit</button><button class="btn primary" data-ed="save">Save <kbd>Ctrl S</kbd></button>`}`,
  });
  const ta = $(".ed-text", d.el);
  const gutter = $(".ed-gutter", d.el);
  ta.value = saved;
  const dirty = () => ta.value !== saved;
  const numbers = () => {
    const n = ta.value.split("\n").length;
    if (gutter.dataset.n !== String(n)) {
      gutter.dataset.n = n;
      gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
    }
  };
  const paint = () => {
    ta.readOnly = !editing;
    d.el.dataset.view = view;
    d.el.classList.toggle("editing", editing);
    $(".ed-dirty", d.el).hidden = !dirty();
    for (const b of d.el.querySelectorAll("[data-view]")) b.classList.toggle("on", b.dataset.view === view);
    if (view === "preview") $(".ed-preview", d.el).innerHTML = markdown(ta.value);
    numbers();
  };
  const save = async (force = false) => {
    if (!editing) return;
    try {
      const r = await api("/api/fs/file", { method: "PUT", body: { path: f.path, text: ta.value, mtime: f.mtime, crlf: f.crlf, bom: f.bom, force } });
      f.mtime = r.mtime;
      saved = ta.value;
      paint();
      toast({ title: "Saved", body: baseName(f.path), timeout: 1500 });
    } catch (e) {
      if (e.message !== "changed_on_disk") return toastError(e);
      if (await confirmDialog(`${baseName(f.path)} changed on disk since it was opened. Overwrite it with your version?`, { ok: "Overwrite", danger: true })) save(true);
    }
  };
  d.canClose = () => {
    if (!dirty()) return true;
    confirmDialog(`Discard unsaved changes to ${baseName(f.path)}?`, { ok: "Discard", danger: true }).then((ok) => ok && d.close(true));
    return false;
  };
  ta.addEventListener("input", paint);
  ta.addEventListener("scroll", () => (gutter.scrollTop = ta.scrollTop));
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Tab" && !e.ctrlKey && !e.altKey && editing) {
      e.preventDefault();
      document.execCommand("insertText", false, /^\t/m.test(ta.value) ? "\t" : "  "); // keeps undo; tabs if the file uses them
    }
  });
  d.el.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      e.stopPropagation();
      save();
    }
  });
  d.el.addEventListener("click", (e) => {
    const v = e.target.closest("[data-view]")?.dataset.view;
    if (v) {
      view = v;
      paint();
    }
    const act = e.target.closest("[data-ed]")?.dataset.ed;
    if (act === "edit") {
      editing = true;
      view = "text";
      paint();
      ta.focus();
    }
    if (act === "save") save();
  });
  paint();
  setTimeout(() => {
    (view === "text" ? ta : $(".ed-preview", d.el)).focus();
    if (line > 0 && view === "text") {
      const at = ta.value.split("\n").slice(0, line - 1).join("\n").length + (line > 1 ? 1 : 0);
      ta.setSelectionRange(at, at);
      ta.scrollTop = Math.max(0, (line - 6) * parseFloat(getComputedStyle(ta).lineHeight));
    }
  }, 0);
  return d;
}
