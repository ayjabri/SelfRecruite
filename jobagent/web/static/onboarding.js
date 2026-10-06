// first-run wizard: resume -> roles & level -> where -> skills -> score
// Opens on its own when the signed-in user has no resume yet; "Set up again" in Settings reopens it.
const SENIORITY_LEVELS = [["director", "Director / Executive Director / SVP"], ["vp_plus", "Managing Director and above"], ["lead", "Lead / Principal / Head of"], ["senior", "Senior / Vice President (bank style)"], ["mid", "Mid-level / Associate"], ["entry", "Entry level"]];
let ob = {step: 0, profile: null, resumeText: "", families: {}, level: "director", states: [], remote: true, have: new Set(), notHave: new Set(), skills: null};

async function openOnboarding() {
  const [cfg, resume] = await Promise.all([api("/api/config"), api("/api/resume")]);
  ob.profile = Object.fromEntries(cfg.filter(c => c.kind === "profile").map(c => [c.name, c.data]));
  ob.resumeText = resume ? resume.text : "";
  const fams = ob.profile.targets.title_families || {};
  const include = new Set((ob.profile.scope.title || {}).include || []);
  ob.families = Object.fromEntries(Object.entries(fams).map(([k, f]) => [k, (f.keywords || []).some(w => include.has(w)) || !include.size]));
  const sen = ob.profile.targets.seniority || {};
  ob.level = (Object.entries(sen).sort((a, b) => b[1] - a[1])[0] || ["director"])[0];
  ob.states = (ob.profile.scope.location || {}).allowed_states || [];
  ob.remote = (ob.profile.scope.location || {}).remote_ok !== false;
  ob.have = new Set(ob.profile.skills.have || []); ob.notHave = new Set(ob.profile.skills.not_have || []);
  ob.step = 0;
  $("onboarding").hidden = false;
  renderOnboarding();
}
function closeOnboarding() { $("onboarding").hidden = true; }

const FAMILY_LABELS = {A_product_delivery_ba: "Product / project management, business analysis", B_ai_product: "AI product management", C_quant: "Quantitative analytics and modeling", D_ai_ml: "AI and machine learning engineering", E_treasury_alm: "Treasury, ALM, interest-rate risk", F_technology: "Software, data and analytics engineering"};
const famLabel = k => FAMILY_LABELS[k] || k.replace(/^[A-Z]_/, "").replace(/_/g, " ");

