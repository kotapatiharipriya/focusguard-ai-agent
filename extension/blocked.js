// The blocked screen: says plainly that you blocked this site, then offers
// three one-click ways out before the loop can start.

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const site = params.get("site");
const why = params.get("why") || "block";
const send = (msg) => chrome.runtime.sendMessage(msg);

let st = null;
let tasks = [];
let away = null; // the "instead" activity currently offered

const REASON_REPLY = {
  bored: "Boredom wants something real. The walk or one of your quick ideas will feel better than the feed.",
  avoiding: "That task feels heavy. Start it for just 5 minutes; you can stop after that.",
  tired: "Scrolling won't rest you. Take the 2-minute reset, or a real break away from the screen.",
  stress: "Breathe out slowly a few times. Then write the worry down as a task and give it 15 minutes.",
  habit: "Autopilot caught. That's the hard part done. Pick one thing above.",
};

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const openTasks = () => {
  const open = tasks.filter((t) => !t.done);
  const ids = new Set(open.map((t) => t.id));
  const out = [];
  for (const t of open) {
    if (t.parentId && ids.has(t.parentId)) continue;
    out.push(t);
    for (const c of open) if (c.parentId === t.id) out.push(c);
  }
  return out;
};

function liveFocus() {
  const f = st && st.focus;
  return f && !f.completedAt && f.until > Date.now() && f.kind !== "break" ? f : null;
}

async function load() {
  st = await send({ kind: "dg_status" });
  ({ tasks = [], plan = null } = await chrome.storage.local.get(["tasks", "plan"]));
  render();
}

function render() {
  const prof = st.profile || {};
  const rule = (st.rules || []).find((r) => r.id === site);
  const label = rule ? rule.label : "This site";
  const name = prof.name ? `${prof.name}, ` : "";
  const n = (st.today.blocked || {})[site] || 0;
  const total = Object.values(st.today.blocked || {}).reduce((x, y) => x + y, 0);
  document.title = `${label} is blocked · FocusGuard AI`;

  const f = liveFocus();
  $("inFocus").hidden = !f;
  $("waysSection").hidden = Boolean(f);
  if (f) {
    const t = tasks.find((x) => x.id === f.taskId);
    const left = Math.ceil((f.until - Date.now()) / 60000);
    $("title").textContent = `${name}you're in the middle of a ${f.kind === "away" ? "break from screens" : "focus session"}.`;
    $("sub").textContent = `${label} stays closed for ${plural(left, "more minute")}` + (t ? `, while you work on “${t.title}”.` : ".");
  } else if (why === "limit") {
    $("title").textContent = `${name}your ${rule ? rule.limitMin : ""} minutes on ${label} are used up.`;
    $("sub").textContent = "It opens again tomorrow. Here's something better for right now.";
  } else if (why === "protect") {
    const pr = prof.protect || {};
    $("title").textContent = `${name}it's your protected time.`;
    $("sub").textContent = `You asked FocusGuard AI to keep ${label} closed from ${dgFmtTime(pr.start)} to ${dgFmtTime(pr.end)}.`;
  } else {
    $("title").textContent = `${name}you blocked ${label}.`;
    $("sub").textContent = n > 1
      ? `This is the ${ordinal(n)} time today you tried to open it. You set this up for a reason.`
      : "You set this up for a reason. Here's something better, one click away.";
  }

  renderPlan();
  $("goal").hidden = !prof.mainGoal;
  $("goalText").textContent = prof.mainGoal || "";

  // Way 1: focus on your top task
  const top = openTasks()[0];
  const mins = st.focusSettings.workMin;
  $("wayFocusTitle").textContent = top ? `Focus on “${top.title}”` : "Start a focus session";
  $("wayFocusText").textContent = `${mins} minutes. Every distracting site stays closed.`;

  // Way 2: something from your "instead" list
  const ideas = (prof.instead && prof.instead.length ? prof.instead : ["walk", "stretch", "water", "read"]);
  if (!away || !ideas.includes(away)) away = ideas[0];
  $("wayAwayTitle").textContent = dgInsteadLabel(away);
  $("swap").replaceChildren(...ideas.slice(0, 5).map((id) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = dgInsteadLabel(id);
    b.setAttribute("aria-pressed", String(id === away));
    b.addEventListener("click", () => { away = id; render(); });
    return b;
  }));
  $("swap").hidden = ideas.length < 2;

  renderTasks();

  const passesLeft = st.focusSettings.passesPerDay - (st.today.passes || 0);
  $("pass").hidden = Boolean(f) || st.focusSettings.strict || passesLeft <= 0 || !site;
  $("passAsk").textContent = `I need this site for something specific (${passesLeft} ${passesLeft === 1 ? "pass" : "passes"} left today)`;
  $("passGo").textContent = `Open for ${st.focusSettings.passMinutes} minutes`;
  $("today").textContent = total ? `FocusGuard AI has turned you around ${plural(total, "time")} today.` : "";
}

