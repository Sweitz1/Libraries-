// Libra web app. Plain JavaScript, no external files. All server text is set with textContent.
"use strict";

const $ = (id) => document.getElementById(id);
const LANGS = ["auto", "English", "Spanish", "French", "German", "Italian", "Portuguese", "Dutch", "Swedish",
  "Danish", "Norwegian", "Finnish", "Polish", "Czech", "Turkish", "Russian", "Ukrainian", "Greek", "Arabic",
  "Persian", "Hebrew", "Hindi", "Chinese", "Japanese", "Korean", "Vietnamese", "Indonesian", "Romanian", "Hungarian"];

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
};

let token = null;
let info = null;
let mode = "files";
const chats = { files: [], model: [] };

// Inside the Libra Android app (its WebView adds "LibraAndroid" to the user agent).
const IN_APP = /LibraAndroid/.test(navigator.userAgent);
// A tab to open first (#tab=agent), e.g. from a notification.
const START_TAB = (location.hash.match(/tab=([a-z]+)/) || [])[1] || "chat";
let pairResult = null;

// A token in the link (#token=...) is taken once, then removed from the address bar.
(function readTokenFromLink() {
  const m = location.hash.match(/token=([^&]+)/);
  if (location.hash && !m) window.history.replaceState(null, "", location.pathname);
  if (m) {
    token = decodeURIComponent(m[1]);
    store.set("libra-token", token);
    window.history.replaceState(null, "", location.pathname);
  } else {
    token = store.get("libra-token");
  }
})();

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (res.status === 401) { showLogin("That token didn't work."); throw new Error("unauthorized"); }
  if (res.status === 429) throw new Error("Too many wrong tokens. Wait a few minutes.");
  return res;
}

async function json(path, body) {
  const res = await api(path, body === undefined ? {} : { method: "POST", body: JSON.stringify(body) });
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok && res.status !== 403) throw new Error((data.error && data.error.message) || data.text || res.statusText);
  return Object.assign({ _status: res.status }, data);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v; else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v); else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

function setDot(state) { $("dot").className = "dot " + (state || ""); }

// ---------------------------------------------------------------- login
const SECTIONS = ["chat", "translate", "skills", "agent", "training", "pair"];

function showLogin(msg) {
  for (const id of SECTIONS) $(id).hidden = true;
  $("tabs").hidden = true; $("composer").hidden = true; $("logout").hidden = true; $("pair-btn").hidden = true;
  $("login").hidden = false; $("login-error").textContent = msg || "";
  setDot("bad");
}

$("login-btn").addEventListener("click", () => {
  token = $("token").value.trim();
  store.set("libra-token", token);
  start();
});
$("token").addEventListener("keydown", (e) => { if (e.key === "Enter") $("login-btn").click(); });
$("logout").addEventListener("click", () => {
  store.del("libra-token"); token = null;
  if (IN_APP) { location.href = "libra://signout"; return; }  // the app forgets its saved sign-in too
  showLogin("");
});

