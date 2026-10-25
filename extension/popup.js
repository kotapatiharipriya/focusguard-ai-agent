const $ = (id) => document.getElementById(id);
let st = null;

const send = (msg) => chrome.runtime.sendMessage(msg);
const time = (ms) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function liveFocus() {
  const f = st.focus;
  return f && !f.completedAt && f.until > Date.now() ? f : null;
}

function render() {
  const d = st.today;
  const live = st.current ? Date.now() - st.current.startedAt : 0;
  const total = dgDistractMs(d) + live;
  const f = liveFocus();

  const state = $("state");
  state.className = "state";
  if (st.pausedUntil > Date.now()) { state.textContent = `Paused until ${time(st.pausedUntil)}`; state.classList.add("paused"); }
  else if (f) { state.textContent = `${f.kind === "break" ? "Break" : "Focusing"}, ${dgFmtClock(f.until - Date.now())} left`; state.classList.add("focus"); }
  else state.textContent = "Watching";

  const big = $("big");
  big.replaceChildren();
  const n = document.createElement("span"); n.className = "num"; n.textContent = dgFmtDuration(total);
  big.append(n, " on distracting sites today");

  const apps = Object.entries(d.apps || {}).map(([id, a]) => [id, a.ms + (st.current && st.current.app === id ? live : 0)]);
  if (st.current && !d.apps[st.current.app]) apps.push([st.current.app, live]);
  apps.sort((a, b) => b[1] - a[1]);
  const top = apps.filter(([, ms]) => ms > 30000).slice(0, 3);
  const max = Math.max(1, ...top.map(([, ms]) => ms));
  const label = (id) => ((st.rules.find((r) => r.id === id) || {}).label) || id;
  $("bars").replaceChildren(...top.map(([id, ms]) => {
    const row = document.createElement("div"); row.className = "bar";
    const a = document.createElement("span"); a.textContent = label(id);
    const b = document.createElement("span"); b.className = "muted"; b.textContent = dgFmtDuration(ms);
    const m = document.createElement("div"); m.className = "meter";
    const i = document.createElement("i"); i.style.width = `${(ms / max) * 100}%`;
    m.appendChild(i); row.append(a, b, m);
    return row;
  }));

  const blocked = Object.values(d.blocked || {}).reduce((a, b) => a + b, 0);
  const facts = $("facts");
  facts.replaceChildren();
  const add = (pre, val, post) => { facts.append(pre); const b = document.createElement("b"); b.textContent = val; facts.append(b, post); };
  add("Turned around ", String(blocked), blocked === 1 ? " time, " : " times, ");
  add("focused ", dgFmtDuration(d.focusMs), ", ");
  add("", String(d.tasksDone || 0), (d.tasksDone === 1 ? " task" : " tasks") + " done.");

  $("focusBtn").textContent = f ? "Open focus page" : `Start ${st.focusSettings.workMin}-minute focus`;
  $("conn").replaceChildren();
  const dot = document.createElement("span"); dot.className = "dot" + (st.connected ? " on" : "");
  $("conn").append(dot, st.connected ? "FocusGuard AI app connected" : "Working on its own");
  $("conn").title = st.connected ? "Loop detection, nudges and coaching are on." : "Start the FocusGuard AI app on this computer for nudges and coaching.";
  $("pauseBtn").textContent = st.pausedUntil > Date.now() ? "Resume" : "Pause 1 hour";
  $("dashBtn").hidden = !st.connected;
  $("setup").hidden = Boolean(st.profile && st.profile.completedAt);
  $("links").classList.toggle("four", st.connected);
}

async function renderPlan() {
  const { plan } = await chrome.storage.local.get("plan");
  const el = $("planLine");
  if (!plan || plan.date !== dgDayKey()) { el.hidden = true; return; }
  const now = new Date(); const nowMin = now.getHours() * 60 + now.getMinutes();
  const next = (plan.focus_blocks || []).find((b) => b.status === "planned" && (Number(b.time.slice(0, 2)) * 60 + Number(b.time.slice(3))) >= nowMin);
  el.replaceChildren();
  const b = document.createElement("b"); b.textContent = "Agent: ";
  el.append(b, next ? `next focus ${dgFmtTime(next.time)}${next.task ? ` on “${next.task}”` : ""}.` : (plan.first_task ? `start with “${plan.first_task}”.` : "plan ready in the dashboard."));
  el.hidden = false;
}

async function refresh() { st = await send({ kind: "dg_status" }); render(); renderPlan(); }

$("focusBtn").addEventListener("click", async () => {
  if (!liveFocus()) await send({ kind: "dg_focus_start", sessionKind: "pomodoro", taskId: localStorage.getItem("dg-last-task") || null });
  chrome.tabs.create({ url: "focus.html" });
  window.close();
});
document.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => {
  chrome.tabs.create({ url: b.dataset.open });
  window.close();
}));
$("dashBtn").addEventListener("click", async () => {
  $("dashBtn").disabled = true;
  $("dashMsg").textContent = "Opening your dashboard…";
  $("dashMsg").hidden = false;
  const r = await send({ kind: "dg_open_dashboard" });
  if (!r.ok) { $("dashMsg").textContent = r.error === "not_running" ? "Start the FocusGuard AI app first." : r.error; $("dashBtn").disabled = false; return; }
  if (!r.started) window.close();
});
$("setup").addEventListener("click", () => { chrome.tabs.create({ url: "onboarding.html" }); window.close(); });
$("pauseBtn").addEventListener("click", async () => {
  st = await send({ kind: "dg_pause", minutes: st.pausedUntil > Date.now() ? 0 : 60 });
  render();
});

refresh();
setInterval(() => { if (st) render(); }, 1000);
