// ---- Settings → Company groups: the user's own groups of companies and what interests them in each
let grp = null, grpTimer = null;
const blankInterest = () => ({families: [], keywords: [], otherwise: "cap"});
// famLabel (role family -> readable name) comes from onboarding.js
const coKey = x => x.toLowerCase().replace(/[^a-z0-9]/g, "");   // "Master Card" and "Mastercard" are one company, as on the server

function renderGroups(c) {
  grp = {
    groups: Object.entries(c.data.company_groups || {}).map(([name, g]) => ({name, companies: [...(g.companies || [])], interest: {...blankInterest(), ...(g.interest || {})}})),
    own: Object.entries(c.data.company_interest || {}).map(([company, i]) => ({company, interest: {...blankInterest(), ...i}})),
    families: {...c.families}, companies: c.companies || [], suggested: c.suggested_groups || {}, missing: {...(c.missing_families || {})}, add: [], counts: {},
  };
  drawGroups(); previewGroups();
}

function interestEditor(i) {
  const fams = Object.keys(grp.families).map(f => `<label class="chip gfam" title="${esc((grp.families[f] || []).join(", "))}"><input type="checkbox" data-fam="${esc(f)}" ${i.families.includes(f) ? "checked" : ""}> ${esc(famLabel(f))}</label>`).join(" ");
  return `<div class="field cfg"><label>Roles you would take</label><div class="skills">${fams || '<span class="sub">No role families yet: add some under Scoring → Title families.</span>'}</div>
      <div class="help">a job qualifies if its title matches any ticked role family (hover a family for its keywords)</div></div>
    <div class="field cfg"><label>or jobs mentioning</label><div><input type="text" class="g-kw" value="${esc(i.keywords.join(", "))}" placeholder="e.g. alm, liquidity, irrbb"></div>
      <div class="help">keywords, comma-separated; looked for in the title and the requirement bullets. Nothing ticked and no keywords = no restriction</div></div>
    <div class="field cfg"><label>Every other job there</label><div><select class="g-other"><option value="cap" ${i.otherwise !== "hide" ? "selected" : ""}>stays listed, score capped at 30</option><option value="hide" ${i.otherwise === "hide" ? "selected" : ""}>is hidden (out of your scope)</option></select></div></div>`;
}
const countsLine = key => `<div class="sub g-counts" data-counts="${esc(key)}">${countsText(grp.counts[key])}</div>`;
function countsText(c) {
  if (!c) return "";
  const parts = [`<b>${c.jobs}</b> jobs in your scope`, `<b>${c.qualify}</b> qualify`];
  if (c.capped) parts.push(`<b>${c.capped}</b> capped at 30`);
  if (c.hidden) parts.push(`<b>${c.hidden}</b> hidden`);
  if (c.conflicts) parts.push(`<span style="color:var(--warn)"><b>${c.conflicts}</b> you marked interested conflict with this</span>`);
  return parts.join(" · ");
}

function drawGroups() {
  const c = configs.groups, assigned = new Set(grp.groups.flatMap(g => g.companies.map(coKey)));
  const free = grp.companies.filter(x => !assigned.has(coKey(x)));
  const head = `<div class="cfghead"><b>${esc(c.title)}</b><span class="match" style="color:var(--muted)">${esc(c.description)}</span>
    <span class="right"><button id="cfg-reload">Discard changes</button><button class="save" id="cfg-save">Save</button></span></div><div class="cfgmsg" id="cfg-msg"></div>`;
  const missing = Object.entries(grp.missing).map(([f, fam]) => `<div class="sub" style="margin:0 0 10px">Your role families have no <b>${esc(famLabel(f))}</b> (${esc((fam.keywords || []).join(", "))}).
    <button data-g-addfam="${esc(f)}">Add it to my role families</button></div>`).join("");
  const empty = grp.groups.length ? "" : `<div class="card"><div class="sub">You have no groups yet. A group is your own set of companies (for example Banks, Tech); you can then say which roles interest you in each.</div>
    <div class="actions" style="margin:10px 0 0">${Object.keys(grp.suggested).length ? `<button class="yes on" id="g-suggest">Start from suggested groups (${esc(Object.keys(grp.suggested).join(", "))})</button>` : ""}<button id="g-new">+ New group</button></div></div>`;
  const groups = grp.groups.map((g, n) => `<div class="card gcard" data-kind="group" data-n="${n}">
      <div class="pairhead"><input type="text" class="g-name" value="${esc(g.name)}" placeholder="Group name" style="font-weight:700;max-width:260px"><span class="btns"><button data-g-delgroup="${n}">Delete group</button></span></div>
      <div class="field cfg"><label>Companies</label><div class="skills">${g.companies.map(x => `<span class="chip" data-co="${esc(x)}">${esc(x)} <a href="#" data-g-delco="${esc(x)}" title="remove from this group">×</a></span>`).join(" ")}
        ${free.length ? `<select class="g-addco"><option value="">+ add a company…</option>${free.map(x => `<option>${esc(x)}</option>`).join("")}</select>` : ""}</div></div>
      ${interestEditor(g.interest)}${countsLine("group:" + g.name)}</div>`).join("");
  const own = grp.own.map((o, n) => `<div class="card gcard" data-kind="company" data-n="${n}" data-company="${esc(o.company)}">
      <div class="pairhead"><b>${esc(o.company)}</b><span class="match" style="color:var(--muted)">replaces its group's statement</span><span class="btns"><button data-g-delown="${n}">Remove</button></span></div>
      ${interestEditor(o.interest)}${countsLine("company:" + o.company)}</div>`).join("");
  const ownFree = grp.companies.filter(x => !grp.own.some(o => coKey(o.company) === coKey(x)));
  $("cfgbody").innerHTML = head + missing + empty + groups +
    (grp.groups.length ? `<div class="actions" style="margin:0 0 14px"><button id="g-new">+ New group</button>${free.length ? `<span class="sub">In no group: ${free.map(esc).join(", ")}</span>` : ""}</div>` : "") +
    `<h3 style="margin:18px 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)">One company that differs from its group</h3>
     <div class="sub" style="margin-bottom:10px">For example: at Citi, only Treasury roles or jobs mentioning ALM. The company also gets its own learned weights in your score.</div>` + own +
    `<div class="actions"><select id="g-addown"><option value="">+ add a company…</option>${ownFree.map(x => `<option>${esc(x)}</option>`).join("")}</select><span class="sub" id="g-conflicts"></span></div>`;
}

