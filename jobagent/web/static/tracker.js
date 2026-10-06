// job tracker: list, detail, marks, notes, possible duplicates
let jobs = [], selected = null, filterResume = 0, myResumes = [];

const money = j => {
  if (j.comp_min == null && j.comp_max == null) return "";
  const f = n => j.comp_period === "hour" ? "$" + n : "$" + Math.round(n / 1000) + "k";
  return f(j.comp_min ?? j.comp_max) + "–" + f(j.comp_max ?? j.comp_min) + (j.comp_period === "hour" ? "/hr" : "");
};

async function loadSummary() {
  const s = await api("/api/summary");
  const done = s.interested + s.not_interested;
  $("progress").innerHTML = `<b>${done}</b> of ${s.in_scope} marked · <b>${s.interested}</b> interested · <b>${s.not_interested}</b> not · <b>${s.favorites}</b> ♥ · <b>${s.applied}</b> applied`;
  $("dupcount").textContent = s.pending_duplicates;
  const sel = $("company"), cur = sel.value;
  sel.innerHTML = '<option value="">All companies</option>' + s.companies.map(c => `<option>${esc(c)}</option>`).join("");
  sel.value = cur;
}

async function loadJobs(keep) {
  const p = new URLSearchParams({
    q: $("q").value, company: $("company").value, label: $("label").value, flag: $("flag").value, sort: $("sort").value, posted: $("posted").value,
    min_score: $("min_score").value || 0, scope: $("scope").checked ? "all" : "in_scope", resume_id: filterResume || 0,
  });
  const data = await api("/api/jobs?" + p);
  jobs = data.jobs;
  renderList(data.total);
  if (keep && jobs.some(j => j.id === selected)) return;
  if (jobs.length) select(jobs[0].id); else { selected = null; $("detail").innerHTML = '<div class="empty">No jobs match these filters.</div>'; }
}

function renderList(total) {
  const shown = (jobs.length < total ? `Showing ${jobs.length} of ${total} jobs` : `${total} jobs`) + (filterResume ? ` applied with ${esc((myResumes.find(r => r.id === filterResume) || {}).filename || "resume")} <a href="#" id="clear-resume-filter">×</a>` : "");
  $("list").innerHTML = `<div class="count">${shown}</div>` + jobs.map(j => `
    <div class="row ${j.label || ""} ${j.id === selected ? "sel" : ""}" data-id="${j.id}">
      <div class="scores"><div class="score ${j.gated ? "gated" : ""}" title="${j.gated ? "Capped by a hard gate" : "Fit score"}">${j.fit_score ?? "–"}</div>
        ${j.match_score != null ? `<div class="mscore" title="Match score from the LLM judgement">M ${j.match_score}</div>` : ""}</div>
      <div style="flex:1;min-width:0"><div class="t">${esc(j.title)}</div>
        <div class="m">${esc(j.company)}${j.location ? " · " + esc(j.location) : ""}</div>
        <div class="m">${[j.seniority !== "unknown" ? j.seniority : "", j.workplace !== "unknown" ? j.workplace : "", money(j), "posted " + j.posted].filter(Boolean).map(esc).join(" · ")}</div>
      </div>
      <div class="tags"><button class="heart ${j.favorite ? "on" : ""}" data-fav="${j.id}" title="Favorite (F)">${j.favorite ? "♥" : "♡"}</button>
        ${j.has_keywords ? '<span class="tag kw" title="Keywords extracted">K</span>' : ""}${j.applied ? `<span class="tag" ${j.applied_resume_id ? `data-resume="${j.applied_resume_id}" style="cursor:pointer"` : ""} title="${esc((myResumes.find(r => r.id === j.applied_resume_id) || {}).filename || "resume not recorded")}">applied${j.applied_resume_id ? " ✓" : ""}</span>` : ""}${j.has_notes ? '<span class="tag note">note</span>' : ""}</div>
    </div>`).join("");
}