let plan = null;
function renderPlan() {
  const el = $("agentPlan");
  const today = dgDayKey();
  if (!plan || plan.date !== today) { el.hidden = true; return; }
  const now = new Date(); const nowMin = now.getHours() * 60 + now.getMinutes();
  const next = (plan.focus_blocks || []).find((b) => b.status === "planned" && (Number(b.time.slice(0, 2)) * 60 + Number(b.time.slice(3))) >= nowMin);
  const bits = [];
  if (plan.first_task) bits.push(`start with “${plan.first_task}”`);
  if (next) bits.push(`next focus session at ${dgFmtTime(next.time)}${next.task ? ` on “${next.task}”` : ""}`);
  if (!bits.length) { el.hidden = true; return; }
  el.replaceChildren();
  const b = document.createElement("b"); b.textContent = "Today's plan: ";
  el.append(b, bits.join(", ") + ".");
  el.hidden = false;
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function renderTasks() {
  const list = openTasks().slice(0, 6);
  const items = list.map((t) => {
    const li = document.createElement("li");
    if (t.parentId) li.className = "sub";
    const span = document.createElement("span"); span.textContent = t.title;
    const go = document.createElement("button"); go.className = "btn btn-quiet"; go.textContent = "Start";
    go.setAttribute("aria-label", `Start focusing on “${t.title}”`);
    go.addEventListener("click", () => startFocus(t.id));
    li.append(span, go);
    return li;
  });
  const add = document.createElement("li"); add.className = "add";
  const form = document.createElement("form"); form.style.display = "contents";
  const inp = document.createElement("input"); inp.className = "input"; inp.placeholder = list.length ? "Add another task" : "What's one thing you could do now?";
  inp.maxLength = 140; inp.setAttribute("aria-label", "New task");
  const btn = document.createElement("button"); btn.className = "btn btn-primary"; btn.type = "submit"; btn.textContent = "Add";
  form.append(inp, btn);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = inp.value.trim();
    if (!title) return;
    tasks.push({ id: crypto.randomUUID(), title, done: false, createdAt: Date.now(), doneAt: null, pomodoros: 0, focusMs: 0 });
    await chrome.storage.local.set({ tasks });
    render();
  });
  add.appendChild(form);
  $("tasks").replaceChildren(...items, add);
}

async function startFocus(taskId) {
  await send({ kind: "dg_focus_start", sessionKind: "pomodoro", taskId: taskId || null });
  location.href = "focus.html";
}

$("wayFocus").addEventListener("click", () => { const t = openTasks()[0]; startFocus(t && t.id); });
$("wayAway").addEventListener("click", async () => {
  await send({ kind: "dg_focus_start", sessionKind: "away", minutes: 10, label: dgInsteadLabel(away) });
  location.href = "focus.html";
});
$("backToFocus").addEventListener("click", () => { location.href = "focus.html"; });

document.querySelectorAll("[data-reason]").forEach((b) => b.addEventListener("click", async () => {
  await send({ kind: "dg_reason", reason: b.dataset.reason });
  $("why").hidden = true;
  $("whyQ").hidden = true;
  $("whyAnswer").textContent = REASON_REPLY[b.dataset.reason];
  $("whyAnswer").hidden = false;
}));

$("passAsk").addEventListener("click", () => { $("passAsk").hidden = true; $("passForm").hidden = false; $("passReason").focus(); });
$("passForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await send({ kind: "dg_pass", app: site, reason: $("passReason").value });
  if (!r.ok) { $("passErr").textContent = r.error; $("passErr").hidden = false; return; }
  const rule = (st.rules || []).find((x) => x.id === site);
  location.href = r.url || `https://${rule ? rule.hosts[0] : ""}`;
});

// ---------------------------------------------------------- 2-minute reset
const STEPS = [
  "Stand up and stretch your arms over your head.",
  "Roll your shoulders back, slowly, five times.",
  "Breathe out longer than you breathe in. Three times.",
  "Get a glass of water and drink some.",
  "Look out a window or at something far away.",
  "Now: what's the one thing you want to do next?",
];
let resetTimer = null;
function startReset() {
  send({ kind: "dg_reset" });
  $("main").hidden = true;
  $("resetView").hidden = false;
  $("resetEnd").hidden = true;
  $("resetSkip").hidden = false;
  const startAt = Date.now(), total = 120000, per = total / STEPS.length;
  $("resetDots").replaceChildren(...STEPS.map(() => document.createElement("i")));
  const tick = () => {
    const el = Date.now() - startAt;
    const i = Math.min(STEPS.length - 1, Math.floor(el / per));
    $("resetStep").textContent = STEPS[i];
    [...$("resetDots").children].forEach((d, k) => d.classList.toggle("on", k <= i));
    $("resetClock").textContent = dgFmtClock(total - el);
    if (el >= total) finishReset();
  };
  tick();
  resetTimer = setInterval(tick, 500);
}
function finishReset() {
  clearInterval(resetTimer);
  $("resetClock").textContent = "Done";
  $("resetStep").textContent = "Nice. You broke the loop. What's next?";
  $("resetEnd").hidden = false;
  $("resetSkip").hidden = true;
  const top = openTasks()[0];
  $("resetFocus").textContent = top ? `Focus on “${top.title}”` : "Start a focus session";
}
$("wayReset").addEventListener("click", startReset);
$("resetSkip").addEventListener("click", finishReset);
$("resetFocus").addEventListener("click", () => { const t = openTasks()[0]; startFocus(t && t.id); });
$("resetTasks").addEventListener("click", () => { location.href = "focus.html"; });

// The agent stepping in while you're on this screen.
let offer = null;
chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
  if (msg && msg.kind === "dg_nudge_page" && !document.hidden) {
    offer = msg.nudge;
    $("offerMsg").textContent = offer.explanation;
    $("offerGo").textContent = offer.cta || "Start";
    $("offer").hidden = false;
    $("offer").scrollIntoView({ behavior: "smooth", block: "start" });
    sendResponse({ shown: true });
  }
});
$("offerGo").addEventListener("click", () => {
  if (!offer) return;
  send({ kind: "dg_agent_accept", ref: offer.id, action: offer.action || "focus", task: offer.task, minutes: offer.minutes });
});
$("offerNo").addEventListener("click", () => {
  if (offer) send({ kind: "dg_nudge_response", ref: offer.id, reason: "dismiss" });
  $("offer").hidden = true;
});

chrome.storage.onChanged.addListener((c) => { if (c.focus || c.tasks || c.days || c.settings || c.profile || c.plan) load(); });
load();
