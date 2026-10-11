// ---- Score check: how the fast score compares with what the user marked
let checkThreshold = 60;
const pct = (a, b) => b ? Math.round(100 * a / b) + "%" : "–";
const aucText = a => a == null ? "–" : a.toFixed(2);
const jobLink = r => `<a href="#" data-check-job="${r.job_id}">${esc(r.title)}</a> <span class="match" style="color:var(--muted)">${esc(r.company)}</span>`;
const bar = (share, color) => `<span class="checkbar"><span style="width:${Math.round(100 * share)}%;background:var(--${color})"></span></span>`;

async function renderCheck() {
  const box = $("check");
  if (!box.innerHTML) box.innerHTML = '<h2>Score check</h2><div class="sub">Loading…</div>';
  let c;
  try { c = await api("/api/score-check?threshold=" + checkThreshold); } catch (e) { box.innerHTML = `<h2>Score check</h2><div class="cfgmsg err">${esc(e.message)}</div>`; return; }
  const h = c.held_out, st = c.status, t = c.threshold, f = c.forward;
  const intro = `<h2>Score check</h2><div class="sub" style="margin-bottom:12px">How your fit score compares with the ${c.marks} jobs you marked (${c.interested} interested, ${c.marks - c.interested} not).
    The score you see on a marked job has already learned from that mark, so the model is judged here on <b>held-out scores</b>: each marked job scored by a model trained without it.
    <label class="f" style="margin-left:10px">A score counts as "interested" from <input type="number" id="check-threshold" min="1" max="99" value="${t}" style="width:64px"></label></div>`;
  if (!st.trained_at) { box.innerHTML = intro + `<div class="card">No model has been trained on your marks yet: it needs at least ${st.min_per_class} jobs marked each way (you have ${st.interested} interested, ${st.not_interested} not). Until then your score is the rule score from your profile.</div>` + cappedCard(c); return; }
  if (!h.marks) { box.innerHTML = intro + `<div class="card">Your model was trained before this page existed. Run <b>score my jobs</b> once (Run tab) and the held-out numbers appear here.</div>` + cappedCard(c); return; }
  const stale = st.out_of_date ? ` <span style="color:var(--warn)">Your marks or settings changed since: the next "score my jobs" retrains it.</span>` : "";
  const headline = `<div class="card"><h3>Does the score predict your marks?</h3>
    <div class="checknums">
      <div><b>${aucText(h.auc)}</b><span>held-out AUC of your trained model<br>(0.5 = coin flip, 1.0 = perfect)</span></div>
      <div><b>${aucText(h.rule_auc)}</b><span>the rule score alone,<br>on the same jobs</span></div>
      <div><b>${h.precision_pct ?? "–"}%</b><span>of the ${h.scored_high} jobs scored ${t}+<br>you marked interested</span></div>
      <div><b>${h.recall_pct ?? "–"}%</b><span>of your ${h.interested} interested jobs<br>scored ${t}+</span></div>
    </div>
    <div class="sub" style="margin-top:10px">Trained on ${st.labels_at_training ?? "?"} marks on ${esc(st.trained_at.slice(0, 16).replace("T", " "))} UTC.${stale}
      ${h.pending ? ` ${h.pending} ${h.pending === 1 ? "mark is" : "marks are"} not counted here yet (made since that training, or on jobs never scored).` : ""}</div></div>`;
  const most = Math.max(1, ...c.bands.map(b => b.marks));
  const bands = `<div class="card"><h3>By score band (held-out)</h3><table class="rows checktable"><tr><th>score</th><th>marked jobs</th><th>interested</th><th>share interested</th><th></th></tr>
    ${c.bands.map(b => `<tr><td>${b.low}–${b.high}</td><td>${b.marks}</td><td>${b.interested}</td><td>${pct(b.interested, b.marks)}</td><td style="width:45%">${bar(b.marks ? b.interested / b.marks : 0, "yes")}</td></tr>`).join("")}</table>
    <div class="sub" style="margin-top:8px">A score that works climbs from top to bottom: few interested in the low bands, most in the high ones.</div></div>`;
  const list = (rows, empty) => rows.length ? `<table class="rows checktable">${rows.map(r => `<tr><td style="width:44px"><b>${r.held}</b></td><td>${jobLink(r)}${(r.gates || []).length ? ` <span class="chip warn" title="${esc(r.gates.join("; "))}">capped</span>` : ""}</td></tr>`).join("")}</table>` : `<div class="sub">${empty}</div>`;
  const disagree = `<div class="checkpair">
    <div class="card"><h3>Interested, but scored below ${t}</h3>${list(c.interested_scored_low, "None: every job you are interested in scores high.")}</div>
    <div class="card"><h3>Not interested, but scored ${t}+</h3>${list(c.not_interested_scored_high, "None.")}</div></div>
    <div class="sub" style="margin:-6px 0 14px">The number is the held-out score. Click a job to open it: a wrong mark can be flipped there; a pattern (a kind of role at a kind of company) is a rule to add under Settings → Company groups.</div>`;
  const slice = (title, rows) => `<div class="card"><h3>${title}</h3><table class="rows checktable"><tr><th></th><th>marks</th><th>interested</th><th>score and mark agree</th><th>AUC</th></tr>
    ${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${r.marks}</td><td>${r.interested}</td><td>${r.agree} of ${r.marks} (${r.agree_pct}%) ${bar(r.agree / r.marks, r.agree_pct >= 75 ? "yes" : r.agree_pct >= 60 ? "warn" : "no")}</td><td>${aucText(r.auc)}</td></tr>`).join("")}</table></div>`;
  const slices = `<div class="checkpair">${slice("By your company groups", c.by_group)}${slice("By company", c.by_company)}</div>
    <div class="sub" style="margin:-6px 0 14px">"Agree" = scored ${t}+ and marked interested, or scored below ${t} and marked not interested. AUC is shown where there are at least 5 marks each way.</div>`;
  const forward = `<div class="card"><h3>Forward test: the score when you marked</h3>${f.all.marks ? `
    <div class="checknums"><div><b>${f.all.agree} of ${f.all.marks}</b><span>marks agreed with the score<br>shown at that moment (${pct(f.all.agree, f.all.marks)})</span></div>
      <div><b>${f.since_training.marks ? f.since_training.agree + " of " + f.since_training.marks : "–"}</b><span>since the last training</span></div>
      <div><b>${aucText(f.auc)}</b><span>AUC of the score when marked</span></div></div>
    ${f.misses.length ? `<table class="rows checktable" style="margin-top:10px"><tr><th>score then</th><th>you marked</th><th></th></tr>${f.misses.map(r => `<tr><td style="width:80px"><b>${r.score_when_marked}</b></td><td style="width:120px">${esc(r.mark)}</td><td>${jobLink(r)}</td></tr>`).join("")}</table>` : ""}`
    : `<div class="sub">Nothing yet. From now on every mark records the score the job had at that moment, before the model could learn from it: the most honest test. It fills in as you mark jobs.</div>`}</div>`;
  const hist = c.history.length ? `<div class="card"><h3>At each training</h3><table class="rows checktable"><tr><th>trained</th><th>marks</th><th>interested</th><th>model AUC</th><th>rule AUC</th><th></th></tr>
    ${c.history.slice().reverse().map(x => `<tr><td>${esc(x.trained_at.slice(0, 16).replace("T", " "))}</td><td>${x.labels}</td><td>${x.interested}</td><td><b>${aucText(x.cv_auc)}</b></td><td>${aucText(x.rule_score_auc)}</td><td style="width:35%">${bar(Math.max(0, (x.cv_auc - 0.5) * 2), "yes")}</td></tr>`).join("")}</table>
    <div class="sub" style="margin-top:8px">Held-out AUC at each training, newest first; the bar runs from 0.5 (chance) to 1.0.</div></div>` : "";
  box.innerHTML = intro + headline + bands + disagree + slices + cappedCard(c) + forward + hist;
}
function cappedCard(c) {
  const k = c.capped;
  if (!k.interested) return "";
  return `<div class="card"><h3>Interested, but held at 30 or hidden</h3><div class="sub" style="margin-bottom:8px">${k.interested} jobs you marked interested are capped or hidden by a gate or one of your rules, whatever the model thinks of them.</div>
    <table class="rows checktable"><tr><th>held down by</th><th>interested marks</th></tr>${k.reasons.map(r => `<tr><td>${esc(r.reason)}</td><td>${r.marks}</td></tr>`).join("")}</table>
    <div class="sub" style="margin-top:8px"><a href="#" id="check-conflicts">List the ones against your company and group rules</a> · gates are under Settings → Scoring → Hard gates.</div></div>`;
}
$("check").addEventListener("click", e => {
  const a = e.target.closest("a[data-check-job]");
  if (a) { e.preventDefault(); showTab("jobs"); return select(+a.dataset.checkJob); }
  if (e.target.id === "check-conflicts") { e.preventDefault(); $("label").value = "conflicts"; showTab("jobs"); loadJobs(false); }
});
$("check").addEventListener("change", e => {
  if (e.target.id !== "check-threshold") return;
  checkThreshold = Math.min(99, Math.max(1, +e.target.value || 60)); renderCheck();
});
