// Focus page: where blocked sites send you. Tasks + a focus timer.

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const R = 96;
const CIRC = 2 * Math.PI * R;
$("fill").style.strokeDasharray = CIRC;

let st = null;      // status from the background worker
let tasks = [];
let arrivedFrom = params.get("site");
let arrivedWhy = params.get("why");
let thanks = false;
let reasonAnswered = false;
const REASON_REPLY = {
  bored: "Boredom wants something real. Pick a 10-minute thing from your list, or add one.",
  avoiding: "That task feels heavy. Break it down and give the first step five minutes.",
  tired: "Scrolling won't rest you. Take a real 5-minute break away from the screen.",
  habit: "Autopilot caught. One slow breath, then pick a task.",
  stress: "Breathe out slowly a few times. Then write the worry down as a task and give it 15 minutes.",
};

const send = (msg) => chrome.runtime.sendMessage(msg);

async function load() {
  st = await send({ kind: "dg_status" });
  ({ tasks = [] } = await chrome.storage.local.get("tasks"));
  render();
}

function ruleFor(id) { return (st.rules || []).find((r) => r.id === id); }
function taskById(id) { return tasks.find((t) => t.id === id); }
function plural(n, w) { return `${n} ${w}${n === 1 ? "" : "s"}`; }

function liveFocus() {
  const f = st && st.focus;
  return f && !f.completedAt && f.until > Date.now() ? f : null;
}

// ---------------------------------------------------------------- banner
function renderBanner() {
  const b = $("banner");
  const f = liveFocus();
  b.className = "banner";
  if (f && f.kind === "rescue") {
    b.classList.add("calm");
    $("bannerTitle").textContent = "You stepped out of the loop.";
    $("bannerText").textContent = (f.why ? `You were at ${f.why}. ` : "") + "Distracting sites are closed until the timer ends. Pick something from your list.";
    $("why").hidden = true;
    b.hidden = false;
    return;
  }
  const prof = st.profile || {};
  if (params.get("welcome") && !arrivedFrom) {
    b.classList.add("calm");
    $("bannerTitle").textContent = prof.name ? `You're all set, ${prof.name}.` : "You're all set.";
    $("bannerText").textContent = "From now on, opening a blocked site brings you here. Pick a task and start a focus session whenever you're ready.";
    $("why").hidden = true;
    b.hidden = false;
    return;
  }
  if (!arrivedFrom) { b.hidden = true; return; }
  $("why").hidden = reasonAnswered || !["block", "limit", "protect"].includes(arrivedWhy);
  const rule = ruleFor(arrivedFrom);
  const label = rule ? rule.label : "That site";
  const n = (st.today.blocked || {})[arrivedFrom] || 0;
  const hi = prof.name ? `${prof.name}, ` : "";
  if (arrivedWhy === "protect") {
    const pr = prof.protect || {};
    $("bannerTitle").textContent = `${hi}it's your protected time.`;
    $("bannerText").textContent = `You asked FocusGuard AI to keep ${label} closed from ${dgFmtTime(pr.start)} to ${dgFmtTime(pr.end)}. Here's what you planned instead.`;
  } else if (arrivedWhy === "limit") {
    $("bannerTitle").textContent = `You've used today's ${rule ? rule.limitMin : ""} minutes on ${label}.`;
    $("bannerText").textContent = "It opens again tomorrow. Here's what you planned instead.";
  } else if (arrivedWhy === "focus" && f) {
    $("bannerTitle").textContent = `${label} is closed while you focus.`;
    $("bannerText").textContent = `${Math.ceil((f.until - Date.now()) / 60000)} minutes to go. You've got this.`;
  } else {
    $("bannerTitle").textContent = `${hi}${label} is blocked.`;
    $("bannerText").textContent = n > 1
      ? `FocusGuard AI has turned you around ${n} times today. Here's what you planned instead.`
      : "Here's what you planned instead.";
  }
  b.hidden = false;
}

// ----------------------------------------------------------------- timer
function setRing(progress, kind) {
  const ring = $("ring");
  ring.className = "ring" + (kind ? (kind === "break" ? " break" : "") : " idle");
  const p = Math.max(0, Math.min(1, progress));
  $("fill").style.strokeDashoffset = CIRC * (1 - p);
  const a = p * 2 * Math.PI - Math.PI / 2;
  $("buoy").setAttribute("cx", (110 + R * Math.cos(a)).toFixed(2));
  $("buoy").setAttribute("cy", (110 + R * Math.sin(a)).toFixed(2));
}

