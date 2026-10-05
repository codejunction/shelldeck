// Remote systems page: saved SSH hosts (a terminal that runs ssh) and RDP desktops (mstsc / xfreerdp on the host).
import { S, orderedProjects, refreshProjects, showSession } from "./app.js";
import { $, api, confirmDialog, dialog, esc, icon, toast, toastError } from "./ui.js";

let data = { remotes: [], clients: {} };

export async function renderRemotes(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.addEventListener("click", (e) => onClick(e, el));
  }
  try {
    data = await api("/api/remotes");
  } catch (e) {
    return toastError(e);
  }
  const c = data.clients || {};
  const warn = [
    !c.ssh ? "ssh is not on PATH (Windows: Settings › Optional features › OpenSSH Client)." : "",
    !c.rdp ? "No RDP client found (Windows has mstsc; on Linux install xfreerdp or Remmina)." : "",
  ].filter(Boolean);
  const rows = data.remotes.length
    ? data.remotes
        .map((r) => {
          const p = S.projects.find((x) => x.id === r.project_id);
          const target = `${r.user ? `${esc(r.user)}@` : ""}${esc(r.host)}${r.port && r.port !== (r.kind === "rdp" ? 3389 : 22) ? `:${r.port}` : ""}`;
          return `<tr data-id="${r.id}">
            <td><b>${esc(r.name)}</b></td>
            <td><span class="remote-kind ${r.kind}">${r.kind === "rdp" ? "Desktop (RDP)" : "Terminal (SSH)"}</span></td>
            <td class="mono">${target}${r.identity ? `<br><span class="faint">key ${esc(r.identity)}</span>` : ""}</td>
            <td>${p ? esc(p.name) : '<span class="faint">current project</span>'}</td>
            <td class="faint">${r.last_used_at ? new Date(r.last_used_at).toLocaleString() : "never"}</td>
            <td class="row-btns">
              <button class="btn sm primary" data-connect="${r.id}">${icon(r.kind === "rdp" ? "external" : "terminal")}Connect</button>
              <button class="icon-btn sm" data-edit="${r.id}" title="Edit" aria-label="Edit ${esc(r.name)}">${icon("pencil")}</button>
              <button class="icon-btn sm danger" data-del="${r.id}" title="Delete" aria-label="Delete ${esc(r.name)}">${icon("trash")}</button>
            </td></tr>`;
        })
        .join("")
    : `<tr><td colspan="6" class="faint empty-row">No remote systems yet. Add a Linux server (SSH) or a Windows machine (RDP).</td></tr>`;
  el.innerHTML = `<div class="page-head"><div><h1>Remote systems</h1>
      <p>SSH hosts open as shelldeck terminals (exit ssh to get the local shell back). RDP opens the desktop client on this machine. Passwords are never stored: ssh asks in the terminal (keys and ssh-agent work), the RDP client asks in its window.</p></div>
      <button class="btn primary" data-add>${icon("plus")}Add remote</button></div>
    ${warn.map((w) => `<p class="faint">${esc(w)}</p>`).join("")}
    <div class="card mon-table"><table>
      <thead><tr><th>Name</th><th>Type</th><th>Address</th><th>Opens in</th><th>Last used</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

async function onClick(e, el) {
  if (e.target.closest("[data-add]")) return editDialog(null, el);
  const edit = e.target.closest("[data-edit]")?.dataset.edit;
  if (edit) return editDialog(data.remotes.find((r) => r.id === edit), el);
  const del = e.target.closest("[data-del]")?.dataset.del;
  if (del) {
    const r = data.remotes.find((x) => x.id === del);
    if (!(await confirmDialog(`Delete ${r?.name || "this remote"}?`, { ok: "Delete", danger: true }))) return;
    try {
      await api(`/api/remotes/${del}`, { method: "DELETE" });
    } catch (err) {
      toastError(err);
    }
    return renderRemotes(el);
  }
  const id = e.target.closest("[data-connect]")?.dataset.connect;
  if (id) return connect(id, el);
}

/** SSH: a new terminal that runs ssh; RDP: the desktop client on the host. */
export async function connect(id, el) {
  const focused = S.focused && S.projects.find((p) => p.sessions.some((s) => s.id === S.focused));
  try {
    const r = await api(`/api/remotes/${id}/connect`, { method: "POST", body: { project_id: focused?.id || orderedProjects()[0]?.id || "" } });
    if (r.kind === "rdp") return toast({ title: "Remote desktop opening", body: r.command });
    await refreshProjects();
    const term = await showSession(r.session.id);
    term?.focus();
  } catch (err) {
    const code = String(err.message || err);
    if (code.includes("host_only")) return toast({ title: "Remote desktop opens on the host", body: "Connect from a browser on the machine running shelldeck.", kind: "warn" });
    if (code.includes("project_required")) return toast({ title: "Add a project first", body: "SSH terminals open in a project.", kind: "warn" });
    toastError(err);
  } finally {
    if (el) renderRemotes(el);
  }
}

function editDialog(r, el) {
  const projects = orderedProjects();
  const kind = r?.kind || "ssh";
  const d = dialog({
    title: r ? `Edit ${r.name}` : "Add remote system",
    body: `<form class="remote-form">
      <div class="seg" role="radiogroup" aria-label="Type">
        <button type="button" role="radio" data-kind="ssh">Linux / SSH terminal</button>
        <button type="button" role="radio" data-kind="rdp">Windows / RDP desktop</button>
      </div>
      <label class="field"><span>Host or IP</span><input name="host" required value="${esc(r?.host || "")}" placeholder="server.example.com" autocomplete="off" spellcheck="false" /></label>
      <label class="field"><span>User <small class="faint">(optional)</small></span><input name="user" value="${esc(r?.user || "")}" autocomplete="off" spellcheck="false" /></label>
      <label class="field"><span>Port</span><input name="port" type="number" min="1" max="65535" value="${r?.port || ""}" placeholder="22" /></label>
      <label class="field ssh-only"><span>Key file <small class="faint">(optional, e.g. ~/.ssh/id_ed25519)</small></span><input name="identity" value="${esc(r?.identity || "")}" autocomplete="off" spellcheck="false" /></label>
      <label class="field ssh-only"><span>Opens in project</span><select name="project_id"><option value="">The current project</option>${projects
        .map((p) => `<option value="${p.id}" ${p.id === r?.project_id ? "selected" : ""}>${esc(p.name)}</option>`)
        .join("")}</select></label>
      <label class="field"><span>Name <small class="faint">(optional)</small></span><input name="name" value="${esc(r?.name || "")}" autocomplete="off" /></label>
      <div class="error" data-err></div>
    </form>`,
    foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-save>${r ? "Save" : "Add"}</button>`,
  });
  const form = $("form", d.el);
  let k = kind;
  const setKind = (v) => {
    k = v;
    for (const b of d.el.querySelectorAll("[data-kind]")) b.classList.toggle("on", b.dataset.kind === v), b.setAttribute("aria-checked", String(b.dataset.kind === v));
    for (const f of d.el.querySelectorAll(".ssh-only")) f.hidden = v !== "ssh";
    form.port.placeholder = v === "rdp" ? "3389" : "22";
  };
  setKind(kind);
  d.el.addEventListener("click", (e) => {
    const v = e.target.closest("[data-kind]")?.dataset.kind;
    if (v) setKind(v);
  });
  const save = async (e) => {
    e?.preventDefault();
    const body = { kind: k, host: form.host.value, user: form.user.value, port: form.port.value || null, identity: form.identity.value, project_id: form.project_id.value, name: form.name.value };
    try {
      await api(r ? `/api/remotes/${r.id}` : "/api/remotes", { method: r ? "PUT" : "POST", body });
      d.close();
      renderRemotes(el);
    } catch (err) {
      $("[data-err]", d.el).textContent = String(err.message || err).replaceAll("_", " ");
    }
  };
  form.addEventListener("submit", save);
  $("[data-save]", d.el).onclick = save;
}
