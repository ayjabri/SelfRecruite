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

// ---- tabs
let tab = "jobs";
function showTab(name) {
  tab = name;
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $("filters").hidden = name !== "jobs"; $("list").hidden = name !== "jobs"; $("detail").hidden = name !== "jobs";
  $("dups").hidden = name !== "dups"; $("runpanel").hidden = name !== "run"; $("settings").hidden = name !== "settings"; $("admin").hidden = name !== "admin";
  try { localStorage.setItem("tab", name); } catch (e) {}
  if (name === "dups") renderDups();
  if (name === "settings") loadConfigs();
  if (name === "admin") renderAdmin();
  if (name === "run") { if (!tools.length && isAdmin()) loadTools(); refreshRuns(); } else clearTimeout(pollTimer);
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