async function loadMyResumes() { try { myResumes = await api("/api/resumes"); } catch (e) { myResumes = []; } }
function resumePicker(j) {
  if (!j.applied_at) return "";
  const opts = myResumes.map(r => `<option value="${r.id}" ${r.id === j.applied_resume_id ? "selected" : ""}>${esc(r.filename)}</option>`).join("");
  const used = myResumes.find(r => r.id === j.applied_resume_id);
  return `<label class="f" title="Which resume you sent">with <select id="applied-resume"><option value="0">${myResumes.length ? "resume not recorded" : "no resumes yet"}</option>${opts}</select></label>
    <button id="apply-upload" title="Upload the .md you sent for this job; it joins your library">Upload .md…</button>
    ${used ? `<span class="resumelink" data-open-resume="${used.id}" title="Read or edit it below">${esc(used.filename)} ▾</span>` : ""}`;
}
async function uploadForJob(file) {
  if (!file || selected == null) return;
  const text = await file.text();
  const name = file.name.replace(/\.(txt|markdown)$/i, ".md");
  const r = await api("/api/resume", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text, filename: name, activate: false})});
  await loadMyResumes();
  await annotate(selected, {applied: true, applied_resume_id: r.resume_id});
}
async function select(id) {
  if (selected != null && selected !== id && $("resume-save") && !$("resume-save").disabled && !confirm("Discard unsaved changes to the resume?")) return;
  if (notesTimer && selected != null && selected !== id) { clearTimeout(notesTimer); const t = $("notes"); if (t) annotate(selected, {notes: t.value}); notesTimer = null; }
  selected = id;
  document.querySelectorAll(".row").forEach(r => r.classList.toggle("sel", +r.dataset.id === id));
  document.querySelector(".row.sel")?.scrollIntoView({block: "nearest"});
  const j = await api("/api/jobs/" + id);
  if (selected !== id) return;
  renderDetail(j);
}

