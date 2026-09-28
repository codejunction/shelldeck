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

export function showGitGraph(project) {
  let limit = PAGE;
  let hasMore = false;
  let loadingMore = false;
  const d = dialog({ title: `Git \u2014 ${project.name}`, cls: "git-dialog", wide: true, body: `<div class="placeholder">Loading\u2026</div>` });
  const body = $(".dialog-body", d.el);

  async function load() {
    limit = PAGE;
    body.innerHTML = `<div class="placeholder">Loading\u2026</div>`;
    try {
      render(await api(`/api/projects/${project.id}/git/log?limit=${limit}`));
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
    body.innerHTML = `<div class="git-head">
        <span class="faint">On <strong>${esc(log.current_branch)}</strong></span>
        <button class="icon-btn sm" data-git="refresh" title="Refresh" aria-label="Refresh">${icon("refresh")}</button>
      </div>
      <div class="git-rows">${renderCommits(log.commits, lanes)}</div>`;

    $("[data-git=refresh]", body).onclick = load;
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