function renderTimer() {
  const fs = st.focusSettings;
  const f = st.focus;
  const live = liveFocus();
  $("idleControls").hidden = true;
  $("runControls").hidden = true;
  $("doneControls").hidden = true;
  $("moods").hidden = true;
  $("doneActions").hidden = false;

  if (live) {
    const total = live.until - live.startedAt;
    const left = live.until - Date.now();
    setRing(1 - left / total, live.kind);
    $("clock").textContent = dgFmtClock(left);
    $("clockSub").textContent = "left";
    $("mode").textContent = { pomodoro: "Focusing", rescue: "Focus Rescue", break: "On a break", away: "Away from the screen" }[live.kind] || "Focusing";
    const t = taskById(live.taskId);
    $("onTask").textContent = live.kind === "break" ? "Stand up, stretch, look at something far away."
      : live.kind === "away" ? `${live.label || "Something away from the screen"}. Leave this tab; I'll keep the time.`
      : t ? t.title : "";
    $("stop").textContent = live.kind === "break" ? "End break" : live.kind === "away" ? "I'm back" : "Stop session";
    $("runControls").hidden = false;
    document.title = `${dgFmtClock(left)} · ${$("mode").textContent}`;
    return;
  }

  if (f && f.completedAt && !thanks) {
    setRing(1, f.kind);
    $("clockSub").textContent = "";
    $("doneControls").hidden = false;
    document.title = "Done · FocusGuard AI";
    if (f.kind === "rescue") {
      $("mode").textContent = "Focus Rescue finished";
      $("clock").textContent = "Out";
      $("onTask").textContent = "You made it out of the loop. How do you feel?";
      $("doneActions").hidden = true;
      $("moods").hidden = false;
    } else if (f.kind === "break" || f.kind === "away") {
      $("mode").textContent = f.kind === "away" ? "Welcome back" : "Break's over";
      $("clock").textContent = "Ready";
      $("onTask").textContent = "Back to it?";
      $("breakBtn").hidden = true;
      $("againBtn").textContent = `Start ${fs.workMin}-minute focus`;
      $("againBtn").className = "btn btn-primary";
    } else {
      const t = taskById(f.taskId);
      $("mode").textContent = "Session done";
      $("clock").textContent = "Done";
      $("onTask").textContent = t ? `${f.minutes} minutes on “${t.title}”. Nice.` : `${f.minutes} focused minutes. Nice.`;
      $("breakBtn").hidden = false;
      $("breakBtn").textContent = `Take a ${fs.breakMin}-minute break`;
      $("againBtn").textContent = "Start another";
      $("againBtn").className = "btn btn-quiet";
    }
    return;
  }

  setRing(0, null);
  $("clock").textContent = `${fs.workMin}:00`;
  $("clockSub").textContent = "minutes of focus";
  $("mode").textContent = thanks ? "Thanks. That's noted for your report." : "Ready when you are";
  $("onTask").textContent = "";
  $("start").textContent = `Start ${fs.workMin}-minute focus`;
  $("idleControls").hidden = false;
  document.title = "Focus · FocusGuard AI";
}

// ----------------------------------------------------------------- tasks
const today = dgDayKey();
function openTasks() {
  // Parents first, each followed by its open steps from "Break down".
  const open = tasks.filter((t) => !t.done);
  const ids = new Set(open.map((t) => t.id));
  const out = [];
  for (const t of open) {
    if (t.parentId && ids.has(t.parentId)) continue;
    out.push(t);
    for (const c of open) if (c.parentId === t.id) out.push(c);
  }
  return out;
}
function doneToday() { return tasks.filter((t) => t.done && dgDayKey(t.doneAt) === today); }

async function saveTasks() {
  // Keep finished tasks for two weeks so the report can count them.
  const cutoff = Date.now() - 14 * 86400000;
  tasks = tasks.filter((t) => !t.done || t.doneAt > cutoff);
  await chrome.storage.local.set({ tasks });
}