function scoreColor(score) {           // 0 = red, 50 = amber, 100 = green
  return `hsl(${Math.round(score * 1.2)} 70% 42%)`;
}
function dial(score, gated) {
  // half-circle gauge: radius 50, centre (60,60), arc length = pi * 50
  const len = Math.PI * 50, filled = len * Math.max(0, Math.min(100, score)) / 100, color = scoreColor(score);
  return `<div class="card dial" title="${gated ? "Capped at 30 by a hard gate" : "Overall fit score"}">
    <svg viewBox="0 0 120 70" aria-label="fit score ${score}">
      <path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="var(--bar)" stroke-width="12" stroke-linecap="round"/>
      <path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="${color}" stroke-width="12" stroke-linecap="round"
            stroke-dasharray="${filled.toFixed(1)} ${len.toFixed(1)}"/>
    </svg>
    <div class="big" style="color:${color}">${score}</div>
    <div class="lbl">${gated ? "capped" : "fit"}</div></div>`;
}
function skillBoxes(sk) {
  const chips = (list, cls) => list.length ? list.map(s => `<span class="chip ${cls}">${esc(s)}</span>`).join("") : "";
  const m = sk?.matching || [], g = sk?.missing || [];
  return `<div class="skillrow">
    <div class="card"><h3>Technical skills you have · ${m.length}</h3><div class="skills">${chips(m, "ok") || '<span class="none">None of the catalogued skills mentioned.</span>'}</div></div>
    <div class="card"><h3>Technical skills missing · ${g.length}</h3><div class="skills">${chips(g, "gap") || '<span class="none">Nothing asked for that you lack.</span>'}</div></div>
  </div>`;
}
function renderDetail(j) {
  const d = j.fit_details || {}, c = d.components || {};
  const names = {required: "Required match", preferred: "Preferred match", title_family: "Title family", seniority: "Seniority", domain: "Domain"};
  const comps = Object.keys(names).filter(k => k in c).map(k => `
    <div>${names[k]}</div><div class="track"><div class="fill" style="width:${Math.round(c[k] * 100)}%"></div></div><div class="num">${Math.round(c[k] * 100)}</div>`).join("");
  const bullets = label => (d.bullets || []).filter(b => b.label === label);
  const verdicts = new Map(((j.match_details || {}).items || []).map(i => [i.label + "|" + i.text, i]));
  const verdictHtml = b => {
    const v = verdicts.get(b.label + "|" + b.text); if (!v) return "";
    return `<div class="evidence"><span class="verdict ${v.cls}">${v.cls} ${v.pct}</span>${v.evidence_quote ? `<q>${esc(v.evidence_quote)}</q> — ${esc(v.evidence_section || "")}` : ""}${v.missing ? `<div>Missing: ${esc(v.missing)}</div>` : ""}</div>`;
  };
  const bulletList = bs => `<ul class="b">${bs.map(b => `<li><div class="s">${Math.round(b.score * 100)}</div>
      <div>${esc(b.text)}${verdictHtml(b)}<div class="match">closest resume section: ${esc(b.matched)} (similarity ${b.similarity})</div></div></li>`).join("")}</ul>`;
  const facts = [
    j.location, j.workplace !== "unknown" && j.workplace, j.seniority !== "unknown" && j.seniority,
    j.employment_type && j.employment_type !== "unknown" && j.employment_type.replace("_", " "),
    j.min_years != null && j.min_years + "+ years", money(j),
    j.visa && j.visa !== "unstated" && "visa sponsorship: " + j.visa,
    j.posted ? "posted " + j.posted : "first seen " + j.first_seen,
  ].filter(Boolean).map(f => `<span class="chip">${esc(f)}</span>`).join("");
  const gates = (d.gates || []).map(g => `<span class="chip warn">capped at 30: ${esc(g)}</span>`).join("");
  const other = (j.sections || []).filter(s => s.label === "responsibilities" || s.label === "other");
  const req = bullets("required"), pref = bullets("preferred");
  $("detail").innerHTML = `
    <div class="titlerow"><h2>${esc(j.title)}</h2>${j.requisition_id ? `<span class="req" title="Requisition ID">Req ${esc(j.requisition_id)}</span>` : ""}</div>
    <div class="sub">${esc(j.company)}</div>
    <div class="actions">
      <button class="yes ${j.label === "interested" ? "on" : ""}" data-label="interested">Interested <kbd>I</kbd></button>
      <button class="no ${j.label === "not_interested" ? "on" : ""}" data-label="not_interested">Not interested <kbd>X</kbd></button>
      ${j.label ? '<button data-label="clear">Clear <kbd>U</kbd></button>' : ""}
      <button class="fav ${j.favorite ? "on" : ""}" data-annotate="favorite">${j.favorite ? "♥" : "♡"} Favorite <kbd>F</kbd></button>
      <button class="app ${j.applied_at ? "on" : ""}" data-annotate="applied">${j.applied_at ? "✓ Applied " + esc(j.applied_at) : "Mark applied"} <kbd>A</kbd></button>${resumePicker(j)}
      ${j.url ? `<a href="${esc(j.url)}" target="_blank" rel="noopener">Open posting ↗</a>` : ""}
      <span class="match" style="color:var(--muted);margin-left:auto"><kbd>J</kbd>/<kbd>K</kbd> next / previous</span>
    </div>
    <div class="facts">${facts}${gates}</div>
    ${skillBoxes(j.skills)}
    <div class="card" id="kw-card"><h3>Keywords the AI found <span class="match" style="color:var(--muted);text-transform:none;letter-spacing:0">· what an applicant-tracking system scans for</span></h3><div id="kw-body" class="sub">loading…</div></div>
    <div class="card"><h3>Your notes</h3><textarea id="notes" placeholder="Anything you want to remember about this job: who you spoke to, why it stands out, follow-ups…">${esc(j.notes)}</textarea><div class="saved" id="notes-saved"></div></div>
    ${j.fit_score == null ? '<div class="card">This job has no fit score (it was filtered out before scoring).</div>' : `
    <div class="fitrow">
    <div class="card"><h3>Fit score${d.title_family ? " · " + esc(d.title_family.replace(/_/g, " ")) : ""}</h3>
      ${d.preference != null ? `<p style="margin:0 0 10px">Learned from your marks: <b>${Math.round(d.preference * 100)}%</b> likely to interest you
        <span class="match" style="color:var(--muted)">(model trained on ${d.model?.labels ?? "?"} marked jobs). The rule-based score below is ${d.rule_score}.</span></p>` : ""}
      <div class="comp">${comps}</div></div>
    ${dial(j.fit_score, (d.gates || []).length > 0)}
    </div>`}
    ${j.match_score != null ? `<div class="card"><h3>Match score ${j.match_score} · judged by the LLM${j.scored_at ? " on " + esc(j.scored_at) : ""}</h3>
      <p style="margin:0">${esc(j.match_explanation || "")}</p></div>` : ""}
    ${req.length ? `<div class="card"><h3>Requirements, matched against your resume${d.sections_quality === "unlabelled" ? " (headings not recognised; all bullets shown)" : ""}</h3>${bulletList(req)}</div>` : ""}
    ${pref.length ? `<div class="card"><h3>Preferred</h3>${bulletList(pref)}</div>` : ""}
    ${other.map(s => `<div class="card"><h3>${esc(s.heading || "Other")}</h3><ul class="plain">${s.bullets.map(b => `<li>${esc(b)}</li>`).join("")}</ul></div>`).join("")}
    ${(j.postings || []).length > 1 ? `<div class="card"><h3>${j.postings.length} postings merged into this job</h3><ul class="plain">${j.postings.map(p =>
      `<li>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title || "posting")}</a>` : esc(p.title || "posting")}${p.location ? " · " + esc(p.location) : ""} <span class="match" style="color:var(--muted)">(${esc(p.source)})</span></li>`).join("")}</ul></div>` : ""}
    ${j.comp_text ? `<div class="card"><h3>Pay, as stated</h3>${esc(j.comp_text)}</div>` : ""}
    <div class="bottomrow">
      <div class="card"><h3>Full posting text</h3><div class="md">${j.description_html || renderMarkdown(j.description, {plain: true, headings: (j.sections || []).map(s => s.heading)})}</div></div>
      ${j.applied_resume_id ? `<div class="card" id="resume-card"><h3>Resume you applied with</h3><div class="sub">loading…</div></div>` : ""}
    </div>`;
  if (j.applied_resume_id) loadResumeCard(j.applied_resume_id);
  loadKeywords(j.id);
  $("detail").scrollTop = 0;
}

async function mark(label) {
  if (selected == null) return;
  const id = selected, i = jobs.findIndex(j => j.id === id);
  await api(`/api/jobs/${id}/label`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({label})});
  if (i >= 0) jobs[i].label = label === "clear" ? null : label;
  loadSummary();
  const next = label !== "clear" && jobs[i + 1];
  renderList(jobs.length);
  select(next ? next.id : id);
}

async function annotate(id, body) {
  const r = await api(`/api/jobs/${id}/annotate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = jobs.find(x => x.id === id);
  if (j) { j.favorite = r.favorite; j.applied = !!r.applied_at; j.has_notes = !!r.notes; j.applied_resume_id = r.applied_resume_id; }
  renderList(jobs.length);
  loadSummary();
  if (id === selected && (body.favorite != null || body.applied != null || body.applied_resume_id != null)) select(id);
  return r;
}
function toggle(kind) {
  if (selected == null) return;
  const j = jobs.find(x => x.id === selected);
  if (!j) return;
  annotate(selected, kind === "favorite" ? {favorite: !j.favorite} : {applied: !j.applied});
}
let notesTimer, notesFor;
function notesChanged() {
  const id = selected, text = $("notes").value;
  $("notes-saved").textContent = "…";
  clearTimeout(notesTimer);
  notesTimer = setTimeout(async () => {
    try {
      await annotate(id, {notes: text});
      if (selected === id) $("notes-saved").textContent = "Saved " + new Date().toLocaleTimeString();
    } catch (e) {
      if (selected === id) $("notes-saved").textContent = "Not saved: " + e.message.slice(0, 120) + " (is the server up to date? restart `jobagent ui`)";
    }
  }, 600);
}
function move(step) {
  const i = jobs.findIndex(j => j.id === selected), n = jobs[i + step];
  if (n) select(n.id);
}

