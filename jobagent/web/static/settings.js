// ---- settings: form editors for the files in config/
const CONFIG_FORMS = {
  scope: [
    {section: "Title keywords", help: "A job counts for you only if its title contains at least one keyword below and none of the excluded ones. Whole words, case-insensitive."},
    {path: "title.include", type: "list", label: "Keep titles containing", help: "one phrase per line"},
    {path: "title.exclude", type: "list", label: "Drop titles containing", help: "one phrase per line"},
    {section: "Location"},
    {path: "location.countries", type: "tags", label: "Countries", help: "ISO codes; empty = the countries the platform covers"},
    {path: "location.allowed_states", type: "tags", label: "Allowed US states", help: "state codes, comma-separated. Empty = anywhere"},
    {path: "location.remote_ok", type: "bool", label: "Remote jobs count", help: "keep remote jobs wherever they are based"},
  ],
  targets: [
    {section: "Fit score", help: "rule score = 100 × (required·M + preferred·P + title_family·F + seniority·S + domain·D)"},
    {path: "weights", type: "numbers", label: "Weights", step: 0.05},
    {path: "similarity", type: "numbers", label: "Similarity band", step: 0.01, help: "embedding similarity at or below floor counts as 0, at or above ceiling as 1"},
    {path: "title_families", type: "families", label: "Title families", help: "the highest weight among families whose keywords appear in the title; the family also picks the resume variant to tailor from"},
    {path: "seniority", type: "numbers", label: "Seniority", step: 0.1},
    {section: "Domain"},
    {path: "domains.companies", type: "tags", label: "Domain companies", help: "full domain credit for these employers"},
    {path: "domains.keywords", type: "tags", label: "Domain keywords", help: "otherwise 1/3 credit per distinct keyword found"},
    {section: "Hard gates", help: "a job that trips a gate is capped at 30"},
    {path: "gates.clearance_required", type: "bool", label: "Cap clearance-required jobs"},
    {path: "gates.phd_required", type: "bool", label: "Cap PhD-required jobs"},
    {path: "gates.pay_floor", type: "number", label: "Pay floor", help: "cap jobs whose top of range is below this (annual). Empty = off"},
    {path: "gates.allowed_states", type: "tags", label: "Allowed states", help: "cap non-remote jobs located elsewhere"},
  ],
  llm: [
    {section: "AI account", help: "The shared local model judges up to your daily budget. Your own account lifts the cap and will power commentary and resume tailoring. Keys are encrypted on the server and never shown again."},
    {path: "provider", type: "choice", label: "Provider", options: [["shared", "Shared local model (Ollama)"], ["anthropic", "Claude (Anthropic)"], ["openai", "OpenAI"]]},
    {path: "model", type: "text", label: "Model", help: "leave empty for the provider's default (Claude: claude-sonnet-5-5, OpenAI: gpt-5)"},
    {path: "api_key", type: "secret", label: "API key", help: "paste to set or replace; leave empty to keep the stored key"},
    {path: "clear_key", type: "bool", label: "Remove stored key"},
    {path: "judge_budget_per_day", type: "number", label: "Daily judgement budget", help: "jobs per day the shared model judges for you; 0 = unlimited"},
  ],
  skills: [
    {path: "have", type: "tags", label: "Skills you also have", help: "beyond what your resume text mentions; names from the skill catalogue"},
    {path: "not_have", type: "tags", label: "Skills not to claim", help: "mentioned in your resume but you would rather not claim"},
  ],
  companies: [
    {path: "greenhouse", type: "tags", label: "Greenhouse boards", help: "board slugs from boards.greenhouse.io/<slug>, comma-separated"},
    {path: "workday", type: "table", label: "Workday sites", columns: ["company", "site"], help: "site = tenant:wdN:site, from https://<tenant>.<wdN>.myworkdayjobs.com/<site>"},
    {path: "oracle", type: "table", label: "Oracle Recruiting sites", columns: ["company", "host", "site_number"], help: "from https://<host>/hcmUI/CandidateExperience/en/sites/<site_number>"},
  ],
  skill_catalogue: [
    {path: "skills", type: "maplist", label: "Skill catalogue", columns: ["skill", "phrases that mean it"], help: "phrases are matched whole-word, case-insensitive"},
  ],
  filters_template: [
    {section: "Crawl location rules", help: "applied to every posting before it is parsed, for all users"},
    {path: "location.countries", type: "tags", label: "Countries covered", help: "ISO codes, e.g. US, CA, GB. Postings clearly elsewhere are dropped for everyone; unclear locations are kept. Empty = no country filter"},
    {path: "location.allowed_states", type: "tags", label: "Crawl states", help: "the crawl keeps postings in these states, remote ones, and unreadable locations. Empty = whole US"},
    {path: "location.extra_us", type: "tags", label: "Extra US place names"},
    {path: "location.extra_non_us", type: "tags", label: "Extra non-US place names"},
    {section: "Title keywords a new profile starts from", help: "the crawl also parses every title any user's scope asks for"},
    {path: "title.include", type: "list", label: "Keep titles containing"},
    {path: "title.exclude", type: "list", label: "Drop titles containing"},
  ],
};
let configs = {}, cfgName = null, cfgYaml = false;
const getPath = (o, p) => p.split(".").reduce((x, k) => (x == null ? undefined : x[k]), o);
const setPath = (o, p, v) => { const ks = p.split("."); let x = o; for (const k of ks.slice(0, -1)) { if (x[k] == null || typeof x[k] !== "object") x[k] = {}; x = x[k]; } x[ks.at(-1)] = v; };
const splitTags = s => s.split(",").map(x => x.trim()).filter(Boolean);
const numOrNull = s => s.trim() === "" ? null : Number(s);

