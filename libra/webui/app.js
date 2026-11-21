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

// A token in the link (#token=...) is taken once, then removed from the address bar.
(function readTokenFromLink() {
  const m = location.hash.match(/token=([^&]+)/);
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
function showLogin(msg) {
  for (const id of ["chat", "translate", "skills", "training"]) $(id).hidden = true;
  $("tabs").hidden = true; $("composer").hidden = true; $("logout").hidden = true;
  $("login").hidden = false; $("login-error").textContent = msg || "";
  setDot("bad");
}

$("login-btn").addEventListener("click", () => {
  token = $("token").value.trim();
  store.set("libra-token", token);
  start();
});
$("token").addEventListener("keydown", (e) => { if (e.key === "Enter") $("login-btn").click(); });
$("logout").addEventListener("click", () => { store.del("libra-token"); token = null; showLogin(""); });

// ----------------------------------------------------------------- tabs
function showTab(name) {
  for (const b of document.querySelectorAll("nav button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const id of ["chat", "translate", "skills", "training"]) $(id).hidden = id !== name;
  $("composer").hidden = name !== "chat";
  if (name === "training") loadTraining();
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
let pollTimer = null;
async function loadTraining() {
  clearTimeout(pollTimer);
  try {
    const r = await json("/v1/auto/status");
    $("au-report").textContent = r.status;
    $("au-state").textContent = r.running ? "A run is in progress…" : (r.last && r.last.decision ? "Last run from this app: " + r.last.decision : "");
    $("au-run").disabled = $("au-dry").disabled = r.running;
    if (r.running) pollTimer = setTimeout(loadTraining, 5000);
  } catch (e) { $("au-report").textContent = "Error: " + e.message; }
}
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
  $("login").hidden = true; $("tabs").hidden = false; $("logout").hidden = !info.auth;
  document.querySelector('nav button[data-tab="training"]').hidden = !info.auto;
  const both = info.workspace && info.model;
  $("mode").hidden = !both;
  mode = info.workspace ? "files" : "model";
  if (!info.workspace && !info.model) $("chat-empty").textContent = "No model or files yet. Translation, skills and calculations still work here.";
  else if (!info.workspace) $("chat-empty").textContent = `Chat with your ${info.model_parameters.toLocaleString()}-parameter model.`;
  showTab("chat");
}

start();