// ---- possible duplicates
let pairs = [];
const selectedPairs = () => [...document.querySelectorAll("#dups input[data-pair]:checked")].map(c => +c.dataset.pair);
function updateDupBar() {
  const n = selectedPairs().length;
  $("dup-n").textContent = `${n} of ${pairs.length} selected`;
  $("dup-same").disabled = $("dup-diff").disabled = n === 0;
  document.querySelectorAll("#dups .pairc").forEach(c => c.classList.toggle("sel", c.querySelector("input").checked));
}
async function renderDups() {
  pairs = await api("/api/duplicates");
  const job = x => `<div class="job"><div class="t">${esc(x.title)}${x.req ? ` <span class="req">Req ${esc(x.req)}</span>` : ""}</div><div class="m">${esc(x.location || "no location")}${x.fit_score != null ? " · score " + x.fit_score : ""}${x.label ? " · " + esc(x.label.replace("_", " ")) : ""}</div>
    <a href="#" data-open="${x.id}">open in tracker</a></div>`;
  $("dups").innerHTML = pairs.length ? `<h2>Possible duplicates</h2><div class="sub">Same company, similar text. "Same opening" merges the pair into one record (both postings stay linked); "Different" keeps both. Tick pairs to act on several at once.</div>
    <div class="dupbar"><label class="f"><input type="checkbox" id="dup-all"> select all</label>
      <button id="dup-same" class="yes" disabled>Same opening → merge selected</button><button id="dup-diff" disabled>Different → keep both</button>
      <span class="n" id="dup-n"></span></div>` +
    pairs.map(p => `<div class="card pairc" data-pair="${p.id}"><input type="checkbox" data-pair="${p.id}"><div>
      <div class="pairhead"><span>${esc(p.company)} · similarity ${p.similarity.toFixed(3)}</span>
      <span class="btns"><button data-decision="same">Same opening</button><button data-decision="different">Different</button></span></div>
      <div class="pair">${job(p.a)}${job(p.b)}</div></div></div>`).join("")
    : '<div class="empty">No pending pairs.</div>';
  if (pairs.length) updateDupBar();
}
async function decidePairs(ids, decision) {
  const r = await api("/api/duplicates/bulk", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({pair_ids: ids, decision})});
  for (const id of r.decided) document.querySelector(`#dups .pairc[data-pair="${id}"]`)?.remove();
  pairs = pairs.filter(p => !r.decided.includes(p.id));
  if (!pairs.length) $("dups").innerHTML = '<div class="empty">No pending pairs.</div>'; else updateDupBar();
  loadSummary(); if (decision === "same") loadJobs(true);
}
$("dups").addEventListener("change", e => {
  if (e.target.id === "dup-all") { document.querySelectorAll("#dups input[data-pair]").forEach(c => { c.checked = e.target.checked; }); }
  if (e.target.matches("input[type=checkbox]")) updateDupBar();
});