// ----------------------------------------------------------------- tabs
function showTab(name) {
  for (const b of document.querySelectorAll("nav button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const id of SECTIONS) $(id).hidden = id !== name;
  $("composer").hidden = name !== "chat";
  if (name === "training") loadTraining();
  if (name === "agent") loadAgent();
  if (name === "skills" && !$("sk-list").childElementCount) searchSkills();
}
for (const b of document.querySelectorAll("nav button")) b.addEventListener("click", () => showTab(b.dataset.tab));

// ----------------------------------------------------------------- chat
function addMsg(role, text) {
  $("chat-empty").hidden = true;
  const bubble = el("div", { class: "msg " + (role === "user" ? "user" : "bot") });
  const body = el("div", { text });
  bubble.append(body);
  $("msgs").append(bubble);
  keepVisible(bubble);
  return { bubble, body };
}

function setMode(m) {
  mode = m;
  for (const b of document.querySelectorAll("#mode button")) b.setAttribute("aria-pressed", String(b.dataset.mode === m));
  $("msgs").textContent = "";
  for (const msg of chats[m]) addMsg(msg.role, msg.content);
  $("chat-empty").hidden = chats[m].length > 0;
}
for (const b of document.querySelectorAll("#mode button")) b.addEventListener("click", () => setMode(b.dataset.mode));

async function send() {
  const text = $("input").value.trim();
  if (!text) return;
  $("input").value = "";
  const msgs = chats[mode];
  msgs.push({ role: "user", content: text });
  addMsg("user", text);
  const { bubble, body } = addMsg("bot", "…");
  $("send").disabled = true;
  try {
    const res = await api("/v1/chat/completions", {
      method: "POST",
      body: JSON.stringify({ messages: msgs, stream: true, max_tokens: 300, libra_mode: mode, libra_auto_skills: true }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data.error && data.error.message) || res.statusText);
    }
    let answer = "", skill = null, grounding = null, buffer = "";
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const event = buffer.slice(0, cut); buffer = buffer.slice(cut + 2);
        for (const line of event.split("\n")) {
          if (!line.startsWith("data: ") || line === "data: [DONE]") continue;
          const chunk = JSON.parse(line.slice(6));
          skill = chunk.libra_skill || skill;
          grounding = chunk.libra_grounding || grounding;
          const delta = chunk.choices && chunk.choices[0].delta;
          if (delta && delta.content) { answer += delta.content; body.textContent = answer; keepVisible(bubble); }
        }
      }
    }
    body.textContent = answer || "(no answer)";
    keepVisible(bubble);
    msgs.push({ role: "assistant", content: answer });
    if (skill === "workspace" && grounding && grounding.status === "answered") addCorrection(bubble, text);
  } catch (e) {
    if (e.message !== "unauthorized") { body.textContent = "Error: " + e.message; bubble.classList.add("error"); }
  } finally {
    $("send").disabled = false;
  }
}

function keepVisible(node) {
  // replies grow while they stream in; keep the bottom of the newest one above the message box
  const limit = $("composer").getBoundingClientRect().top - 12;
  const bottom = node.getBoundingClientRect().bottom;
  if (bottom > limit) document.querySelector("main").scrollTop += bottom - limit;  // <main> is the scroller
}

function addCorrection(bubble, question) {
  const actions = el("div", { class: "actions" });
  actions.append(el("button", { class: "btn small secondary", text: "Correct this", onclick: async () => {
    const fixed = window.prompt("The right answer (saved permanently for this question):");
    if (!fixed) return;
    try { await json("/v1/workspace/correct", { question, answer: fixed }); actions.textContent = "Correction saved."; }
    catch (e) { actions.textContent = "Couldn't save: " + e.message; }
  } }));
  actions.append(el("button", { class: "btn small secondary", text: "Add a rule", onclick: async () => {
    const rule = prompt("A standing rule, e.g. “always answer in bullet points”:");
    if (!rule) return;
    try { await json("/v1/workspace/rule", { rule }); actions.textContent = "Rule saved."; }
    catch (e) { actions.textContent = "Couldn't save: " + e.message; }
  } }));
  bubble.append(actions);
  keepVisible(bubble);
}

$("send").addEventListener("click", send);
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });

// ------------------------------------------------------------ translate
for (const l of LANGS) $("langs").append(el("option", { value: l }));
$("tr-swap").addEventListener("click", () => {
  const from = $("tr-from").value; $("tr-from").value = $("tr-to").value; $("tr-to").value = from === "auto" ? "English" : from;
});
$("tr-go").addEventListener("click", async () => {
  const text = $("tr-text").value.trim();
  if (!text) return;
  $("tr-out").textContent = "Translating…";
  try {
    const r = await json("/v1/translate", { text, to: $("tr-to").value, source: $("tr-from").value || "auto" });
    $("tr-out").textContent = r.display;
  } catch (e) { $("tr-out").textContent = "Error: " + e.message; }
});

// --------------------------------------------------------------- skills
async function searchSkills() {
  const q = $("sk-q").value.trim();
  $("sk-form").hidden = true;
  $("sk-list").textContent = "Searching…";
  try {
    const r = await json("/v1/skills" + (q ? "?q=" + encodeURIComponent(q) + "&limit=30" : ""));
    $("sk-count").textContent = r.catalog_size ? `${r.catalog_size.toLocaleString()} skills in the catalog, plus built-ins` : "";
    $("sk-list").textContent = "";
    if (!r.data.length) $("sk-list").append(el("p", { class: "empty", text: "No matching skills." }));
    for (const s of r.data) {
      const f = s.function;
      const card = el("div", { class: "card skill", role: "button", tabindex: "0" },
        el("h3", { text: f.name }), el("span", { class: "badge " + s.risk, text: s.risk }), " ",
        el("span", { class: "muted", text: f.description || "" }));
      card.addEventListener("click", () => openSkill(s));
      card.addEventListener("keydown", (e) => { if (e.key === "Enter") openSkill(s); });
      $("sk-list").append(card);
    }
  } catch (e) { $("sk-list").textContent = "Error: " + e.message; }
}

