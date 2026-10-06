// Scratchpad: quick markdown notes that belong to no project. Saves as you type.
import { $, api, confirmDialog, esc, fmtTime, icon, store, toastError } from "./ui.js";
import { markdown } from "./md.js";

let notes = [];
let current = null; // note id
let preview = store.get("scratchPreview", false);
let timer = null;
let pending = null; // note with an unsaved edit

const title = (n) => n.body.split("\n").map((l) => l.replace(/^[#>\-*\s]+/, "").trim()).find(Boolean) || "Empty note";

export async function renderScratch(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.innerHTML = `<div class="page-head"><div><h1>Scratchpad</h1><p>Notes for anything, in markdown. Not tied to a project; saved as you type.</p></div>
        <button class="btn primary" data-sp="new">${icon("plus")}New note</button></div>
      <div class="sp-wrap">
        <div class="card sp-list" role="listbox" aria-label="Notes"></div>
        <div class="card sp-editor">
          <div class="sp-bar"><div class="seg" role="group" aria-label="View"><button data-sp="write">Write</button><button data-sp="preview">Preview</button></div>
            <span class="sp-saved faint"></span>
            <button class="icon-btn sm danger" data-sp="delete" title="Delete note" aria-label="Delete note">${icon("trash")}</button></div>
          <textarea class="sp-text" spellcheck="true" placeholder="# Title&#10;&#10;Write anything…" aria-label="Note"></textarea>
          <div class="sp-preview md" tabindex="0"></div>
        </div>
      </div>`;
    el.addEventListener("click", (e) => onClick(e, el));
    $(".sp-text", el).addEventListener("input", () => {
      const n = notes.find((x) => x.id === current);
      if (!n) return;
      n.body = $(".sp-text", el).value;
      n.updated_at = new Date().toISOString();
      $(".sp-saved", el).textContent = "saving…";
      clearTimeout(timer);
      pending = n;
      timer = setTimeout(() => flush(el), 500);
      const row = el.querySelector(`.sp-item[data-id="${n.id}"] b`);
      if (row) row.textContent = title(n);
    });
  }
  try {
    notes = (await api("/api/scratch")).notes;
  } catch (e) {
    return toastError(e);
  }
  if (!notes.length) notes = [await api("/api/scratch", { method: "POST", body: { body: "" } })];
  if (!notes.some((n) => n.id === current)) current = notes[0].id;
  paint(el);
}

function flush(el) {
  clearTimeout(timer);
  if (pending) save(el, pending);
  pending = null;
}

async function save(el, n) {
  try {
    await api(`/api/scratch/${n.id}`, { method: "PUT", body: { body: n.body } });
    $(".sp-saved", el).textContent = "saved";
  } catch (e) {
    $(".sp-saved", el).textContent = "not saved";
    toastError(e);
  }
}

function paint(el) {
  const n = notes.find((x) => x.id === current);
  $(".sp-list", el).innerHTML = notes
    .map((x) => `<button class="sp-item ${x.id === current ? "on" : ""}" data-id="${x.id}" role="option" aria-selected="${x.id === current}"><b>${esc(title(x))}</b><span class="faint">${esc(fmtTime(x.updated_at))}</span></button>`)
    .join("");
  const ta = $(".sp-text", el);
  if (ta.value !== n.body) ta.value = n.body;
  $(".sp-saved", el).textContent = "";
  el.querySelector(".sp-editor").dataset.view = preview ? "preview" : "write";
  for (const b of el.querySelectorAll('[data-sp="write"], [data-sp="preview"]')) b.classList.toggle("on", (b.dataset.sp === "preview") === preview);
  if (preview) $(".sp-preview", el).innerHTML = markdown(n.body) || '<p class="faint">Nothing here yet.</p>';
}

async function onClick(e, el) {
  const item = e.target.closest(".sp-item")?.dataset.id;
  if (item) {
    flush(el);
    current = item;
    paint(el);
    if (!preview) $(".sp-text", el).focus();
    return;
  }
  const act = e.target.closest("[data-sp]")?.dataset.sp;
  if (act === "new") {
    flush(el);
    try {
      const n = await api("/api/scratch", { method: "POST", body: { body: "" } });
      notes.unshift(n);
      current = n.id;
      preview = false;
      paint(el);
      $(".sp-text", el).focus();
    } catch (err) {
      toastError(err);
    }
  } else if (act === "write" || act === "preview") {
    preview = act === "preview";
    store.set("scratchPreview", preview);
    paint(el);
    if (!preview) $(".sp-text", el).focus();
  } else if (act === "delete") {
    const n = notes.find((x) => x.id === current);
    if (n.body.trim() && !(await confirmDialog(`Delete "${title(n)}"?`, { ok: "Delete", danger: true }))) return;
    clearTimeout(timer);
    pending = null;
    await api(`/api/scratch/${n.id}`, { method: "DELETE" }).catch(toastError);
    current = null;
    renderScratch(el);
  }
}

/** A note changed outside this page (sd notes): reload unless you are mid-edit. */
export function scratchChanged() {
  const el = document.getElementById("view-scratch");
  if (el && !el.hidden && el.dataset.wired && !pending) renderScratch(el);
}