$("dups").addEventListener("click", async e => {
  const o = e.target.closest("a[data-open]");
  if (o) { e.preventDefault(); showTab("jobs"); if (!jobs.some(j => j.id === +o.dataset.open)) { $("scope").checked = true; await loadJobs(true); } select(+o.dataset.open); return; }
  if (e.target.id === "dup-same") return decidePairs(selectedPairs(), "same");
  if (e.target.id === "dup-diff") return decidePairs(selectedPairs(), "different");
  const b = e.target.closest("button[data-decision]"); if (!b) return;
  decidePairs([+b.closest(".pairc").dataset.pair], b.dataset.decision);
});
$("list").addEventListener("click", e => { if (e.target.id === "clear-resume-filter") { e.preventDefault(); filterResume = 0; return loadJobs(false); } const r = e.target.closest(".row"); if (r) select(+r.dataset.id); });
$("detail").addEventListener("click", e => {
  const b = e.target.closest("button[data-label]"); if (b) return mark(b.dataset.label);
  const a = e.target.closest("button[data-annotate]"); if (a) toggle(a.dataset.annotate);
});
$("detail").addEventListener("input", e => { if (e.target.id === "notes") notesChanged(); });
$("detail").addEventListener("change", e => { if (e.target.id === "applied-resume" && selected != null) annotate(selected, {applied_resume_id: +e.target.value}); });
$("detail").addEventListener("click", e => {
  if (e.target.id === "apply-upload") return $("apply-file").click();
  const o = e.target.closest("[data-open-resume]"); if (o) scrollToResume();
});
$("apply-file").addEventListener("change", e => { uploadForJob(e.target.files[0]); e.target.value = ""; });
$("list").addEventListener("click", e => {
  const tag = e.target.closest(".tag[data-resume]"); if (tag) { e.stopPropagation(); const row = tag.closest(".row"); select(+row.dataset.id).then(scrollToResume); }
}, true);

