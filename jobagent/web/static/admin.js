// admin tab: accounts, the shared pool, recent stage runs and the queue
async function renderAdmin() {
  const [users, ov] = await Promise.all([api("/api/users"), api("/api/admin/overview")]);
  const p = ov.pool;
  const byDay = {};
  for (const r of ov.stage_runs) (byDay[r.date] = byDay[r.date] || []).push(r);
  const fmt = r => `<span class="${r.status === "ok" ? "ok" : "failed"}" title="${esc(r.error || "")}">${esc(r.stage.replace(/_agent$/, ""))}${r.user_id ? " u" + r.user_id : ""}${r.seconds != null ? " " + r.seconds + "s" : ""}${r.status !== "ok" ? " ✗" : ""}</span>`;
  $("admin").innerHTML = `<h2>Admin</h2>
    <div class="card"><h3>Shared pool</h3><div class="nums">
      <label>postings <b>${p.postings.toLocaleString()}</b></label><label>kept by the crawl filter <b>${p.postings_kept.toLocaleString()}</b></label>
      <label>jobs <b>${p.jobs.toLocaleString()}</b></label><label>embedded <b>${p.jobs_embedded.toLocaleString()}</b></label>
      <label>companies <b>${p.companies}</b></label><label>active users <b>${p.users}</b></label></div></div>
    <div class="card"><h3>Accounts</h3><table class="rows"><tr><th>id</th><th>name</th><th>email</th><th>role</th><th>status</th><th>resume</th><th>in scope</th><th>marks</th><th>since</th></tr>
      ${users.map(u => `<tr data-user="${u.id}"><td>${u.id}</td><td>${esc(u.name || "")}</td><td>${esc(u.email)}${u.has_password ? "" : ' <span class="tag note">no password</span>'}</td>
        <td><select data-field="role" ${u.id === me.id ? "disabled" : ""}><option ${u.role === "member" ? "selected" : ""}>member</option><option ${u.role === "admin" ? "selected" : ""}>admin</option></select></td>
        <td><select data-field="status" ${u.id === me.id ? "disabled" : ""}><option ${u.status === "active" ? "selected" : ""}>active</option><option ${u.status === "disabled" ? "selected" : ""}>disabled</option></select></td>
        <td>${u.has_resume ? "✓" : "–"}</td><td>${u.in_scope}</td><td>${u.marks}</td><td>${u.created_at}</td></tr>`).join("")}</table>
      <div class="sub" style="margin-top:8px">A disabled account is signed out everywhere and cannot sign in. Set a password for an account from the server with <code>jobagent passwd --user ID</code>.</div></div>
    <div class="card"><h3>Queue</h3>${ov.queue.length ? ov.queue.map(r => `<div class="runrow"><span class="${r.status}">${r.status}</span><span>${esc(r.label)}</span><span>${r.lane} lane</span></div>`).join("") : '<div class="sub">idle</div>'}</div>
    <div class="card"><h3>Stage runs, newest first</h3>${Object.entries(byDay).map(([d, rs]) => `<div class="runrow" style="grid-template-columns:90px 1fr;cursor:default"><span>${d}</span><span>${rs.reverse().map(fmt).join(" → ")}</span></div>`).join("") || '<div class="sub">no runs yet</div>'}
      <div class="sub" style="margin-top:8px">The daily crawl runs from <code>systemd/jobagent-crawl.timer</code> at 06:00; "u1" marks a per-user stage.</div></div>`;
}
$("admin").addEventListener("change", async e => {
  const sel = e.target.closest("select[data-field]"); if (!sel) return;
  const id = sel.closest("tr").dataset.user;
  try { await api("/api/users/" + id, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({[sel.dataset.field]: sel.value})}); }
  catch (err) { alert(err.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, "")); renderAdmin(); }
});
