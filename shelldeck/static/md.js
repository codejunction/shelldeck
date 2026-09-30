// Tiny markdown renderer for the file viewer and the scratchpad. Everything is escaped first, so
// markdown can't inject HTML. ponytail: common syntax only (no nested lists, footnotes or raw HTML).
import { esc } from "./ui.js";

function inline(s) {
  const codes = [];
  s = esc(s).replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  s = s
    .replace(/!\[([^\]]*)\]\((https?:[^)\s]+)\)/g, '<img alt="$1" src="$2">')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
    .replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));

export function markdown(src) {
  const lines = String(src).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if ((m = line.match(/^\s*(```|~~~)\s*([\w+-]*)/))) {
      const code = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith(m[1]); i++) code.push(lines[i]);
      i++;
      out.push(`<pre><code${m[2] ? ` data-lang="${esc(m[2])}"` : ""}>${esc(code.join("\n"))}</code></pre>`);
    } else if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      out.push(`<h${m[1].length}>${inline(m[2].replace(/\s+#+\s*$/, ""))}</h${m[1].length}>`);
      i++;
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      out.push("<hr>");
      i++;
    } else if (/^\s*>/.test(line)) {
      const q = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i++) q.push(lines[i].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${markdown(q.join("\n"))}</blockquote>`);
    } else if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items = [];
      for (; i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i]); i++) {
        const text = lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, "");
        const task = text.match(/^\[([ xX])\]\s+(.*)$/);
        items.push(task ? `<li class="task"><input type="checkbox" disabled ${task[1] === " " ? "" : "checked"}> ${inline(task[2])}</li>` : `<li>${inline(text)}</li>`);
      }
      out.push(`<${ordered ? "ol" : "ul"}>${items.join("")}</${ordered ? "ol" : "ul"}>`);
    } else if (line.includes("|") && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1] || "")) {
      const head = cells(line);
      const rows = [];
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(cells(lines[i]));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
    } else if (!line.trim()) {
      i++;
    } else {
      const para = [];
      for (; i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*~~~|\s*>|\s*([-*+]|\d+[.)])\s+)/.test(lines[i]); i++) para.push(lines[i]);
      if (!para.length) para.push(lines[i++]);
      out.push(`<p>${para.map(inline).join("<br>")}</p>`);
    }
  }
  return out.join("\n");
}