function openSkill(s) {
  const f = s.function, form = $("sk-form"), props = (f.parameters && f.parameters.properties) || {};
  const required = new Set((f.parameters && f.parameters.required) || []);
  form.textContent = "";
  form.append(el("h3", { text: f.name, style: "margin-top:0;word-break:break-all" }),
    el("p", { class: "muted", text: f.description || "" }));
  if (s.risk !== "read") {
    form.append(el("div", { class: "notice", text: `This is a ${s.risk} skill. The server runs it only if you've allowed it (libra trust allow ${f.name}).` }));
  }
  const inputs = {};
  for (const [name, p] of Object.entries(props)) {
    const id = "sk-" + name;
    form.append(el("label", { for: id, text: name + (required.has(name) ? " *" : "") + (p.description ? " — " + p.description : "") }));
    const input = el("input", { id, placeholder: p.default !== undefined ? String(p.default) : (p.type || "") });
    inputs[name] = input; form.append(input);
  }
  const out = el("div", { class: "out", style: "margin-top:12px" });
  form.append(el("p", {}, el("button", { class: "btn", text: "Run", onclick: async () => {
    const args = {};
    for (const [k, i] of Object.entries(inputs)) if (i.value !== "") args[k] = i.value;
    out.textContent = "Running…";
    try {
      const r = await json("/v1/skills/" + encodeURIComponent(f.name), args);
      out.textContent = r.text || "(no output)";
    } catch (e) { out.textContent = "Error: " + e.message; }
  } })), out);
  form.hidden = false;
  form.scrollIntoView({ behavior: "smooth" });
}
$("sk-go").addEventListener("click", searchSkills);
$("sk-q").addEventListener("keydown", (e) => { if (e.key === "Enter") searchSkills(); });

// ------------------------------------------------------------- training
let pollTimer = null, trainingWasRunning = false;
const PHASES = { starting: "Starting", collecting: "Collecting pages", training: "Training", scoring: "Testing the new model",
                 done: "Finished", failed: "Failed" };
async function loadTraining() {
  clearTimeout(pollTimer);
  try {
    const r = await json("/v1/auto/status");
    $("au-report").textContent = r.status;
    $("au-state").textContent = r.running ? "A run is in progress…" : (r.last && r.last.decision ? "Last run from this app: " + r.last.decision : "");
    $("au-run").disabled = $("au-dry").disabled = r.running;
    const p = r.progress || {};
    $("au-progress").hidden = !r.running;
    if (r.running) {
      const pct = p.phase === "training" && p.steps ? Math.round(100 * (p.step || 0) / p.steps) : (p.phase === "scoring" ? 100 : 5);
      $("au-bar").style.width = pct + "%";
      const bits = [PHASES[p.phase] || p.phase || "Working"];
      if (p.fetched) bits.push(`${p.fetched} pages fetched, ${p.kept || 0} kept`);
      if (p.phase === "training" && p.steps) bits.push(`step ${p.step || 0} of ${p.steps}` + (p.loss != null ? `, loss ${p.loss}` : ""));
      $("au-phase").textContent = bits.join(" · ");
    }
    if (trainingWasRunning && !r.running) notifyDevice("Training run finished", (p.decision || "").slice(0, 140));
    trainingWasRunning = r.running;
    if (r.running) pollTimer = setTimeout(loadTraining, 3000);
  } catch (e) { $("au-report").textContent = "Error: " + e.message; }
}

// ------------------------------------------------------- notifications
function notifyDevice(title, body) {
  try {
    if ("Notification" in window && Notification.permission === "granted" && document.visibilityState !== "visible") {
      new Notification(title, { body, icon: "/icon-192.png" });
    }
  } catch (e) { /* not supported here */ }
}
if ("Notification" in window && window.isSecureContext && Notification.permission === "default") $("notify-btn").hidden = false;
$("notify-btn").addEventListener("click", async () => {
  try { await Notification.requestPermission(); } catch (e) { /* ignore */ }
  $("notify-btn").hidden = true;
});

