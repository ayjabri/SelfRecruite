// sign-in / registration page
const $ = id => document.getElementById(id);
let mode = location.hash === "#register" ? "register" : "login";
let registrationOpen = true;
function render() {
  const reg = mode === "register";
  $("title").textContent = reg ? "Create your account" : "Sign in";
  $("subtitle").textContent = reg ? "Then paste your resume and set what you are looking for." : "Your jobs, scored against your resume.";
  $("namerow").hidden = !reg; $("pwhint").hidden = !reg;
  $("password").autocomplete = reg ? "new-password" : "current-password";
  $("submit").textContent = reg ? "Create account" : "Sign in";
  $("switch").innerHTML = reg ? 'Already have an account? <a id="toggle">Sign in</a>' : (registrationOpen ? 'New here? <a id="toggle">Create an account</a>' : "");
  $("toggle")?.addEventListener("click", () => { mode = reg ? "login" : "register"; $("msg").textContent = ""; render(); });
}
fetch("/api/auth/status").then(r => r.json()).then(s => { registrationOpen = s.registration_open; if (s.signed_in) location.href = "/"; render(); }).catch(render);
$("form").addEventListener("submit", async e => {
  e.preventDefault();
  $("submit").disabled = true; $("msg").textContent = "";
  const body = {email: $("email").value, password: $("password").value, name: $("name").value};
  try {
    const r = await fetch("/api/auth/" + mode, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    location.href = "/";
  } catch (err) { $("msg").textContent = err.message; $("submit").disabled = false; }
});
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
render();
