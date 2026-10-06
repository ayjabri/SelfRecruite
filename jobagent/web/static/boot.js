// keyboard shortcuts, sign-in check and first load (loaded last)
document.addEventListener("keydown", e => {
  if (tab !== "jobs" || e.target.matches("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === "j" || k === "arrowdown") { e.preventDefault(); move(1); }
  else if (k === "k" || k === "arrowup") { e.preventDefault(); move(-1); }
  else if (k === "i") mark("interested");
  else if (k === "x") mark("not_interested");
  else if (k === "u") mark("clear");
  else if (k === "f") toggle("favorite");
  else if (k === "a") toggle("applied");
});
$("logout").addEventListener("click", async () => { await api("/api/auth/logout", {method: "POST"}); location.href = "/login"; });
async function whoami() {
  me = await api("/api/auth/me");
  $("whoami").textContent = me.name || me.email;
  $("whoami").title = me.email + (isAdmin() ? " (admin)" : "");
  if (!isAdmin()) {  // members: no crawl / raw tools, no shared files
    for (const o of [...$("run-cmd").options]) if (o.value !== "score") o.remove();
    $("run-pipeline").hidden = $("run-tool").hidden = true;
  }
  $("admintab").hidden = !isAdmin();
  await loadMyResumes();
  if (!myResumes.length) openOnboarding();  // first visit: nothing to score against yet
}
let lastTab = "jobs"; try { lastTab = localStorage.getItem("tab") || "jobs"; } catch (e) {}
whoami().then(() => { if (lastTab !== "jobs" && !(lastTab === "admin" && !isAdmin())) showTab(lastTab); return loadSummary(); }).then(() => loadJobs(false)).catch(e => { $("detail").innerHTML = `<div class="empty">Could not load jobs: ${esc(e.message)}</div>`; });
