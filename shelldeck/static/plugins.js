// Plugins page: installed plugins, their commands and events, enable / disable.
import { S, runPlugin } from "./app.js";
import { api, esc, icon, promptDialog, toastError } from "./ui.js";

const EXAMPLE = `[project.entry-points."shelldeck.plugins"]
docker = "shelldeck_docker:plugin"

from shelldeck.plugins import Plugin
plugin = Plugin("docker", "0.1.0")

@plugin.command("logs", "Follow a container's logs", usage="<container>")
def logs(args, ctx):  # ctx: session_id, cwd, shell, project
    return {"input": "docker logs -f " + " ".join(args)}  # typed, not run`;

const STATUS = { loaded: ["ok", "On"], disabled: ["", "Off"], error: ["bad", "Failed"] };

export async function renderPlugins(el) {
  if (!el.dataset.wired) {
    el.dataset.wired = "1";
    el.innerHTML = `<div class="page-head">
        <div><h1>Plugins</h1><p>Python packages that add commands to the <code>?</code> menu and the CLI (<code>?docker ps</code>, <code>sd docker ps</code>) and react to shelldeck's events. They run inside shelldeck with your permissions, so install only plugins you trust.</p></div>
      </div>
      <div class="plg-list"></div>
      <div class="card plg-help">
        <b>Add a plugin</b>
        <p class="muted">Install it next to shelldeck, then restart shelldeck (<code>sd stop</code>, then <code>sd</code>):</p>
        <pre class="mono">uv tool install shelldeck --with &lt;package or folder&gt;    # e.g. --with ./examples/shelldeck-docker</pre>
        <p class="muted">Write one: a package with a <code>shelldeck.plugins</code> entry point (the reference plugin is <code>examples/shelldeck-docker</code> in the shelldeck repo). The full guide: <a href="https://github.com/codejunction/shelldeck/blob/main/docs/plugins.md" target="_blank" rel="noopener">Writing a plugin</a>.</p>
        <pre class="mono">${esc(EXAMPLE)}</pre>
      </div>`;
    el.addEventListener("click", (e) => onClick(e, el));
  }
  let data;
  try {
    data = await api("/api/plugins");
  } catch (e) {
    return toastError(e);
  }
  S.pluginCommands = data.commands;
  el.querySelector(".plg-list").innerHTML = data.plugins.length
    ? data.plugins.map(card).join("")
    : `<div class="card plg-empty muted">${icon("grid")} No plugins installed yet.</div>`;
}

function card(p) {
  const [cls, label] = STATUS[p.status] || ["", p.status];
  const on = p.status !== "disabled";
  return `<div class="card plg">
    <div class="plg-head">
      <b>${esc(p.name)}</b>${p.version ? `<span class="faint">${esc(p.version)}</span>` : ""}<span class="exit ${cls}">${label}</span>
      <span class="plg-pkg faint">${esc(p.package && p.package !== p.name ? p.package : "")}</span>
      <button class="btn sm" data-plg="toggle" data-name="${esc(p.name)}" data-on="${on ? 1 : ""}">${icon("power")}${on ? "Disable" : "Enable"}</button>
    </div>
    ${p.summary ? `<p class="muted">${esc(p.summary)}</p>` : ""}
    ${p.error ? `<p class="plg-error">${esc(p.error)}</p>` : ""}
    ${p.commands.length ? `<table class="plg-cmds"><tbody>${p.commands
      .map((c) => `<tr><td class="mono">${esc(c.name)}${c.usage ? ` <span class="faint">${esc(c.usage)}</span>` : ""}</td><td class="muted">${esc(c.description)}</td>
        <td><button class="btn sm" data-plg="run" data-cmd="${esc(c.name)}" data-usage="${esc(c.usage || "")}" title="Run in the focused terminal">${icon("play")}Run</button></td></tr>`)
      .join("")}</tbody></table>` : ""}
    ${p.events.length ? `<p class="faint plg-events">Listens to ${p.events.map((e) => `<code>${esc(e)}*</code>`).join(", ")}</p>` : ""}
  </div>`;
}

async function onClick(e, el) {
  const b = e.target.closest("[data-plg]");
  if (!b) return;
  if (b.dataset.plg === "run") {
    const usage = b.dataset.usage;
    let args = "";
    if (usage) {
      // ponytail: arguments split on spaces (no quoting); a plugin that needs quoted values can join them back
      args = await promptDialog(b.dataset.cmd, "", { label: `Arguments: ${usage}`, ok: "Run", optional: !usage.includes("<") });
      if (args === null) return;
    }
    return runPlugin([...b.dataset.cmd.split(" "), ...args.split(/\s+/).filter(Boolean)]);
  }
  try {
    await api(`/api/plugins/${encodeURIComponent(b.dataset.name)}`, { method: "POST", body: { enabled: !b.dataset.on } });
  } catch (err) {
    return toastError(err.message === "host_only" ? new Error("Only the machine running shelldeck can turn plugins on or off.") : err);
  }
  renderPlugins(el);
}
