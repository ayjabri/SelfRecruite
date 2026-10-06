// ---- running jobagent from the page
let tools = [], pollTimer, currentRun = null;
async function loadTools() {
  tools = await api("/api/tools");
  $("run-toolname").innerHTML = tools.map(t => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join("");
  renderToolForm();
}
function renderToolForm() {
  const t = tools.find(x => x.name === $("run-toolname").value); if (!t) return;
  $("run-tooldesc").textContent = t.description;
  $("run-fields").innerHTML = t.fields.map(f => {
    const id = "arg-" + f.name, label = `<label for="${id}">${esc(f.name)}${f.required ? " *" : ""}</label>`;
    let input;
    if (f.type === "bool") input = `<select id="${id}" data-arg="${esc(f.name)}" data-type="bool"><option value="">default${f.default != null ? " (" + f.default + ")" : ""}</option><option value="true">true</option><option value="false">false</option></select>`;
    else if (f.type.startsWith("choice:")) input = `<select id="${id}" data-arg="${esc(f.name)}"><option value="">default${f.default != null ? " (" + esc(f.default) + ")" : ""}</option>${f.type.slice(7).split(",").map(c => `<option>${esc(c)}</option>`).join("")}</select>`;
    else input = `<input id="${id}" data-arg="${esc(f.name)}" data-type="${f.type}" type="${f.type === "number" ? "number" : "text"}" placeholder="${f.default != null ? "default: " + esc(f.default) : (f.required ? "required" : "optional")}">`;
    return `<div class="field">${label}${input}<div class="d">${esc(f.description)}</div></div>`;
  }).join("") || '<div class="sub">This tool takes no arguments.</div>';
}
function toolArgs() {
  const args = {};
  for (const el of $("run-fields").querySelectorAll("[data-arg]")) {
    const v = el.value; if (v === "") continue;
    args[el.dataset.arg] = el.dataset.type === "bool" ? v === "true" : el.dataset.type === "number" ? Number(v) : v;
  }
  return args;
}
async function startRun() {
  const cmd = $("run-cmd").value;
  const body = cmd === "score" ? {command: "score"} : cmd === "pipeline" ? {command: "pipeline", request: $("run-request").value} : {command: "run", tool: $("run-toolname").value, args: toolArgs()};
  $("run-msg").textContent = "";
  try { const r = await api("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}); watch(r.id); }
  catch (e) { $("run-msg").textContent = e.message.replace(/^.*"detail":"?/, "").replace(/"?}$/, ""); }
}
function watch(id) { currentRun = id; $("run-current").hidden = false; poll(); }
async function poll() {
  clearTimeout(pollTimer);
  if (!currentRun) return;
  const r = await api("/api/runs/" + currentRun);
  $("run-title").textContent = `${r.label} — ${r.status}${r.status === "queued" && r.ahead != null ? " (" + r.ahead + " ahead in the " + r.lane + " lane)" : ""}${r.elapsed_s != null ? " · " + r.elapsed_s + "s" : ""}${r.exit_code != null ? " · exit " + r.exit_code : ""}`;
  const out = $("run-output"), atBottom = out.scrollHeight - out.scrollTop - out.clientHeight < 40;
  out.textContent = r.output || "(no output yet)";
  if (atBottom) out.scrollTop = out.scrollHeight;
  $("run-stop").hidden = !(r.status === "running" || r.status === "queued");
  $("run-stop").textContent = r.status === "queued" ? "Cancel" : "Stop";
  if (r.status === "running" || r.status === "queued") pollTimer = setTimeout(poll, 2000);
  else { refreshRuns(); loadSummary(); loadJobs(true); }
}
async function refreshRuns() {
  const d = await api("/api/runs");
  if (d.running && !currentRun) watch(d.running.id);
  $("run-list").innerHTML = d.runs.length ? d.runs.map(r => `<div class="runrow" data-run="${r.id}"><span class="${r.status}">${r.status}</span><span>${esc(r.label)}</span><span>${(r.started_at || r.queued_at || "").slice(0, 16).replace("T", " ")}</span></div>`).join("") : "none yet";
}
$("run-cmd").addEventListener("change", () => { const c = $("run-cmd").value; $("run-pipeline").hidden = c !== "pipeline"; $("run-tool").hidden = c !== "run"; });
$("run-pipeline").hidden = true;
$("run-toolname").addEventListener("change", renderToolForm);
$("run-start").addEventListener("click", startRun);
$("run-stop").addEventListener("click", async () => { if (currentRun) await api(`/api/runs/${currentRun}/stop`, {method: "POST"}); });
$("run-list").addEventListener("click", e => { const r = e.target.closest(".runrow"); if (r) watch(r.dataset.run); });