function renderOnboarding() {
  const steps = ["Resume", "Roles", "Where", "Skills", "Done"];
  const nav = steps.map((s, i) => `<span class="obstep ${i === ob.step ? "on" : i < ob.step ? "done" : ""}">${i + 1}. ${s}</span>`).join("");
  let body;
  if (ob.step === 0) body = `<h2>Paste your resume</h2><p class="sub">Markdown works best: headings become the sections that get matched against each job's requirements. Plain text is fine too.</p>
    <textarea id="ob-resume" class="yaml" style="min-height:40vh" placeholder="# Your name&#10;&#10;## Experience&#10;### Company — Title (2020–present)&#10;- what you did...">${esc(ob.resumeText)}</textarea>`;
  else if (ob.step === 1) body = `<h2>What roles are you after?</h2><p class="sub">Tick every family that fits. Their title keywords define which jobs count for you; you can fine-tune the words later under Settings → Scope.</p>
    ${Object.entries(ob.profile.targets.title_families || {}).map(([k, f]) => `<label class="obrow"><input type="checkbox" data-fam="${esc(k)}" ${ob.families[k] ? "checked" : ""}> <b>${esc(famLabel(k))}</b><span class="match" style="color:var(--muted)"> · ${esc((f.keywords || []).slice(0, 6).join(", "))}${(f.keywords || []).length > 6 ? ", …" : ""}</span></label>`).join("")}
    <h3 style="margin-top:18px">Level you are targeting</h3>
    <select id="ob-level">${SENIORITY_LEVELS.map(([v, l]) => `<option value="${v}" ${v === ob.level ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  else if (ob.step === 2) body = `<h2>Where can you work?</h2><p class="sub">Jobs located elsewhere stay out of your list. Leave the states empty for anywhere in the US.</p>
    <div class="field cfg"><label>States</label><div><input type="text" id="ob-states" value="${esc(ob.states.join(", "))}" placeholder="NY, NJ, CT"></div><div class="help">two-letter codes, comma-separated</div></div>
    <div class="field cfg"><label>Remote jobs</label><div><label class="f"><input type="checkbox" id="ob-remote" ${ob.remote ? "checked" : ""}> count wherever they are based</label></div></div>`;
  else if (ob.step === 3) {
    const detected = ob.skills?.detected || [], cat = ob.skills?.catalogue || [];
    const rest = cat.filter(s => !detected.includes(s));
    body = `<h2>Your technical skills</h2><p class="sub">Found in your resume (untick anything you would rather not claim), plus anything else you have.</p>
      <h3>From your resume</h3><div class="skills">${detected.map(s => `<label class="chip ok"><input type="checkbox" data-nothave="${esc(s)}" ${ob.notHave.has(s) ? "" : "checked"}> ${esc(s)}</label>`).join("") || '<span class="none">Nothing recognised yet; the catalogue is below.</span>'}</div>
      <h3 style="margin-top:14px">Also have</h3><div class="skills">${rest.map(s => `<label class="chip"><input type="checkbox" data-have="${esc(s)}" ${ob.have.has(s) ? "checked" : ""}> ${esc(s)}</label>`).join("")}</div>`;
  } else body = `<h2>All set</h2><p class="sub">Your profile is saved. Scoring runs now: scope, fast score against your resume, then the LLM judges your best matches. It takes a few minutes; the Run tab shows progress and the tracker fills in as it goes.</p>`;
  $("onboarding").innerHTML = `<div class="obcard"><div class="obnav">${nav}<button class="theme" id="ob-close" title="Close" style="margin-left:auto;color:var(--text);border-color:var(--line)">×</button></div>
    <div class="obbody">${body}</div><div class="cfgmsg" id="ob-msg"></div>
    <div class="actions"><button id="ob-back" ${ob.step === 0 ? "disabled" : ""}>Back</button><button class="yes on" id="ob-next">${ob.step === 4 ? "Open the tracker" : ob.step === 3 ? "Save and score my jobs" : "Next"}</button></div></div>`;
}

function seniorityFor(level) {
  const order = ["entry", "mid", "senior", "lead", "vp_plus", "director"];
  const i = order.indexOf(level);
  return Object.fromEntries(order.map((k, j) => [k, j === i ? 1.0 : Math.abs(j - i) === 1 ? 0.6 : Math.abs(j - i) === 2 ? 0.3 : 0.1]).concat([["unknown", 0.4]]));
}

async function onboardingNext() {
  const msg = $("ob-msg"); msg.className = "cfgmsg"; msg.textContent = "";
  try {
    if (ob.step === 0) {
      const text = $("ob-resume").value;
      if (text.trim().length < 200) throw new Error("that is too short to be a resume");
      msg.textContent = "Saving and indexing…";
      const r = await api("/api/resume", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text, filename: "resume.md"})});
      ob.resumeText = text; msg.textContent = r.unchanged ? "" : `Indexed into ${r.chunks} sections.`;
    } else if (ob.step === 1) {
      for (const c of document.querySelectorAll("#onboarding input[data-fam]")) ob.families[c.dataset.fam] = c.checked;
      if (!Object.values(ob.families).some(Boolean)) throw new Error("tick at least one role family");
      ob.level = $("ob-level").value;
    } else if (ob.step === 2) {
      ob.states = $("ob-states").value.split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
      ob.remote = $("ob-remote").checked;
      ob.skills = await api("/api/skills");
    } else if (ob.step === 3) {
      ob.notHave = new Set([...document.querySelectorAll("#onboarding input[data-nothave]")].filter(c => !c.checked).map(c => c.dataset.nothave));
      ob.have = new Set([...document.querySelectorAll("#onboarding input[data-have]")].filter(c => c.checked).map(c => c.dataset.have));
      await saveOnboarding();
      await api("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({command: "score"})});
    } else { closeOnboarding(); showTab("run"); refreshRuns(); loadSummary(); loadJobs(false); return; }
    ob.step += 1; renderOnboarding();
  } catch (e) { msg.className = "cfgmsg err"; msg.textContent = e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""); }
}

async function saveOnboarding() {
  const put = (name, data) => api("/api/config/" + name, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({data})});
  const fams = ob.profile.targets.title_families || {};
  const include = [...new Set(Object.entries(fams).filter(([k]) => ob.families[k]).flatMap(([, f]) => f.keywords || []))];
  const scope = {...ob.profile.scope, title: {...(ob.profile.scope.title || {}), include}, location: {allowed_states: ob.states, remote_ok: ob.remote}};
  const targets = {...ob.profile.targets, seniority: seniorityFor(ob.level),
    title_families: Object.fromEntries(Object.entries(fams).map(([k, f]) => [k, {...f, weight: ob.families[k] ? (f.weight && f.weight >= 0.5 ? f.weight : 1.0) : 0}]))};
  const gates = {...(targets.gates || {}), allowed_states: ob.states};
  targets.gates = gates;
  await put("scope", scope); await put("targets", targets); await put("skills", {have: [...ob.have], not_have: [...ob.notHave]});
}

$("onboarding").addEventListener("click", e => {
  if (e.target.id === "ob-next") onboardingNext();
  else if (e.target.id === "ob-back") { ob.step = Math.max(0, ob.step - 1); renderOnboarding(); }
  else if (e.target.id === "ob-close") closeOnboarding();
});