// ---------------------------------------------------------------- agent
let agentTimer = null, agentWasRunning = false;
function showPlan(plan) {
  const lines = plan.steps.map((s, i) => `${i + 1}. ${s.clause}\n   → ${s.description}`);
  for (const p of plan.problems) lines.push("✗ " + p);
  return lines.join("\n") || "(empty)";
}
$("ag-plan").addEventListener("click", async () => {
  const goal = $("ag-goal").value.trim(); if (!goal) return;
  try { $("ag-out").textContent = showPlan(await json("/v1/agent/plan", { goal })); }
  catch (e) { $("ag-out").textContent = "Error: " + e.message; }
});
async function startAgent(body, autonomous) {
  try {
    const r = await json(autonomous ? "/v1/agent/auto" : "/v1/agent/run", body);
    $("ag-out").textContent = r.started ? "Running…\n\n" + showPlan(r.plan) : "Can't run this goal:\n" + showPlan(r.plan);
    if (r.started) loadAgent();
  } catch (e) { $("ag-out").textContent = "Error: " + e.message; }
}
$("ag-run").addEventListener("click", () => { const goal = $("ag-goal").value.trim(); if (goal) startAgent({ goal }, $("ag-auto").checked); });
$("ag-save").addEventListener("click", async () => {
  const name = $("ag-name").value.trim(), goal = $("ag-goal").value.trim(), every = $("ag-every").value.trim();
  if (!name || !goal) { $("ag-out").textContent = "Give the goal a name, and write the goal above."; return; }
  try { await json("/v1/agent/goals", { name, goal, every: every || null, autonomous: $("ag-auto").checked }); $("ag-out").textContent = `Saved “${name}”.`; loadAgent(); }
  catch (e) { $("ag-out").textContent = e.message; }
});
async function loadAgent() {
  clearTimeout(agentTimer);
  let r;
  try { r = await json("/v1/agent"); } catch (e) { $("ag-goals").textContent = "Error: " + e.message; return; }
  const goals = $("ag-goals"); goals.textContent = "";
  const names = Object.keys(r.goals).sort();
  if (!names.length) goals.append(el("p", { class: "muted", text: "No saved goals yet." }));
  for (const name of names) {
    const g = r.goals[name];
    const card = el("div", { class: "card" }, el("strong", { text: name }), el("p", { class: "muted", text: g.goal }),
      el("p", { class: "muted", text: `${g.autonomous ? "Autonomous · " : ""}Runs: ${g.every || "manually"} · last: ${g.last_run ? g.last_run.replace("T", " ").slice(0, 16) + " (" + (g.last_status || "") + ")" : "never"}` }));
    const row = el("div", { class: "row" });
    row.append(el("button", { class: "btn small", text: "Run now", onclick: () => startAgent({ name }) }),
      el("button", { class: "btn small secondary", text: "Delete", onclick: async () => {
        if (confirm(`Delete the goal “${name}”?`)) { await json("/v1/agent/remove", { name }); loadAgent(); } } }), el("span"));
    card.append(row); goals.append(card);
  }
  const jobsBox = $("ag-jobs"); jobsBox.textContent = "";
  let jobs = [];
  try { jobs = (await json("/v1/jobs?n=10")).jobs; } catch (e) { jobsBox.append(el("p", { class: "muted", text: "Jobs: " + e.message })); }
  if (!jobs.length) jobsBox.append(el("p", { class: "muted", text: "No jobs yet." }));
  for (const j of jobs) {
    const state = j.running ? "running" : j.status;
    const card = el("div", { class: "card" }, el("strong", {}, el("span", { class: "status-" + state, text: state }),
      ` · ${j.steps_done}/${j.steps} steps · ` + (j.name || j.goal).slice(0, 70)));
    if (j.waiting && !j.running) card.append(el("p", { text: j.waiting.reason }));
    const op = j.waiting && j.waiting.operation;
    if (op && !j.running) card.append(el("pre", { class: "muted", text: `${op.skill} (${op.risk}), ${op.count} call(s):\n` + JSON.stringify(op.calls, null, 1).slice(0, 600) }));
    const act = (action, label, cls) => el("button", { class: "btn small" + (cls ? " " + cls : ""), text: label, onclick: async () => {
      // an approval names the operation shown here; if the job changed since, the server refuses it (409)
      const body = action === "approve" && op ? { operation_digest: op.digest } : {};
      try { await json(`/v1/jobs/${j.id}/${action}`, body); } catch (e) { alert(e.message); }
      setTimeout(loadAgent, 400);
    } });
    const row = el("div", { class: "row" });
    if (j.status === "waiting" && !j.running) {
      row.append(act("approve", j.waiting.kind === "limit" ? "Continue" : j.waiting.kind === "interrupted" ? "Run it again" : "Approve"));
      if (j.waiting.kind !== "limit") row.append(act("skip", "Skip step", "secondary"));
    }
    if (!["done", "failed", "cancelled", "needs_review"].includes(j.status)) row.append(act("cancel", "Cancel", "secondary"));
    row.append(el("button", { class: "btn small secondary", text: "Details", onclick: async () => {
      try {
        const full = await json(`/v1/jobs/${j.id}`);
        const lines = full.steps.map((s) => `${{ done: "✓", failed: "✗", skipped: "–", running: "…" }[s.status] || "·"} ${s.n + 1}. ${s.skill}` +
          (s.needs.length ? ` (after ${s.needs.map((n) => n + 1).join(", ")})` : "") + `: ${s.clause}` + (s.error ? `\n     ${s.error.slice(0, 160)}` : ""));
        if (full.result) {
          for (const c of full.result.checks) lines.push(`${c.ok ? "✓" : "✗"} check: ${c.check}`);
          lines.push(`result ${full.result.verified ? "verified" : "not verified"} · sha256 ${full.result.sha256.slice(0, 16)}…`, "", full.result.output);
        }
        $("ag-out").textContent = lines.join("\n");
      } catch (e) { $("ag-out").textContent = "Error: " + e.message; }
    } }));
    card.append(row); jobsBox.append(card);
  }
  const jobBusy = jobs.some((j) => j.running);
  const runs = $("ag-runs"); runs.textContent = "";
  if (r.running) runs.append(el("div", { class: "card" }, el("strong", { text: "Running: " + (r.current.name || r.current.goal || "") }),
    el("pre", { class: "report", text: (r.current.steps || []).join("\n") || "starting…" })));
  if (!r.runs.length && !r.running) runs.append(el("p", { class: "muted", text: "No runs yet." }));
  for (const run of r.runs) {
    const d = el("details", { class: "card" });
    d.append(el("summary", {}, el("span", { class: "status-" + run.status, text: run.status }), " · " + (run.name || run.goal).slice(0, 80) +
      " · " + run.started.replace("T", " ").slice(5, 16)));
    const steps = run.steps.map((s, i) => `${s.ok ? "✓" : "✗"} ${i + 1}. ${s.skill}${s.items ? " ×" + s.items : ""}: ${s.clause}`);
    const thoughts = (run.thoughts || []).map((t, i) => `💭 ${t.thought}` + (t.reasoning ? `\n   why: ${t.reasoning}` : "") +
      (t.criticism ? `\n   caveat: ${t.criticism}` : ""));
    const learned = run.learned ? [`📚 learned for goals like: ${run.learned}`] : [];
    d.append(el("pre", { class: "report", text: thoughts.concat(steps, run.problems.map((p) => "✗ " + p), learned).join("\n") +
      (run.output ? "\n\n" + run.output : "") }));
    runs.append(d);
  }
  if (agentWasRunning && !r.running && r.runs[0]) notifyDevice("Goal finished: " + (r.runs[0].name || "agent"), r.runs[0].status);
  agentWasRunning = r.running;
  if (r.running || jobBusy) agentTimer = setTimeout(loadAgent, 2500);
}

