import { $, $$, api, dialog, esc, fmtTime, icon, menu, toast, toastError } from "./ui.js";

const ROW_H = 28;
const LANE_W = 16;
const R = 4;

function laneX(col) {
  return col * LANE_W + LANE_W / 2;
}

// A curve if the lane changes column, a straight line otherwise.
function path(x1, y1, x2, y2) {
  if (x1 === x2) return `M${x1},${y1} L${x2},${y2}`;
  const my = (y1 + y2) / 2;
  return `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`;
}

function rowSvg(commit, lanes) {
  const midY = ROW_H / 2;
  const cx = laneX(commit.column);
  const lines = [
    ...commit.through.map((t) => `<path d="${path(laneX(t.col), 0, laneX(t.col), ROW_H)}" stroke="${t.color}" />`),
    ...commit.enter.map((e) => `<path d="${path(laneX(e.from_col), 0, cx, midY)}" stroke="${e.color}" />`),
    ...commit.exit.map((x) => `<path d="${path(cx, midY, laneX(x.to_col), ROW_H)}" stroke="${x.color}" />`),
  ].join("");
  return `<svg width="${lanes * LANE_W}" height="${ROW_H}" class="git-lanes">${lines}<circle cx="${cx}" cy="${midY}" r="${R}" fill="${commit.color}" /></svg>`;
}

function refPills(refs) {
  return refs
    .map((r) => {
      const tag = r.startsWith("tag: ");
      const name = r.replace("HEAD -> ", "").replace("tag: ", "");
      return `<span class="git-ref ${tag ? "tag" : ""}" data-ref="${esc(name)}">${esc(name)}</span>`;
    })
    .join("");
}

function renderCommits(commits, lanes) {
  return commits
    .map(
      (c) => `<div class="git-row" data-hash="${c.hash}">
        ${rowSvg(c, lanes)}
        <span class="git-hash mono">${c.short}</span>
        <span class="git-subject" title="${esc(c.subject)}">${esc(c.subject)}</span>
        <span class="git-refs">${refPills(c.refs)}</span>
        <span class="git-author">${esc(c.author)}</span>
        <span class="git-date faint">${fmtTime(c.date)}</span>
      </div>`,
    )
    .join("");
}

const PAGE = 300;
const SCROLL_MARGIN = 120; // px from the bottom that triggers loading the next page

const STATUS_MARK = { modified: "M", added: "A", deleted: "D", renamed: "R", copied: "C", conflict: "!", untracked: "U", "type changed": "T" };

/** Unified diff as lines coloured by kind; text is escaped. */
function renderDiff(d) {
  if (d.missing) return `<div class="placeholder">No longer changed.</div>`;
  if (d.binary) return `<div class="placeholder">Binary file</div>`;
  if (!d.diff.trim()) return `<div class="placeholder">No line changes (mode or whitespace only).</div>`;
  const rows = d.diff
    .split("\n")
    .filter((l) => !/^(diff --git|index |--- |\+\+\+ |new file mode|deleted file mode|similarity index|rename (from|to) )/.test(l))
    .map((l) => `<div class="${l.startsWith("@@") ? "dl-hunk" : l.startsWith("+") ? "dl-add" : l.startsWith("-") ? "dl-del" : "dl-ctx"}">${esc(l) || " "}</div>`)
    .join("");
  return `<div class="git-diff mono">${rows}${d.truncated ? `<div class="placeholder">Diff cut off (too large).</div>` : ""}</div>`;
}

function renderChanges(c) {
  const sync = [c.ahead ? `${c.ahead} ahead` : "", c.behind ? `${c.behind} behind` : ""].filter(Boolean).join(", ");
  const head = `<div class="git-sub faint">On <strong>${esc(c.branch || "(detached)")}</strong>${sync ? ` · ${sync}` : ""} · ${c.files.length || "no"} changed file${c.files.length === 1 ? "" : "s"}${
    c.files.length ? ` · <span class="dl-add">+${c.files.reduce((n, f) => n + (f.added || 0), 0)}</span> <span class="dl-del">−${c.files.reduce((n, f) => n + (f.deleted || 0), 0)}</span>` : ""
  }</div>`;
  if (!c.files.length) return `${head}<div class="placeholder">Working tree clean.</div>`;
  const rows = c.files
    .map((f) => {
      const slash = f.path.lastIndexOf("/");
      const counts = f.added == null ? `<span class="faint">bin</span>` : `<span class="dl-add">+${f.added}</span> <span class="dl-del">−${f.deleted}</span>`;
      return `<div class="git-file" data-file="${esc(f.path)}" tabindex="0" role="button" aria-expanded="false">
          <span class="git-st st-${esc(f.status.replace(" ", "-"))}" title="${esc(f.status)}${f.staged ? " (staged)" : ""}">${STATUS_MARK[f.status] || "M"}</span>
          <span class="git-fname" title="${esc(f.old_path ? `${f.old_path} → ${f.path}` : f.path)}"><b>${esc(f.path.slice(slash + 1))}</b> <span class="faint">${esc(slash >= 0 ? f.path.slice(0, slash) : "")}</span></span>
          <span class="git-counts mono">${counts}</span>
        </div><div class="git-file-diff" hidden></div>`;
    })
    .join("");
  return `${head}<div class="git-rows">${rows}</div>`;
}