function taskRow(t) {
  const li = document.createElement("li");
  const live = liveFocus();
  const open = tasks.filter((x) => !x.done);
  const isSub = Boolean(t.parentId && open.some((x) => x.id === t.parentId)) && !t.done;
  li.className = "task" + (t.done ? " done" : "") + (isSub ? " sub" : "") + (live && live.taskId === t.id ? " current" : "");
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = t.done;
  cb.setAttribute("aria-label", `Mark “${t.title}” ${t.done ? "not done" : "done"}`);
  cb.addEventListener("change", async () => {
    t.done = cb.checked;
    t.doneAt = cb.checked ? Date.now() : null;
    await saveTasks();
    send({ kind: "dg_task_done", delta: cb.checked ? 1 : -1 });
  });
  const title = document.createElement("span");
  title.className = "title";
  title.textContent = t.title;
  li.append(cb, title);

  if (t.pomodoros) {
    const poms = document.createElement("span");
    poms.className = "poms";
    poms.title = `${plural(t.pomodoros, "focus session")} on this task`;
    for (let i = 0; i < Math.min(t.pomodoros, 8); i++) poms.appendChild(document.createElement("i"));
    li.appendChild(poms);
  }
  const hasSteps = tasks.some((x) => x.parentId === t.id && !x.done);
  if (!t.done && !isSub && !hasSteps && st.connected) {
    const steps = document.createElement("button");
    steps.className = "btn btn-text steps-btn";
    steps.textContent = "Break down";
    steps.setAttribute("aria-label", `Break “${t.title}” into steps`);
    steps.addEventListener("click", () => breakDown(t, steps));
    li.appendChild(steps);
  }
  if (!t.done) {
    const go = document.createElement("button");
    go.className = "btn btn-quiet focus-btn";
    go.textContent = "Focus";
    go.disabled = Boolean(live);
    go.setAttribute("aria-label", `Start focusing on “${t.title}”`);
    go.addEventListener("click", () => startFocus(t.id));
    li.appendChild(go);
  }
  const del = document.createElement("button");
  del.className = "btn btn-text del";
  del.textContent = "×";
  del.setAttribute("aria-label", `Delete “${t.title}”`);
  del.addEventListener("click", async () => {
    tasks = tasks.filter((x) => x.id !== t.id && x.parentId !== t.id);
    await saveTasks();
  });
  li.appendChild(del);
  return li;
}

function renderTasks() {
  const open = openTasks();
  const done = doneToday();
  $("tasks").replaceChildren(...open.map(taskRow));
  $("empty").hidden = open.length > 0;
  $("doneWrap").hidden = done.length === 0;
  $("doneSummary").textContent = `Done today (${done.length})`;
  $("doneTasks").replaceChildren(...done.map(taskRow));

  const pick = $("taskPick");
  const prev = pick.value || localStorage.getItem("dg-last-task") || "";
  pick.replaceChildren(new Option("No specific task", ""), ...open.map((t) => new Option(t.title, t.id)));
  pick.value = open.some((t) => t.id === prev) ? prev : "";
}

// ----------------------------------------------------------------- today
function renderToday() {
  const d = st.today;
  const blocked = Object.values(d.blocked || {}).reduce((a, b) => a + b, 0);
  const live = st.current ? Date.now() - st.current.startedAt : 0;
  const items = [
    ["Distracting sites today", dgFmtDuration(dgDistractMs(d) + live)],
    ["Turned around", String(blocked)],
    ["Focused", dgFmtDuration(d.focusMs)],
    ["Tasks done", String(doneToday().length)],
  ];
  $("today").replaceChildren(...items.map(([k, v]) => {
    const s = document.createElement("span");
    s.append(`${k} `);
    const b = document.createElement("b");
    b.textContent = v;
    s.appendChild(b);
    return s;
  }));
}

// ------------------------------------------------------------------ pass
function renderPass() {
  const fs = st.focusSettings;
  const left = fs.passesPerDay - (st.today.passes || 0);
  const show = arrivedFrom && ["block", "limit", "protect"].includes(arrivedWhy) && !fs.strict && !liveFocus() && left > 0;
  $("pass").hidden = !show;
  if (show) {
    $("passAsk").textContent = `I need this site for something specific (${left} ${left === 1 ? "pass" : "passes"} left today)`;
    $("passGo").textContent = `Open for ${fs.passMinutes} minutes`;
  }
}