// ----------------------------------------------------------------- pair
$("pair-btn").addEventListener("click", async () => {
  for (const b of document.querySelectorAll("nav button")) b.setAttribute("aria-selected", "false");
  for (const id of SECTIONS) $(id).hidden = id !== "pair";
  $("composer").hidden = true;
  $("pair-qr").removeAttribute("src");
  $("pair-link").textContent = "";
  pairResult = null;
  loadDevices();
});
$("pair-new").addEventListener("click", async () => {
  try {
    pairResult = await json("/v1/pair", { base: location.origin, name: $("pair-name").value.trim() });
    showPairCode();
    loadDevices();
  } catch (e) { $("pair-link").textContent = "Error: " + e.message; }
});
function showPairCode() {
  if (!pairResult) return;
  const app = $("pair-app").checked && pairResult.app_svg;
  $("pair-qr").src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(app ? pairResult.app_svg : pairResult.svg);
  $("pair-link").textContent = app ? pairResult.app_url : pairResult.url;
}
$("pair-app").addEventListener("change", showPairCode);
async function loadDevices() {
  const list = $("pair-devices"); list.textContent = "";
  let r;
  try { r = await json("/v1/devices"); } catch (e) { list.textContent = "Error: " + e.message; return; }
  if (!r.auth) { list.append(el("p", { class: "muted", text: "This server runs without tokens." })); return; }
  if (!r.devices.length) list.append(el("p", { class: "muted", text: "No paired devices yet." }));
  for (const d of r.devices) {
    const row = el("div", { class: "row", style: "margin:6px 0" },
      el("span", { text: `${d.name}${d.current ? " (this device)" : ""} · last seen ${d.last_seen ? d.last_seen.replace("T", " ").slice(0, 16) : "never"}` }));
    row.append(el("button", { class: "btn small secondary", text: "Revoke", style: "flex:0 0 auto", onclick: async () => {
      if (!confirm(`Revoke “${d.name}”? It (and any device it paired) will have to be paired again.`)) return;
      try { await json("/v1/devices/revoke", { id: d.id }); } catch (e) { alert(e.message); }
      if (d.current) { store.del("libra-token"); token = null; showLogin("This device was revoked."); return; }
      loadDevices();
    } }));
    list.append(row);
  }
}
$("pair-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("pair-link").textContent); $("pair-copy").textContent = "Copied"; }
  catch (e) { $("pair-copy").textContent = "Select the link to copy"; }
});
$("pair-close").addEventListener("click", () => showTab("chat"));
async function startRun(dry) {
  try { await json("/v1/auto/run", { dry_run: dry }); } catch (e) { $("au-state").textContent = e.message; return; }
  loadTraining();
}
$("au-run").addEventListener("click", () => { if (confirm("Start a training run now? It can take a while.")) startRun(false); });
$("au-dry").addEventListener("click", () => startRun(true));