// read the page back into grp, so edits survive a redraw
function syncGroups() {
  const interest = card => ({families: [...card.querySelectorAll("input[data-fam]:checked")].map(i => i.dataset.fam),
    keywords: splitTags(card.querySelector(".g-kw").value), otherwise: card.querySelector(".g-other").value});
  for (const card of $("cfgbody").querySelectorAll(".gcard")) {
    const n = +card.dataset.n;
    if (card.dataset.kind === "group") { grp.groups[n].name = card.querySelector(".g-name").value.trim(); grp.groups[n].interest = interest(card); }
    else grp.own[n].interest = interest(card);
  }
}
function collectGroups() {
  syncGroups();
  const live = i => i.families.length || i.keywords.length ? i : {};
  return {
    company_groups: Object.fromEntries(grp.groups.filter(g => g.name).map(g => [g.name, {companies: g.companies, interest: live(g.interest)}])),
    company_interest: Object.fromEntries(grp.own.map(o => [o.company, live(o.interest)])),
    add_families: grp.add,
  };
}
async function previewGroups() {
  clearTimeout(grpTimer);
  if (cfgName !== "groups" || !grp) return;
  try {
    const r = await api("/api/groups/preview", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({data: collectGroups()})});
    grp.counts = Object.fromEntries(r.segments.map(s => [s.kind + ":" + s.name, s]));
    for (const el of $("cfgbody").querySelectorAll("[data-counts]")) el.innerHTML = countsText(grp.counts[el.dataset.counts]);
    const box = $("g-conflicts"); if (box) box.innerHTML = r.conflicts ? `${r.conflicts} jobs you marked interested conflict with these rules; after saving and scoring, <a href="#" id="g-review">review them</a>.` : "";
    const msg = $("cfg-msg"); if (msg && msg.dataset.preview) { msg.textContent = ""; delete msg.dataset.preview; }
  } catch (e) {
    const msg = $("cfg-msg"); if (msg) { msg.className = "cfgmsg err"; msg.dataset.preview = "1"; msg.textContent = e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""); }
  }
}
const redrawGroups = () => { drawGroups(); previewGroups(); };

$("cfgbody").addEventListener("click", e => {
  if (cfgName !== "groups" || !grp) return;
  const t = e.target;
  if (t.id === "g-review") { e.preventDefault(); $("label").value = "conflicts"; showTab("jobs"); return loadJobs(false); }
  const hit = t.id === "g-new" || t.id === "g-suggest" || t.dataset.gDelgroup != null || t.dataset.gDelown != null || t.dataset.gDelco != null || t.dataset.gAddfam != null;
  if (!hit) return;
  e.preventDefault(); syncGroups();
  if (t.id === "g-new") grp.groups.push({name: "", companies: [], interest: blankInterest()});
  else if (t.id === "g-suggest") {
    const known = new Map(grp.companies.map(x => [coKey(x), x]));
    grp.groups = Object.entries(grp.suggested).map(([name, g]) => ({name, interest: blankInterest(),   // only companies that have jobs here, under the name they are stored with
      companies: [...new Set((g.companies || []).map(x => known.get(coKey(x))).filter(Boolean))]}));
  }
  else if (t.dataset.gDelgroup != null) { if (!confirm("Delete this group? Its companies become ungrouped.")) return; grp.groups.splice(+t.dataset.gDelgroup, 1); }
  else if (t.dataset.gDelown != null) grp.own.splice(+t.dataset.gDelown, 1);
  else if (t.dataset.gDelco != null) { const g = grp.groups[+t.closest(".gcard").dataset.n]; g.companies = g.companies.filter(x => x !== t.dataset.gDelco); }
  else if (t.dataset.gAddfam != null) { const f = t.dataset.gAddfam; grp.families[f] = grp.missing[f].keywords || []; grp.add.push(f); delete grp.missing[f]; }
  redrawGroups();
  if (t.id === "g-new") [...$("cfgbody").querySelectorAll(".gcard[data-kind=group] .g-name")].pop()?.focus();
});
$("cfgbody").addEventListener("change", e => {
  if (cfgName !== "groups" || !grp) return;
  const t = e.target;
  if (t.classList.contains("g-addco") && t.value) { syncGroups(); grp.groups[+t.closest(".gcard").dataset.n].companies.push(t.value); return redrawGroups(); }
  if (t.id === "g-addown" && t.value) { syncGroups(); grp.own.push({company: t.value, interest: blankInterest()}); return redrawGroups(); }
  if (t.closest(".gcard")) { clearTimeout(grpTimer); grpTimer = setTimeout(previewGroups, 300); }
});
$("cfgbody").addEventListener("input", e => {
  if (cfgName !== "groups" || !grp || !e.target.closest(".gcard")) return;
  clearTimeout(grpTimer); grpTimer = setTimeout(previewGroups, 600);
});