// ---- keywords: the first job-agent result; the button is a chat message
let kwTimer = null;
const KW_ORDER = ["skill", "tool", "domain", "certification", "title", "soft"];
async function loadKeywords(jobId) {
  clearTimeout(kwTimer);
  const box = $("kw-body"); if (!box || selected !== jobId) return;
  const [kw, chat] = await Promise.all([api(`/api/jobs/${jobId}/keywords`), api(`/api/jobs/${jobId}/messages`)]);
  if (selected !== jobId) return;
  const pending = chat.pending.length ? chat.pending[0] : null;
  const last = chat.messages.length ? chat.messages[chat.messages.length - 1] : null;
  const btn = `<button id="kw-extract" class="yes ${kw ? "" : "on"}" ${pending ? "disabled" : ""}>${pending ? (pending.status === "queued" ? "Waiting for the AI…" : "Extracting…") : kw ? "Extract again" : "Extract keywords"}</button>`;
  let body = "";
  if (kw) {
    const groups = KW_ORDER.filter(c => kw.keywords.some(k => k.category === c));
    body = `<p style="margin:0 0 8px">${esc(kw.summary || "")}</p>` + groups.map(c => `<div class="kwgroup"><span class="kwcat">${c}</span><span class="skills">${kw.keywords.filter(k => k.category === c).map(k =>
      `<span class="chip ${k.have ? "ok" : "gap"} w${k.weight}" title="${esc(k.evidence || "")}${k.have ? " · on your resume" : " · not on your resume"}">${esc(k.term)}</span>`).join("")}</span></div>`).join("") +
      `<div class="sub" style="margin-top:8px">${kw.keywords.length} keywords · green = in your skills, red = missing · ${esc(kw.model || "")} · ${esc(kw.created_at.slice(0, 16).replace("T", " "))} <a href="#" id="kw-copy">copy as list</a></div>`;
  } else if (!pending) body = `<div class="sub">Not extracted yet. The AI reads the full posting plus the terms similar roles ask for, and returns the keywords a resume for this job should contain.</div>`;
  if (last && last.role === "assistant" && last.error) body += `<div class="cfgmsg err">${esc(last.error)}</div>`;
  box.className = ""; box.innerHTML = `<div class="actions" style="margin:0 0 10px">${btn}${pending ? `<span class="sub">${pending.status === "queued" ? "queued in the " + esc(pending.lane) + " lane" : "running"}</span>` : ""}</div>${body}`;
  if (pending) kwTimer = setTimeout(() => loadKeywords(jobId), 3000);
}
async function extractKeywords() {
  const id = selected; if (id == null) return;
  const box = $("kw-body");
  try { await api(`/api/jobs/${id}/chat`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({message: "extract keywords"})}); loadKeywords(id); }
  catch (e) { if (box) box.insertAdjacentHTML("beforeend", `<div class="cfgmsg err">${esc(e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""))}</div>`); }
}
$("detail").addEventListener("click", e => {
  if (e.target.id === "kw-extract") return extractKeywords();
  if (e.target.id === "kw-copy") {
    e.preventDefault(); const link = e.target;
    api(`/api/jobs/${selected}/keywords`).then(async kw => {
      const text = kw.keywords.map(k => k.term).join(", ");
      if (await copyText(text)) { link.textContent = "copied"; return; }
      // copying is blocked here: show the list so it can be selected by hand
      let box = $("kw-copybox");
      if (!box) { box = document.createElement("textarea"); box.id = "kw-copybox"; box.readOnly = true; box.style.cssText = "width:100%;min-height:70px;margin-top:6px;font:inherit"; link.parentElement.after(box); }
      box.value = text; box.focus(); box.select(); link.textContent = "select and copy from the box below";
    });
  }
});