/** Git dialog: uncommitted changes (with per-file diffs) and the commit graph. */
export function showGitGraph(project, tab = project.git_changes ? "changes" : "history") {
  let limit = PAGE;
  let hasMore = false;
  let loadingMore = false;
  const d = dialog({
    title: `Git \u2014 ${project.name}`,
    cls: "git-dialog",
    wide: true,
    body: `<div class="git-head">
        <div class="seg" role="tablist">
          <button role="tab" data-tab="changes">Changes</button>
          <button role="tab" data-tab="history">History</button>
        </div>
        <button class="icon-btn sm" data-git="refresh" title="Refresh" aria-label="Refresh">${icon("refresh")}</button>
      </div><div class="git-body"><div class="placeholder">Loading\u2026</div></div>`,
  });
  const body = $(".git-body", d.el);
  $(".git-head", d.el).addEventListener("click", (e) => {
    const t = e.target.closest("[data-tab]")?.dataset.tab;
    if (t) {
      tab = t;
      return load();
    }
    if (e.target.closest("[data-git=refresh]")) load();
  });
  body.addEventListener("click", (e) => toggleFile(e.target.closest(".git-file")));
  body.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches(".git-file")) {
      e.preventDefault();
      toggleFile(e.target);
    }
  });

  async function toggleFile(row) {
    if (!row) return;
    const box = row.nextElementSibling;
    const open = box.hidden;
    box.hidden = !open;
    row.setAttribute("aria-expanded", String(open));
    row.classList.toggle("open", open);
    if (!open || box.dataset.loaded) return;
    box.innerHTML = `<div class="placeholder">Loading\u2026</div>`;
    try {
      box.innerHTML = renderDiff(await api(`/api/projects/${project.id}/git/diff?file=${encodeURIComponent(row.dataset.file)}`));
      box.dataset.loaded = "1";
    } catch (err) {
      box.innerHTML = `<div class="placeholder">${esc(err.message.replaceAll("_", " "))}</div>`;
    }
  }

  async function load() {
    for (const b of $$("[data-tab]", d.el)) b.classList.toggle("on", b.dataset.tab === tab), b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    limit = PAGE;
    body.innerHTML = `<div class="placeholder">Loading\u2026</div>`;
    try {
      if (tab === "changes") body.innerHTML = renderChanges(await api(`/api/projects/${project.id}/git/changes`));
      else render(await api(`/api/projects/${project.id}/git/log?limit=${limit}`));
    } catch (e) {
      body.innerHTML = `<div class="placeholder">${esc(e.message.replaceAll("_", " "))}</div>`;
    }
  }

  async function loadMore() {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    limit += PAGE;
    const scrollTop = $(".git-rows", body)?.scrollTop || 0;
    try {
      render(await api(`/api/projects/${project.id}/git/log?limit=${limit}`), scrollTop);
    } catch (e) {
      toastError(e);
    } finally {
      loadingMore = false;
    }
  }

  function render(log, scrollTop = 0) {
    hasMore = log.has_more;
    if (!log.commits.length) {
      body.innerHTML = `<div class="placeholder">No commits yet.</div>`;
      return;
    }
    const lanes = 1 + Math.max(0, ...log.commits.flatMap((c) => [c.column, ...c.through.map((t) => t.col), ...c.enter.map((e) => e.from_col), ...c.exit.map((x) => x.to_col)]));
    body.innerHTML = `<div class="git-sub faint">On <strong>${esc(log.current_branch)}</strong></div>
      <div class="git-rows">${renderCommits(log.commits, lanes)}</div>`;
    const rows = $(".git-rows", body);
    rows.scrollTop = scrollTop;
    rows.addEventListener("scroll", () => {
      if (rows.scrollTop + rows.clientHeight >= rows.scrollHeight - SCROLL_MARGIN) loadMore();
    });
    const byHash = new Map(log.commits.map((c) => [c.hash, c]));
    rows.addEventListener("contextmenu", (e) => {
      const row = e.target.closest(".git-row");
      if (!row) return;
      e.preventDefault();
      rowMenu({ x: e.clientX, y: e.clientY }, project, byHash.get(row.dataset.hash));
    });
    $$(".git-ref", body).forEach((pill) => {
      pill.addEventListener("click", (e) => {
        e.stopPropagation();
        checkout(project, pill.dataset.ref);
      });
    });
  }

  function rowMenu(anchor, project, commit) {
    const items = commit.refs
      .filter((r) => !r.startsWith("tag: "))
      .map((r) => r.replace("HEAD -> ", ""))
      .map((name) => ({ label: `Checkout branch ${name}`, icon: "git-branch", onClick: () => checkout(project, name) }));
    items.push({ label: "Checkout commit (detached)", icon: "git-branch", onClick: () => checkout(project, commit.hash) });
    menu(anchor, items);
  }

  async function checkout(project, ref) {
    try {
      await api(`/api/projects/${project.id}/git/checkout`, { method: "POST", body: { ref } });
      toast({ title: `Checked out ${ref}` });
      load();
    } catch (e) {
      toastError(e);
    }
  }

  load();
}