async function loadConfigs() {
  const list = await api("/api/config");
  configs = Object.fromEntries(list.map(c => [c.name, c]));
  if (!cfgName) cfgName = list[0].name;
  const btn = c => `<button data-cfg="${c.name}" class="${c.name === cfgName ? "on" : ""}" title="${esc(c.description)}">${esc(c.title)}${c.path ? ` <span class="match" style="color:var(--muted)">${esc(c.path.replace("config/", ""))}</span>` : ""}</button>`;
  const shared = list.filter(c => c.kind === "file");
  $("cfgnav").innerHTML = `<span class="match" style="color:var(--muted);align-self:center">You:</span>` + list.filter(c => c.kind === "profile").map(btn).join("") +
    `<button data-cfg="resume" class="${cfgName === "resume" ? "on" : ""}" title="Your resumes (markdown)">Resumes</button>` +
    `<button data-cfg="account" class="${cfgName === "account" ? "on" : ""}" title="Change your password">Account</button>` +
    `<button id="cfg-wizard" title="Walk through resume, roles, location and skills again">Set up again…</button>` +
    (shared.length ? `<span class="match" style="color:var(--muted);align-self:center;margin-left:12px">Shared:</span>` + shared.map(btn).join("") : "");
  if (cfgName === "resume") renderResume(); else if (cfgName === "account") renderAccount(); else renderConfig();
}
function renderConfig() {
  const c = configs[cfgName]; if (!c) return;
  const isFile = c.kind === "file";
  const head = `<div class="cfghead"><b>${esc(c.title)}</b>${isFile ? `<span class="path">${esc(c.path)}</span>` : ""}<span class="match" style="color:var(--muted)">${esc(c.description)}</span>
    <span class="right">${isFile ? `<label class="f"><input type="checkbox" id="cfg-yaml" ${cfgYaml ? "checked" : ""}> edit as YAML</label>` : ""}<button id="cfg-reload">Discard changes</button><button class="save" id="cfg-save">Save</button></span></div>
    <div class="cfgmsg" id="cfg-msg"></div>`;
  let body;
  if (cfgYaml && isFile) body = `<textarea class="yaml" id="cfg-text" spellcheck="false">${esc(c.text)}</textarea>`;
  else body = `<div class="card">${(CONFIG_FORMS[cfgName] || []).map(f => renderField(f, c.data)).join("") || '<div class="sub">No form for this file; use "edit as YAML".</div>'}</div>`;
  $("cfgbody").innerHTML = head + body;
}
const tagsIn = (id, list) => `<input type="text" id="${id}" value="${esc((list || []).join(", "))}">`;
function renderField(f, data) {
  if (f.section) return `<h3 style="margin:16px 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)">${esc(f.section)}</h3>${f.help ? `<div class="sub" style="font-size:12px">${esc(f.help)}</div>` : ""}`;
  const v = getPath(data, f.path), id = "cfg-" + f.path.replace(/\./g, "-");
  let input;
  switch (f.type) {
    case "bool": input = `<label class="f"><input type="checkbox" id="${id}" ${v ? "checked" : ""}> ${v ? "on" : "off"}</label>`; break;
    case "number": input = `<input type="number" id="${id}" value="${v ?? ""}" step="any">`; break;
    case "text": input = `<input type="text" id="${id}" value="${esc(v ?? "")}">`; break;
    case "secret": input = `<input type="password" id="${id}" value="" autocomplete="new-password" placeholder="${getPath(data, "has_key") ? "a key is stored" : "not set"}">`; break;
    case "choice": input = `<select id="${id}">${f.options.map(([val, lab]) => `<option value="${esc(val)}" ${val === v ? "selected" : ""}>${esc(lab)}</option>`).join("")}</select>`; break;
    case "tags": input = tagsIn(id, v); break;
    case "list": input = `<textarea id="${id}" spellcheck="false">${esc((v || []).join("\n"))}</textarea>`; break;
    case "numbers": input = `<div class="nums" id="${id}">${Object.entries(v || {}).map(([k, n]) => `<label>${esc(k)} <input type="number" class="num" data-key="${esc(k)}" value="${n ?? ""}" step="${f.step || "any"}"></label>`).join("")}</div>`; break;
    case "table": input = `<table class="rows" id="${id}" data-cols="${f.columns.join(",")}"><tr>${f.columns.map(c => `<th>${esc(c)}</th>`).join("")}<th></th></tr>
      ${(v || []).map(row => tableRow(f.columns, row)).join("")}</table><button class="addrow" data-add="table" data-for="${id}">+ add row</button>`; break;
    case "maplist": input = `<table class="rows" id="${id}"><tr><th>${esc(f.columns[0])}</th><th>${esc(f.columns[1])}</th><th></th></tr>
      ${Object.entries(v || {}).map(([k, list]) => mapRow(k, list)).join("")}</table><button class="addrow" data-add="maplist" data-for="${id}">+ add skill</button>`; break;
    case "families": input = `<table class="rows" id="${id}"><tr><th>family</th><th>weight</th><th>title keywords (comma-separated)</th><th></th></tr>
      ${Object.entries(v || {}).map(([k, fam]) => familyRow(k, fam)).join("")}</table><button class="addrow" data-add="families" data-for="${id}">+ add family</button>`; break;
  }
  return `<div class="field cfg" data-path="${f.path}" data-type="${f.type}"><label for="${id}">${esc(f.label)}</label><div>${input}</div>${f.help ? `<div class="help">${esc(f.help)}</div>` : ""}</div>`;
}
const delBtn = '<td><button class="del" data-del title="remove">×</button></td>';
const tableRow = (cols, row = {}) => `<tr>${cols.map(c => `<td><input type="text" data-col="${esc(c)}" value="${esc(row[c] ?? "")}"></td>`).join("")}${delBtn}</tr>`;
const mapRow = (k = "", list = []) => `<tr><td style="width:220px"><input type="text" data-key value="${esc(k)}"></td><td><input type="text" data-list value="${esc(list.join(", "))}"></td>${delBtn}</tr>`;
const familyRow = (k = "", fam = {}) => `<tr><td style="width:200px"><input type="text" data-key value="${esc(k)}"></td><td><input type="number" class="num" data-weight value="${fam.weight ?? 1}" step="0.05"></td><td><input type="text" data-list value="${esc((fam.keywords || []).join(", "))}"></td>${delBtn}</tr>`;