function renderGoal() {
  $("tasksLede").textContent = `What you'd rather be doing. Pick one and give it ${st.focusSettings.workMin} minutes.`;
  const g = st.profile && st.profile.mainGoal;
  $("goalLine").hidden = !g;
  if (g) { $("goalLine").replaceChildren(); const b = document.createElement("b"); b.textContent = "Your goal: "; $("goalLine").append(b, g); }
}

function render() {
  if (!st) return;
  renderGoal();
  renderBanner();
  renderTimer();
  renderTasks();
  renderToday();
  renderPass();
}

// --------------------------------------------------------- AI breakdown
async function breakDown(t, btn) {
  btn.disabled = true;
  btn.textContent = "Thinking…";
  const r = await send({ kind: "dg_breakdown", task: t.title });
  const note = $("stepsNote");
  if (!r || !r.ok || !(r.steps || []).length) {
    note.textContent = "Start the FocusGuard AI app on this computer to break tasks into steps.";
    note.hidden = false;
    btn.disabled = false; btn.textContent = "Break down";
    return;
  }
  const at = tasks.findIndex((x) => x.id === t.id);
  const steps = r.steps.map((title) => ({ id: crypto.randomUUID(), title, parentId: t.id, done: false,
                                          createdAt: Date.now(), doneAt: null, pomodoros: 0, focusMs: 0 }));
  tasks.splice(at + 1, 0, ...steps);
  note.textContent = r.source === "ollama"
    ? "Steps written by the AI model on your computer. Start a focus session on the first one."
    : "Starter steps. Turn on Ollama in the FocusGuard AI app to get steps written for this exact task.";
  note.hidden = false;
  await saveTasks();
}

// --------------------------------------------------------------- actions
async function startFocus(taskId) {
  thanks = false;
  if (taskId) localStorage.setItem("dg-last-task", taskId);
  await chrome.runtime.sendMessage({ kind: "dg_focus_start", sessionKind: "pomodoro", taskId: taskId || null });
  arrivedFrom = null;
  history.replaceState(null, "", "focus.html");
  await load();
}

$("start").addEventListener("click", () => startFocus($("taskPick").value));
$("taskPick").addEventListener("change", () => localStorage.setItem("dg-last-task", $("taskPick").value));
$("stop").addEventListener("click", async () => { await send({ kind: "dg_focus_stop", reason: "ended_early" }); await load(); });
$("breakBtn").addEventListener("click", async () => { await send({ kind: "dg_break_start" }); await load(); });
$("againBtn").addEventListener("click", () => startFocus(st.focus && st.focus.taskId));
document.querySelectorAll("[data-mood]").forEach((b) => b.addEventListener("click", async () => {
  await send({ kind: "dg_focus_ack", mood: b.dataset.mood });
  thanks = true;
  await load();
}));

document.querySelectorAll("[data-reason]").forEach((b) => b.addEventListener("click", async () => {
  reasonAnswered = true;
  await send({ kind: "dg_reason", reason: b.dataset.reason });
  $("why").hidden = true;
  $("whyAnswer").textContent = REASON_REPLY[b.dataset.reason];
  $("whyAnswer").hidden = false;
}));

$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = $("newTask").value.trim();
  if (!title) return;
  tasks.push({ id: crypto.randomUUID(), title, done: false, createdAt: Date.now(), doneAt: null, pomodoros: 0, focusMs: 0 });
  $("newTask").value = "";
  await saveTasks();
});

$("passAsk").addEventListener("click", () => { $("passAsk").hidden = true; $("passForm").hidden = false; $("passReason").focus(); });
$("passForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await send({ kind: "dg_pass", app: arrivedFrom, reason: $("passReason").value });
  if (!r.ok) { $("passErr").textContent = r.error; $("passErr").hidden = false; return; }
  const rule = ruleFor(arrivedFrom);
  location.href = r.url || `https://${rule ? rule.hosts[0] : ""}`;
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.focus || changes.days || changes.settings || changes.profile) load();
  else if (changes.tasks) { tasks = changes.tasks.newValue || []; render(); }
});
setInterval(() => { if (st) { renderTimer(); if (liveFocus()) renderBanner(); } }, 1000);
load();