// ---- the resume used for an application, shown beside the full posting text and editable in place
let rcard = {id: null, text: "", filename: ""};
async function loadResumeCard(id) {
  const box = $("resume-card"); if (!box) return;
  try {
    const r = await api("/api/resumes/" + id);
    rcard = {id: r.id, text: r.text, filename: r.filename || `resume-${r.id}.md`, active: r.active};
    box.innerHTML = `<div class="resumehead"><span class="name" id="resume-name">${esc(rcard.filename)}${r.active ? ' <span class="tag">scoring</span>' : ""}</span><input type="text" id="resume-rename" hidden>
        <button id="resume-renamebtn" title="Rename">Rename</button><button id="resume-edit">Edit</button><button id="resume-discard" hidden>Discard</button><button class="save" id="resume-save" hidden disabled>Save</button>
        <a href="/api/resumes/${r.id}?download=true" title="Download .md" style="color:var(--accent)">⬇</a></div>
      <div class="cfgmsg" id="resume-msg"></div><div class="md" id="resume-view">${renderMarkdown(r.text)}</div><textarea id="resume-text" spellcheck="false" hidden>${esc(r.text)}</textarea>`;
  } catch (e) { box.innerHTML = `<div class="sub">Could not load the resume: ${esc(e.message)}</div>`; }
}
function resumeDirty(dirty) { $("resume-save").disabled = !dirty; }
function resumeMode(edit) {  // preview by default; the editor only when asked
  $("resume-view").hidden = edit; $("resume-text").hidden = !edit;
  $("resume-edit").hidden = edit; $("resume-save").hidden = $("resume-discard").hidden = !edit;
  if (edit) $("resume-text").focus();
}
async function saveResumeCard() {
  const msg = $("resume-msg"); msg.className = "cfgmsg"; msg.textContent = "Saving…";
  const body = {text: $("resume-text").value};
  if (!$("resume-rename").hidden) body.filename = $("resume-rename").value;
  try {
    const r = await api("/api/resumes/" + rcard.id, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    rcard.text = body.text; if (body.filename) rcard.filename = body.filename.endsWith(".md") ? body.filename : body.filename + ".md";
    $("resume-name").innerHTML = esc(rcard.filename) + (r.active ? ' <span class="tag">scoring</span>' : ""); $("resume-rename").hidden = true; $("resume-name").hidden = false;
    msg.className = "cfgmsg ok"; msg.textContent = "Saved" + (r.active && r.chunks ? `; re-indexed into ${r.chunks} sections` : "") + ".";
    $("resume-view").innerHTML = renderMarkdown(rcard.text); resumeDirty(false); resumeMode(false);
    await loadMyResumes(); renderList(jobs.length);
  } catch (e) { msg.className = "cfgmsg err"; msg.textContent = "Not saved: " + e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""); }
}
function scrollToResume() { $("resume-card")?.scrollIntoView({behavior: "smooth", block: "start"}); }
$("detail").addEventListener("click", e => {
  if (e.target.id === "resume-save") saveResumeCard();
  else if (e.target.id === "resume-edit") { $("resume-text").value = rcard.text; resumeMode(true); }
  else if (e.target.id === "resume-discard") { $("resume-text").value = rcard.text; $("resume-rename").hidden = true; $("resume-name").hidden = false; resumeDirty(false); resumeMode(false); $("resume-msg").textContent = ""; }
  else if (e.target.id === "resume-renamebtn") { $("resume-rename").value = rcard.filename; $("resume-rename").hidden = false; $("resume-name").hidden = true; $("resume-rename").focus(); resumeMode(true); resumeDirty(true); }
});
$("detail").addEventListener("input", e => { if (e.target.id === "resume-text" || e.target.id === "resume-rename") resumeDirty(true); });