let resumeSel = null, resumeList = [];
async function renderResume() {
  resumeList = await api("/api/resumes");
  if (resumeSel == null || !resumeList.some(r => r.id === resumeSel)) resumeSel = (resumeList.find(r => r.active) || resumeList[0] || {}).id ?? null;
  const cur = resumeSel != null ? await api("/api/resumes/" + resumeSel) : null;
  const rows = resumeList.map(r => `<tr class="${r.id === resumeSel ? "sel" : ""}" data-resume="${r.id}">
      <td><a href="#" data-open-resume="${r.id}">${esc(r.filename)}</a>${r.active ? ' <span class="tag">scoring</span>' : ""}</td>
      <td>${r.chars.toLocaleString()}</td><td>${esc(r.uploaded_at.slice(0, 16).replace("T", " "))}</td>
      <td>${r.applications ? `<a href="#" data-filter-resume="${r.id}">${r.applications}</a>` : "0"}</td>
      <td><a href="/api/resumes/${r.id}?download=true" title="Download .md">⬇</a> ${r.active ? "" : `<button data-activate="${r.id}" title="Score against this one">use for scoring</button> <button class="del" data-delete="${r.id}" title="Remove">×</button>`}</td></tr>`).join("");
  $("cfgbody").innerHTML = `<div class="cfghead"><b>Resumes</b><span class="match" style="color:var(--muted)">Your library of markdown resumes. One is used for scoring; any can be recorded as the one you applied with.</span></div>
    <div class="card"><table class="rows"><tr><th>file</th><th>chars</th><th>added</th><th>applications</th><th></th></tr>${rows || '<tr><td colspan="5" class="sub">No resumes yet.</td></tr>'}</table>
      <div class="actions" style="margin-bottom:0"><label class="f"><input type="file" id="resume-file" accept=".md,.markdown,.txt,text/markdown,text/plain" hidden><button id="resume-upload">Upload .md file…</button></label><button id="resume-new">New from blank</button></div></div>
    ${cur ? `<div class="cfghead"><b>${esc(cur.filename || "resume")}</b>${cur.active ? '<span class="tag">scoring</span>' : ""}
      <span class="right"><input type="text" id="resume-name" value="${esc(cur.filename || "resume.md")}" style="width:220px" title="file name"><label class="f"><input type="checkbox" id="resume-activate" ${cur.active ? "checked" : ""}> use for scoring</label><button class="save" id="resume-save">Save as new version</button></span></div>
      <div class="cfgmsg" id="cfg-msg"></div>
      <textarea class="yaml" id="resume-text" spellcheck="false">${esc(cur.text)}</textarea>` : `<div class="cfghead"><b>New resume</b><span class="right"><input type="text" id="resume-name" value="resume.md" style="width:220px"><label class="f"><input type="checkbox" id="resume-activate" checked> use for scoring</label><button class="save" id="resume-save">Save</button></span></div>
      <div class="cfgmsg" id="cfg-msg"></div><textarea class="yaml" id="resume-text" spellcheck="false" placeholder="# Your name&#10;&#10;## Experience&#10;..."></textarea>`}`;
}
async function saveResume() {
  const msg = $("cfg-msg"); msg.className = "cfgmsg"; msg.textContent = "Saving…"; $("resume-save").disabled = true;
  try {
    const activate = $("resume-activate").checked;
    const r = await api("/api/resume", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: $("resume-text").value, filename: $("resume-name").value, activate})});
    resumeSel = r.resume_id;
    await renderResume();
    const m = $("cfg-msg"); m.className = "cfgmsg ok";
    m.innerHTML = r.unchanged ? "Identical to a resume already in the library." : activate ? `Saved and indexed into ${r.chunks} sections. Re-score every job: <button id="cfg-after">Run score_jobs_fast now</button>` : "Saved to the library.";
    $("cfg-after")?.addEventListener("click", async () => {
      const run = await api("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({command: "run", tool: "score_jobs_fast", args: {rescore: true}})});
      showTab("run"); watch(run.id);
    });
  } catch (e) { msg.className = "cfgmsg err"; msg.textContent = "Not saved: " + e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""); $("resume-save").disabled = false; }
}
async function resumeAction(e) {
  const open = e.target.closest("a[data-open-resume]"); if (open) { e.preventDefault(); resumeSel = +open.dataset.openResume; return renderResume(); }
  const filt = e.target.closest("a[data-filter-resume]"); if (filt) { e.preventDefault(); $("flag").value = "applied"; filterResume = +filt.dataset.filterResume; showTab("jobs"); return loadJobs(false); }
  const act = e.target.closest("button[data-activate]"); if (act) { await api(`/api/resumes/${act.dataset.activate}/activate`, {method: "POST"}); resumeSel = +act.dataset.activate; return renderResume(); }
  const del = e.target.closest("button[data-delete]"); if (del) { if (!confirm("Remove this resume from your library?")) return; await api(`/api/resumes/${del.dataset.delete}`, {method: "DELETE"}); if (resumeSel === +del.dataset.delete) resumeSel = null; return renderResume(); }
  if (e.target.id === "resume-upload") return $("resume-file").click();
  if (e.target.id === "resume-new") { resumeSel = null; const list = await api("/api/resumes"); resumeList = list; $("resume-name").value = "resume.md"; $("resume-text").value = ""; $("resume-activate").checked = !list.length; $("cfg-msg").textContent = "Blank resume: paste or type, then Save."; return; }
}
$("cfgbody").addEventListener("change", e => {
  if (e.target.id !== "resume-file" || !e.target.files[0]) return;
  const f = e.target.files[0];
  f.text().then(text => { $("resume-text").value = text; $("resume-name").value = f.name.replace(/\.(txt|markdown)$/i, ".md"); $("cfg-msg").textContent = `Loaded ${f.name}; click Save to keep it.`; });
});
function renderAccount() {
  $("cfgbody").innerHTML = `<div class="cfghead"><b>Account</b><span class="match" style="color:var(--muted)">${esc(me.email)}${isAdmin() ? " · admin" : ""}</span></div>
    <div class="card" style="max-width:480px"><h3>Change password</h3>
      <div class="field cfg"><label>Current password</label><div><input type="password" id="pw-old" autocomplete="current-password"></div></div>
      <div class="field cfg"><label>New password</label><div><input type="password" id="pw-new" autocomplete="new-password"></div><div class="help">at least 10 characters; other browsers are signed out</div></div>
      <div class="actions"><button class="yes on" id="pw-save">Change password</button><span class="cfgmsg" id="cfg-msg"></span></div></div>
    <div class="card" style="max-width:480px"><h3>Delete account</h3><div class="sub">Removes your profile, resumes, marks, notes and judgements for good. The shared job pool is unaffected.</div>
      <div class="field cfg"><label>Password</label><div><input type="password" id="del-pw" autocomplete="current-password"></div></div>
      <div class="actions"><button class="no" id="acct-delete">Delete my account</button><span class="cfgmsg" id="del-msg"></span></div></div>`;
}
async function deleteAccount() {
  if (!confirm("Delete your account and everything in it? This cannot be undone.")) return;
  const msg = $("del-msg"); msg.className = "cfgmsg";
  try { await api("/api/auth/delete", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({password: $("del-pw").value})}); location.href = "/login"; }
  catch (e) { msg.className = "cfgmsg err"; msg.textContent = e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""); }
}
async function savePassword() {
  const msg = $("cfg-msg"); msg.className = "cfgmsg";
  try { await api("/api/auth/password", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({current: $("pw-old").value, new: $("pw-new").value})}); msg.className = "cfgmsg ok"; msg.textContent = "Password changed."; $("pw-old").value = $("pw-new").value = ""; }
  catch (e) { msg.className = "cfgmsg err"; msg.textContent = e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""); }
}
async function saveResume() {
  const msg = $("cfg-msg"); msg.className = "cfgmsg"; msg.textContent = "Saving and indexing…"; $("resume-save").disabled = true;
  try {
    const r = await api("/api/resume", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: $("resume-text").value, filename: "resume.md"})});
    msg.className = "cfgmsg ok";
    msg.innerHTML = r.unchanged ? "Unchanged." : `Saved and indexed into ${r.chunks} sections. Re-score every job: <button id="cfg-after">Run score_jobs_fast now</button>`;
    if (!r.unchanged) $("cfg-after").addEventListener("click", async () => {
      const run = await api("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({command: "run", tool: "score_jobs_fast", args: {rescore: true}})});
      showTab("run"); watch(run.id);
    });
  } catch (e) { msg.className = "cfgmsg err"; msg.textContent = "Not saved: " + e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""); }
  $("resume-save").disabled = false;
}
function collectConfig() {
  const data = JSON.parse(JSON.stringify(configs[cfgName].data));
  for (const el of $("cfgbody").querySelectorAll(".field.cfg")) {
    const p = el.dataset.path, t = el.dataset.type, box = el.querySelector("div");
    let v;
    if (t === "bool") v = box.querySelector("input").checked;
    else if (t === "number") v = numOrNull(box.querySelector("input").value);
    else if (t === "text" || t === "secret") v = box.querySelector("input").value;
    else if (t === "choice") v = box.querySelector("select").value;
    else if (t === "tags") v = splitTags(box.querySelector("input").value);
    else if (t === "list") v = box.querySelector("textarea").value.split("\n").map(s => s.trim()).filter(Boolean);
    else if (t === "numbers") { v = {}; for (const i of box.querySelectorAll("input")) v[i.dataset.key] = numOrNull(i.value); }
    else if (t === "table") { v = []; for (const r of box.querySelectorAll("tr:has(input)")) { const row = {}; for (const i of r.querySelectorAll("input")) if (i.value.trim()) row[i.dataset.col] = i.value.trim(); if (Object.keys(row).length) v.push(row); } }
    else if (t === "maplist") { v = {}; for (const r of box.querySelectorAll("tr:has(input)")) { const k = r.querySelector("[data-key]").value.trim(); if (k) v[k] = splitTags(r.querySelector("[data-list]").value); } }
    else if (t === "families") { v = {}; for (const r of box.querySelectorAll("tr:has(input)")) { const k = r.querySelector("[data-key]").value.trim(); if (k) v[k] = {weight: Number(r.querySelector("[data-weight]").value || 0), keywords: splitTags(r.querySelector("[data-list]").value)}; } }
    setPath(data, p, v);
  }
  return data;
}
async function saveConfig() {
  const msg = $("cfg-msg"), btn = $("cfg-save");
  const body = cfgYaml && configs[cfgName].kind === "file" ? {text: $("cfg-text").value} : {data: collectConfig()};
  btn.disabled = true; msg.className = "cfgmsg"; msg.textContent = "Saving…";
  try {
    const c = await api("/api/config/" + cfgName, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    configs[cfgName] = c; renderConfig();
    const m = $("cfg-msg"); m.className = "cfgmsg ok";
    m.innerHTML = `Saved ${esc(c.path)} at ${new Date().toLocaleTimeString()}.` + (c.after ? ` ${esc(c.after.note)}: <button id="cfg-after">Run ${esc(c.after.tool)} now</button>` : "");
    if (c.after) $("cfg-after").addEventListener("click", async () => {
      try { const r = await api("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({command: "run", tool: c.after.tool, args: c.after.args})}); showTab("run"); watch(r.id); }
      catch (e) { $("cfg-msg").className = "cfgmsg err"; $("cfg-msg").textContent = e.message; }
    });
    if (cfgName === "skills" && selected != null) select(selected);
    if (cfgName === "scope") loadSummary();
  } catch (e) {
    msg.className = "cfgmsg err"; msg.textContent = "Not saved: " + e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, "");
    btn.disabled = false;
  }
}
$("cfgnav").addEventListener("click", e => { if (e.target.id === "cfg-wizard") return openOnboarding(); const b = e.target.closest("button[data-cfg]"); if (b) { cfgName = b.dataset.cfg; loadConfigs(); } });
$("cfgbody").addEventListener("click", e => {
  if (e.target.id === "cfg-save") return saveConfig();
  if (e.target.id === "resume-save") return saveResume();
  if (cfgName === "resume") return resumeAction(e);
  if (e.target.id === "pw-save") return savePassword();
  if (e.target.id === "acct-delete") return deleteAccount();
  if (e.target.id === "cfg-reload") return loadConfigs();
  const d = e.target.closest("button[data-del]"); if (d) { d.closest("tr").remove(); return; }
  const a = e.target.closest("button[data-add]");
  if (a) {
    const table = $(a.dataset.for);
    const html = a.dataset.add === "table" ? tableRow(table.dataset.cols.split(",")) : a.dataset.add === "maplist" ? mapRow() : familyRow();
    table.insertAdjacentHTML("beforeend", html); table.lastElementChild.querySelector("input").focus();
  }
});
$("cfgbody").addEventListener("change", e => {
  if (e.target.id === "cfg-yaml") { cfgYaml = e.target.checked; renderConfig(); }
  if (e.target.type === "checkbox" && e.target.closest(".field.cfg")) e.target.parentElement.lastChild.textContent = " " + (e.target.checked ? "on" : "off");
});

