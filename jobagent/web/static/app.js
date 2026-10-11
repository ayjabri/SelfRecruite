// shared helpers, session, tabs and theme (loaded first)
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let me = null;
async function api(path, opts) {
  if (opts && opts.method && opts.method !== "GET") opts.headers = {...(opts.headers || {}), "X-CSRF-Token": me?.csrf || ""};
  const r = await fetch(path, opts);
  if (r.status === 401) { location.href = "/login"; throw new Error("sign in first"); }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
const isAdmin = () => me && me.role === "admin";

// copy text to the clipboard; the async API needs HTTPS or localhost, so fall back to the old way
async function copyText(text) {
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; } } catch (e) {}
  const ta = document.createElement("textarea");
  ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.left = "-9999px";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove();
  return ok;
}

// ---- tabs
let tab = "jobs";
function showTab(name) {
  tab = name;
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $("filters").hidden = name !== "jobs"; $("list").hidden = name !== "jobs"; $("detail").hidden = name !== "jobs";
  $("dups").hidden = name !== "dups"; $("runpanel").hidden = name !== "run"; $("settings").hidden = name !== "settings"; $("admin").hidden = name !== "admin"; $("check").hidden = name !== "check";
  try { localStorage.setItem("tab", name); } catch (e) {}
  if (name === "dups") renderDups();
  if (name === "settings") loadConfigs();
  if (name === "admin") renderAdmin();
  if (name === "check") renderCheck();
  if (name === "run") { if (!tools.length && isAdmin()) loadTools(); refreshRuns(); loadPref(); } else clearTimeout(pollTimer);
}
$("tabs").addEventListener("click", e => { const b = e.target.closest("button[data-tab]"); if (b) showTab(b.dataset.tab); });

// ---- theme
function applyTheme(mode) {
  if (mode) document.documentElement.dataset.theme = mode; else delete document.documentElement.dataset.theme;
  const dark = mode ? mode === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  $("theme").textContent = dark ? "☀" : "☾";
  $("theme").title = dark ? "Switch to light mode" : "Switch to dark mode";
}
$("theme").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  try { localStorage.setItem("theme", next); } catch (e) {}
  applyTheme(next);
});
let savedTheme = null; try { savedTheme = localStorage.getItem("theme"); } catch (e) {}
applyTheme(savedTheme);


// ---- a small markdown renderer (headings, lists, emphasis, code, links, quotes, tables, rules)
function inlineMd(s) {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/__([^_]+)__/g, "<b>$1</b>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<i>$2</i>").replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, "$1<i>$2</i>");
  s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}
// opts.plain: for posting text (not markdown): keep every line break, and show the section
// headings the parser found (opts.headings) and short "Label:" lines as headings.
function renderMarkdown(text, opts = {}) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const out = []; let i = 0, para = [], list = null, quote = [];
  const headings = new Set([...(opts.headings || [])].map(h => h.trim().toLowerCase()));
  const isHeading = l => opts.plain && l.trim().length <= 80 && (headings.has(l.trim().toLowerCase()) || /^[^.!?]{2,60}:$/.test(l.trim()));
  const flushPara = () => { if (para.length) { out.push(`<p>${para.map(inlineMd).join(opts.plain ? "<br>" : " ")}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map(x => `<li>${inlineMd(x)}</li>`).join("")}</${list.tag}>`); list = null; } };
  const flushQuote = () => { if (quote.length) { out.push(`<blockquote>${inlineMd(quote.join(" "))}</blockquote>`); quote = []; } };
  const flushAll = () => { flushPara(); flushList(); flushQuote(); };
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if (/^```/.test(line)) { flushAll(); const code = []; i++; while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]); i++; out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); continue; }
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) { flushAll(); out.push(`<h${m[1].length}>${inlineMd(m[2].replace(/\s+#+$/, ""))}</h${m[1].length}>`); i++; continue; }
    if (isHeading(line)) { flushAll(); out.push(`<h3>${inlineMd(line.trim())}</h3>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushAll(); out.push("<hr>"); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flushAll(); const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => inlineMd(c.trim()));
      const head = cells(line); i += 2; const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push(`<table><thead><tr>${head.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`); continue;
    }
    if ((m = line.match(/^\s*([-*+•·]|\d+[.)])\s+(.*)$/))) {
      flushPara(); flushQuote(); const tag = /\d/.test(m[1]) ? "ol" : "ul";
      if (!list || list.tag !== tag) { flushList(); list = {tag, items: []}; }
      list.items.push(m[2]); i++;
      while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+•·]|\d+[.)])\s+/.test(lines[i])) list.items[list.items.length - 1] += " " + lines[i++].trim();
      continue;
    }
    if ((m = line.match(/^\s*>\s?(.*)$/))) { flushPara(); flushList(); quote.push(m[1]); i++; continue; }
    if (!line.trim()) { flushAll(); i++; continue; }
    flushList(); flushQuote(); para.push(line.trim()); i++;
  }
  flushAll();
  return out.join("\n");
}