// ---------------------------------------------------------------- start
async function start() {
  try {
    info = await json("/v1/info");
  } catch (e) {
    if (e.message !== "unauthorized") { setDot("bad"); $("login").hidden = false; $("login-error").textContent = "Can't reach the server: " + e.message; }
    return;
  }
  setDot("ok");
  $("login").hidden = true; $("tabs").hidden = false; $("logout").hidden = !info.auth; $("pair-btn").hidden = false;
  document.querySelector('nav button[data-tab="training"]').hidden = !info.auto;
  const both = info.workspace && info.model;
  $("mode").hidden = !both;
  mode = info.workspace ? "files" : "model";
  if (!info.workspace && !info.model) $("chat-empty").textContent = "No model or files yet. Translation, skills and calculations still work here.";
  else if (!info.workspace) $("chat-empty").textContent = `Chat with your ${info.model_parameters.toLocaleString()}-parameter model.`;
  offerAndroidApp();
  const tab = document.querySelector(`nav button[data-tab="${START_TAB}"]`);
  showTab(tab && !tab.hidden ? START_TAB : "chat");
}

// On an Android phone's browser, offer to open the same sign-in in the Libra app (if it's installed).
function offerAndroidApp() {
  if (IN_APP || !/Android/i.test(navigator.userAgent) || !token || store.get("libra-app-offer") === "no") return;
  const link = `libra://pair?base=${encodeURIComponent(location.origin)}&token=${encodeURIComponent(token)}`;
  const bar = el("div", { class: "card", id: "app-offer", style: "margin:8px 12px" },
    el("span", { text: "Have the Libra Android app? " }),
    el("a", { href: link, text: "Open it there" }), " · ",
    el("a", { href: "#", text: "No thanks", onclick: (e) => { e.preventDefault(); store.set("libra-app-offer", "no"); bar.remove(); } }));
  document.querySelector("main").prepend(bar);
}

start();
