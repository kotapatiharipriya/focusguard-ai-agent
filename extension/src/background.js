// FocusGuard AI — background service worker (Manifest V3).
//
// Works on its own: site rules, blocking, focus sessions, daily stats.
// When the FocusGuard AI app is running on this computer it also syncs events
// there, which adds loop detection, nudges and the AI coach.

importScripts("sites.js", "store.js", "profile.js");

const VERSION = chrome.runtime.getManifest().version;
const FOCUS_PAGE = chrome.runtime.getURL("focus.html");
const BLOCKED_PAGE = chrome.runtime.getURL("blocked.html");
const OWN_PAGES = chrome.runtime.getURL("");
const DEFAULT_SETTINGS = {
  backendUrl: "http://127.0.0.1:8765",
  token: "",
  rules: DG_DEFAULT_RULES,
  focus: DG_DEFAULT_FOCUS,
  pausedUntil: 0,
};
const QUEUE_CAP = 5000;
const BATCH_SIZE = 200;
const HEARTBEAT_MS = 60000;
const KEEP_DAYS = 90;

// ---------------------------------------------------------------------------
// Storage. One promise chain serialises read-modify-write so concurrent
// events can't overwrite each other.
let chain = Promise.resolve();
function serial(fn) { chain = chain.then(fn, fn); return chain; }

async function getSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  const s = { ...DEFAULT_SETTINGS, ...(settings || {}) };
  s.focus = { ...DG_DEFAULT_FOCUS, ...(s.focus || {}) };
  if (!Array.isArray(s.rules) || !s.rules.length) s.rules = DG_DEFAULT_RULES;
  return s;
}
async function getCurrent() { return (await chrome.storage.session.get("current")).current || null; }
function setCurrent(current) { return chrome.storage.session.set({ current }); }
async function getFocus() { return (await chrome.storage.local.get("focus")).focus || null; }

function enqueue(event) {
  return serial(async () => {
    const { queue = [] } = await chrome.storage.local.get("queue");
    queue.push({ id: crypto.randomUUID(), ts: Date.now(), ...event });
    if (queue.length > QUEUE_CAP) queue.splice(0, queue.length - QUEUE_CAP);
    await chrome.storage.local.set({ queue });
  }).then(scheduleFlush);
}

function updateDay(fn, ts) {
  return serial(async () => {
    const { days = {} } = await chrome.storage.local.get("days");
    const key = dgDayKey(ts);
    const day = { ...dgEmptyDay(), ...(days[key] || {}) };
    fn(day);
    days[key] = day;
    const keys = Object.keys(days).sort();
    while (keys.length > KEEP_DAYS) delete days[keys.shift()];
    await chrome.storage.local.set({ days });
  });
}

function appStats(day, app) {
  return (day.apps[app] = day.apps[app] || { ms: 0, visits: 0, shorts: 0 });
}

async function usedTodayMs(app) {
  const { days = {} } = await chrome.storage.local.get("days");
  const day = days[dgDayKey()];
  let ms = (day && day.apps[app] && day.apps[app].ms) || 0;
  const current = await getCurrent();
  if (current && current.app === app) ms += Date.now() - current.startedAt;
  return ms;
}

// ---------------------------------------------------------------------------
// Blocking. Returns why a site should be blocked right now, or null.
//   focus — a focus session is running: every tracked site is off-limits
//   protect — inside the protected hours you chose: every tracked site is closed
//   block — the site is set to Block
//   limit — the site is set to Limit and today's allowance is used up
async function blockReason(c) {
  if (!c) return null;
  const s = await getSettings();
  const now = Date.now();
  if (s.pausedUntil > now) return null;
  const { focus, passes = {}, profile } = await chrome.storage.local.get(["focus", "passes", "profile"]);
  if (focus && !focus.completedAt && focus.until > now && focus.kind !== "break") return "focus";
  if ((passes[c.app] || 0) > now) return null;
  if (dgProtectActive(profile)) return "protect";
  if (c.mode === "block") return "block";
  if (c.mode === "limit" && (await usedTodayMs(c.app)) >= (c.limitMin || 0) * 60000) return "limit";
  return null;
}

const lastRedirect = new Map();
async function redirect(tabId, c, reason, url) {
  const t = lastRedirect.get(tabId);
  if (t && Date.now() - t < 1500) return; // one navigation can fire several events
  lastRedirect.set(tabId, Date.now());
  // Kept only in this browser session, so "open anyway" can return you to the page.
  await chrome.storage.session.set({ ["blocked-" + tabId]: url });
  try {
    await chrome.tabs.update(tabId, { url: `${BLOCKED_PAGE}?site=${encodeURIComponent(c.app)}&why=${reason}` });
  } catch (_) { return; }
  await updateDay((d) => { d.blocked[c.app] = (d.blocked[c.app] || 0) + 1; });
  await enqueue({ type: "blocked", app: c.app, surface: c.surface, reason });
  setTimeout(flush, 7000); // pick up the agent's response if it decides to step in
}

async function guard(tabId, url) {
  if (!url || url.startsWith(OWN_PAGES) || tabId < 0) return false;
  const s = await getSettings();
  const c = dgClassify(url, s.rules);
  const reason = await blockReason(c);
  if (!reason) return false;
  await redirect(tabId, c, reason, url);
  return true;
}

// Close the loop in every open tab, e.g. when a focus session starts.
async function sweepTabs() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) if (tab.url) await guard(tab.id, tab.url);
}

// Catch full page loads early, and single-page-app jumps (Home → Shorts) too.
chrome.webNavigation.onBeforeNavigate.addListener((d) => { if (d.frameId === 0) guard(d.tabId, d.url); });
chrome.webNavigation.onHistoryStateUpdated.addListener(async (d) => {
  if (d.frameId !== 0) return;
  if (!(await guard(d.tabId, d.url))) evaluate("navigate");
});
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (!info.url) return;
  if (await guard(tabId, info.url)) return;
  if (tab.active) evaluate("navigate");
});

// ---------------------------------------------------------------------------
// Attention tracking: one "current" session for the tracked site in the
// focused window's active tab.
let evaluating = Promise.resolve();
function evaluate(reason) {
  evaluating = evaluating.then(() => doEvaluate(reason)).catch((e) => console.warn("evaluate", e));
  return evaluating;
}

async function doEvaluate(reason) {
  const settings = await getSettings();
  const current = await getCurrent();
  let target = null;
  const paused = settings.pausedUntil > Date.now();
  const idle = (await chrome.idle.queryState(60)) !== "active";

  if (!paused && !idle) {
    const win = await chrome.windows.getLastFocused().catch(() => null);
    if (win && win.focused) {
      const [tab] = await chrome.tabs.query({ active: true, windowId: win.id });
      if (tab && tab.url) {
        const c = dgClassify(tab.url, settings.rules);
        if (c) {
          const why = await blockReason(c);
          if (why) await redirect(tab.id, c, why, tab.url);
          else target = { tabId: tab.id, app: c.app, surface: c.surface };
        }
      }
    }
  }

  const same = current && target && current.tabId === target.tabId && current.app === target.app;
  if (same) {
    if (current.surface !== target.surface) await setCurrent({ ...current, surface: target.surface });
    return;
  }
  if (current) await closeSession(current, target ? "switch" : reason);
  if (target) {
    const sessionId = crypto.randomUUID();
    await setCurrent({ ...target, sessionId, startedAt: Date.now(), lastBeat: Date.now() });
    await updateDay((d) => { appStats(d, target.app).visits += 1; });
    await enqueue({ type: "session_start", app: target.app, surface: target.surface, session_id: sessionId, reason });
  } else {
    await setCurrent(null);
  }
}

async function closeSession(current, reason) {
  const dur = Date.now() - current.startedAt;
  // Late scroll/short events from the page get attributed to this session.
  await chrome.storage.session.set({
    lastEnded: { tabId: current.tabId, app: current.app, sessionId: current.sessionId, endedAt: Date.now() },
  });
  await updateDay((d) => {
    appStats(d, current.app).ms += dur;
    d.hours[new Date(current.startedAt).getHours()] += dur;
  }, current.startedAt);
  await enqueue({
    type: "session_end", app: current.app, surface: current.surface,
    session_id: current.sessionId, duration_ms: dur, reason,
  });
}

chrome.tabs.onActivated.addListener(() => evaluate("tab_switch"));
chrome.tabs.onRemoved.addListener((tabId) => { chrome.storage.session.remove("blocked-" + tabId); evaluate("tab_closed"); });
chrome.windows.onFocusChanged.addListener(() => evaluate("window_focus"));
chrome.idle.setDetectionInterval(60);
chrome.idle.onStateChanged.addListener((state) => evaluate(state === "active" ? "resume" : state));

// ---------------------------------------------------------------------------
// Events from content scripts (scroll bursts, short swipes).
const CONTENT_TYPES = new Set(["scroll_burst", "short_viewed"]);
async function handleContentEvent(ev, tabId) {
  if (!ev || !CONTENT_TYPES.has(ev.type)) return;
  const settings = await getSettings();
  if (settings.pausedUntil > Date.now()) return;
  const current = await getCurrent();
  const { lastEnded } = await chrome.storage.session.get("lastEnded");
  let sessionId = null;
  if (current && current.tabId === tabId && current.app === ev.app) sessionId = current.sessionId;
  else if (lastEnded && lastEnded.tabId === tabId && lastEnded.app === ev.app && Date.now() - lastEnded.endedAt < 5000) sessionId = lastEnded.sessionId;
  if (!sessionId) return;
  if (ev.type === "short_viewed") await updateDay((d) => { appStats(d, ev.app).shorts += 1; });
  await enqueue({
    type: ev.type, app: ev.app, surface: ev.surface, session_id: sessionId,
    duration_ms: ev.duration_ms, dwell_ms: ev.dwell_ms, scroll_px: ev.scroll_px,
    wheel_ticks: ev.wheel_ticks, key_nav: ev.key_nav,
  });
}

// ---------------------------------------------------------------------------
// Focus sessions: pomodoro (your 25 minutes), rescue (after a nudge), break.
// Kinds: pomodoro (focus on a task), away (10 minutes of something from your
// "instead" list, screens off), rescue (after a nudge), break. All but break
// keep every tracked site closed.
async function startFocus({ kind = "pomodoro", minutes, taskId = null, ref = null, why = "", until = null, synced = false, label = "" }) {
  const s = await getSettings();
  const mins = Number(minutes) || (kind === "break" ? s.focus.breakMin : kind === "rescue" || kind === "away" ? 10 : s.focus.workMin);
  const now = Date.now();
  const focus = { kind, minutes: mins, startedAt: now, until: until || now + mins * 60000, taskId, ref, why, synced, completedAt: null,
                  label: String(label || "").slice(0, 80) };
  await chrome.storage.local.set({ focus });
  await chrome.alarms.create("dg_focus_end", { when: focus.until });
  if (kind === "rescue") {
    await updateDay((d) => { d.rescues += 1; });
    if (!synced) {
      if (ref) await enqueue({ type: "nudge_response", app: "driftguard", ref, reason: "rescue" });
      await enqueue({ type: "focus_rescue_start", app: "driftguard", ref: ref || undefined, duration_ms: mins * 60000 });
    }
  } else if (kind === "pomodoro" || kind === "away") {
    await enqueue({ type: "focus_start", app: "driftguard", duration_ms: mins * 60000, reason: kind });
  }
  if (kind !== "break") await sweepTabs();
  await evaluate("focus");
  updateBadge();
  flushSoon();
  return focus;
}

async function creditFocus(focus, ms, completed) {
  if (focus.kind === "away") {
    if (completed) await updateDay((d) => { d.aways = (d.aways || 0) + 1; });
    return;
  }
  if (focus.kind === "break") return;
  await updateDay((d) => {
    d.focusMs += ms;
    if (completed && focus.kind === "pomodoro") d.pomodoros += 1;
  });
  if (focus.taskId) {
    await serial(async () => {
      const { tasks = [] } = await chrome.storage.local.get("tasks");
      const t = tasks.find((x) => x.id === focus.taskId);
      if (!t) return;
      t.focusMs = (t.focusMs || 0) + ms;
      if (completed && focus.kind === "pomodoro") t.pomodoros = (t.pomodoros || 0) + 1;
      await chrome.storage.local.set({ tasks });
    });
  }
}

async function completeFocus() {
  const focus = await getFocus();
  if (!focus || focus.completedAt) return;
  if (focus.until > Date.now() + 1000) { chrome.alarms.create("dg_focus_end", { when: focus.until }); return; }
  await chrome.storage.local.set({ focus: { ...focus, completedAt: Date.now() } });
  await creditFocus(focus, focus.until - focus.startedAt, true);
  let title = "Focus session done";
  let message = `${focus.minutes} minutes of real work. Take a short break?`;
  if (focus.kind === "pomodoro") {
    await enqueue({ type: "focus_end", app: "driftguard", reason: "completed", duration_ms: focus.until - focus.startedAt });
    const { tasks = [] } = await chrome.storage.local.get("tasks");
    const t = tasks.find((x) => x.id === focus.taskId);
    if (t) message = `${focus.minutes} minutes on “${t.title}”. Take a short break?`;
  } else if (focus.kind === "away") {
    await enqueue({ type: "focus_end", app: "driftguard", reason: "completed", duration_ms: focus.until - focus.startedAt });
    title = "Welcome back";
    message = `${focus.minutes} minutes away from the screen. Ready to pick something to work on?`;
  } else if (focus.kind === "rescue") {
    title = "Focus Rescue finished";
    message = "You made it out of the loop. How do you feel?";
  } else {
    title = "Break's over";
    message = "Ready for another focus session?";
  }
  chrome.notifications.create("dg-focus-done", { type: "basic", iconUrl: "icons/icon128.png", title, message, priority: 2 });
  updateBadge();
  flushSoon();
}

async function stopFocus(reason = "ended_early") {
  const focus = await getFocus();
  if (!focus) return;
  await chrome.alarms.clear("dg_focus_end");
  await chrome.storage.local.remove("focus");
  if (!focus.completedAt) await creditFocus(focus, Date.now() - focus.startedAt, false);
  if (focus.kind === "rescue") {
    await chrome.storage.session.set({ rescueEndedAt: Date.now() });
    await enqueue({ type: "focus_rescue_end", app: "driftguard", ref: focus.ref || undefined, reason, duration_ms: Date.now() - focus.startedAt });
  } else if ((focus.kind === "pomodoro" || focus.kind === "away") && !focus.completedAt) {
    await enqueue({ type: "focus_end", app: "driftguard", reason, duration_ms: Date.now() - focus.startedAt });
  }
  updateBadge();
  await evaluate("focus_end");
  flushSoon();
}

// Clears a finished session; a rescue reports how you felt.
async function ackFocus(mood) {
  const focus = await getFocus();
  if (!focus || !focus.completedAt) return;
  await chrome.storage.local.remove("focus");
  if (focus.kind === "rescue") {
    await chrome.storage.session.set({ rescueEndedAt: Date.now() });
    await enqueue({ type: "focus_rescue_end", app: "driftguard", ref: focus.ref || undefined,
                    reason: mood ? "completed_" + mood : "completed", duration_ms: focus.until - focus.startedAt });
  }
  updateBadge();
  flushSoon();
}

async function updateBadge() {
  const focus = await getFocus();
  const left = focus && !focus.completedAt ? focus.until - Date.now() : 0;
  if (left > 0) {
    await chrome.action.setBadgeBackgroundColor({ color: focus.kind === "break" ? "#5B6F6C" : "#1F7A74" });
    await chrome.action.setBadgeText({ text: String(Math.ceil(left / 60000)) });
  } else {
    await chrome.action.setBadgeText({ text: "" });
  }
}

// "I need it for a few minutes" — limited, logged, and off in strict mode.
async function grantPass(app, reason) {
  const s = await getSettings();
  if (s.focus.strict) return { ok: false, error: "Strict mode is on, so there are no passes." };
  const focus = await getFocus();
  if (focus && !focus.completedAt && focus.until > Date.now() && focus.kind !== "break") {
    return { ok: false, error: "Finish or stop your focus session first." };
  }
  if (!reason || reason.trim().length < 3) return { ok: false, error: "Write what you need it for." };
  const { days = {} } = await chrome.storage.local.get("days");
  const used = (days[dgDayKey()] || {}).passes || 0;
  if (used >= s.focus.passesPerDay) return { ok: false, error: `You've used all ${s.focus.passesPerDay} passes for today.` };
  const until = Date.now() + s.focus.passMinutes * 60000;
  await serial(async () => {
    const { passes = {} } = await chrome.storage.local.get("passes");
    passes[app] = until;
    await chrome.storage.local.set({ passes });
  });
  await updateDay((d) => { d.passes += 1; });
  // The reason you typed stays on the page; only the fact a pass was used is recorded.
  await enqueue({ type: "pass_used", app, duration_ms: s.focus.passMinutes * 60000 });
  return { ok: true, until };
}

async function pause(minutes) {
  const settings = await getSettings();
  settings.pausedUntil = minutes > 0 ? Date.now() + minutes * 60000 : 0;
  await chrome.storage.local.set({ settings });
  await enqueue({ type: "pause", app: "driftguard", duration_ms: Math.max(0, minutes) * 60000 });
  await evaluate("paused");
  if (!minutes) await sweepTabs();
}

async function status() {
  const settings = await getSettings();
  const { queue = [], lastSync = null, days = {}, focus = null } = await chrome.storage.local.get(["queue", "lastSync", "days", "focus"]);
  return {
    current: await getCurrent(),
    focus,
    today: days[dgDayKey()] || dgEmptyDay(),
    queued: queue.length,
    lastSync,
    connected: Boolean(settings.token && lastSync && lastSync.ok && Date.now() - lastSync.at < 120000),
    pausedUntil: settings.pausedUntil,
    rules: settings.rules,
    focusSettings: settings.focus,
    profile: (await chrome.storage.local.get("profile")).profile || null,
  };
}

// ---------------------------------------------------------------------------
// Messages from pages and content scripts.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const k = msg && msg.kind;
  const reply = (p) => { p.then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) })); return true; };
  if (k === "dg_event" && sender.tab) { handleContentEvent(msg.event, sender.tab.id); return false; }
  if (k === "dg_status") return reply(status());
  if (k === "dg_focus_start") return reply(startFocus({ kind: ["pomodoro", "away"].includes(msg.sessionKind) ? msg.sessionKind : "pomodoro",
                                                         minutes: msg.minutes, taskId: msg.taskId, label: msg.label }));
  if (k === "dg_reset") return reply(updateDay((d) => { d.resets = (d.resets || 0) + 1; }).then(() => ({ ok: true })));
  if (k === "dg_break_start") return reply(startFocus({ kind: "break", minutes: msg.minutes }));
  if (k === "dg_focus_stop") return reply(stopFocus(msg.reason).then(status));
  if (k === "dg_focus_ack") return reply(ackFocus(msg.mood).then(status));
  if (k === "dg_pause") return reply(pause(msg.minutes).then(status));
  if (k === "dg_flush") return reply(flush().then(status));
  if (k === "dg_rules_changed") return reply(sweepTabs().then(() => evaluate("rules")).then(status));
  if (k === "dg_task_done") return reply(updateDay((d) => { d.tasksDone = Math.max(0, d.tasksDone + (msg.delta || 1)); })
    .then(() => (msg.delta || 1) > 0 ? enqueue({ type: "task_done", app: "driftguard" }) : null).then(() => ({ ok: true })));
  if (k === "dg_pass") {
    return reply(grantPass(msg.app, msg.reason).then(async (r) => {
      if (r.ok && sender.tab) {
        const key = "blocked-" + sender.tab.id;
        r.url = (await chrome.storage.session.get(key))[key] || null;
      }
      return r;
    }));
  }
  if (k === "dg_coach") return reply(askCoach(msg.days, msg.labels, msg.modes));
  if (k === "dg_breakdown") return reply(appPost("/api/breakdown", { task: msg.task }));
  if (k === "dg_open_dashboard") {
    return reply(appPost("/api/dashboard", {}).then((r) => {
      if (r.ok) setTimeout(() => chrome.tabs.create({ url: r.url }), r.started ? 3500 : 0);
      return r;
    }));
  }
  if (k === "dg_reason") {
    const ok = ["bored", "avoiding", "tired", "habit", "stress"].includes(msg.reason);
    return reply(ok ? updateDay((d) => { d.reasons = d.reasons || {}; d.reasons[msg.reason] = (d.reasons[msg.reason] || 0) + 1; }).then(() => ({ ok: true }))
                    : Promise.resolve({ ok: false }));
  }
  if (k === "dg_nudge_response") {
    enqueue({ type: "nudge_response", app: "driftguard", ref: msg.ref, reason: msg.reason }).then(flushSoon);
    return false;
  }
  if (k === "dg_rescue_start") return reply(startFocus({ kind: "rescue", minutes: msg.minutes, ref: msg.ref, why: msg.why }));
  if (k === "dg_agent_accept") return reply(acceptAgent(msg, sender.tab && sender.tab.id));
  return false;
});

// ---------------------------------------------------------------------------
// Sync with the FocusGuard AI app (optional — everything above works without it).
let flushTimer = null;
function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(() => { flushTimer = null; flush(); }, 5000);
}
function flushSoon() { setTimeout(flush, 300); }

function backend(settings, path) { return settings.backendUrl.replace(/\/$/, "") + path; }

async function tryPair(settings) {
  try {
    const res = await fetch(backend(settings, "/api/pair"));
    if (!res.ok) return settings;
    const { token } = await res.json();
    if (token) {
      settings = { ...settings, token };
      await chrome.storage.local.set({ settings });
    }
  } catch (_) { /* app not running */ }
  return settings;
}

let flushing = false;
async function flush() {
  if (flushing) return;
  flushing = true;
  try {
    let settings = await getSettings();
    if (!settings.token) settings = await tryPair(settings);
    if (!settings.token) return;
    const { queue = [] } = await chrome.storage.local.get("queue");
    if (!queue.length) {
      const r = await fetch(backend(settings, "/api/control"), { headers: { "X-DriftGuard-Token": settings.token } });
      if (r.ok) {
        await chrome.storage.local.set({ lastSync: { ok: true, at: Date.now(), error: "" } });
        const ctl = await r.json();
        await applyControl(ctl);
        await runCommands(ctl.commands);
        if ("plan" in ctl) await chrome.storage.local.set({ plan: ctl.plan || null });
        for (const n of ctl.nudges || []) await deliverNudge(n);
      }
      if (stateDirty) await pushState(settings);
      return;
    }
    const batch = queue.slice(0, BATCH_SIZE);
    const res = await fetch(backend(settings, "/api/events"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-DriftGuard-Token": settings.token },
      body: JSON.stringify({ source: "browser_extension", client_version: VERSION,
                             tz_offset_min: -new Date().getTimezoneOffset(), events: batch }),
    });
    if (res.status === 401) await tryPair({ ...settings, token: "" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const replyBody = await res.json().catch(() => ({}));
    const sent = new Set(batch.map((e) => e.id));
    await serial(async () => {
      const { queue: q = [] } = await chrome.storage.local.get("queue");
      await chrome.storage.local.set({ queue: q.filter((e) => !sent.has(e.id)) });
    });
    await chrome.storage.local.set({ lastSync: { ok: true, at: Date.now(), error: "" } });
    await applyControl(replyBody.control);
    await runCommands(replyBody.commands);
    if ("plan" in replyBody) await chrome.storage.local.set({ plan: replyBody.plan || null });
    for (const n of replyBody.nudges || []) await deliverNudge(n);
    if (queue.length > BATCH_SIZE) scheduleFlush();
    if (stateDirty) await pushState(settings);
  } catch (e) {
    await chrome.storage.local.set({ lastSync: { ok: false, at: Date.now(), error: String(e.message || e) } });
  } finally {
    flushing = false;
  }
}

// The app is the source of truth for pause / rescue started from the tray.
async function applyControl(ctl) {
  if (!ctl) return;
  const settings = await getSettings();
  const localPause = settings.pausedUntil > Date.now() ? settings.pausedUntil : 0;
  if ((ctl.paused_until || 0) !== localPause) {
    settings.pausedUntil = ctl.paused_until || 0;
    await chrome.storage.local.set({ settings });
  }
  const focus = await getFocus();
  const { rescueEndedAt = 0 } = await chrome.storage.session.get("rescueEndedAt");
  const serverRescue = ctl.rescue_until && ctl.rescue_until > Date.now();
  if (serverRescue) {
    if (!focus && Date.now() - rescueEndedAt > 15000) {
      await startFocus({ kind: "rescue", until: ctl.rescue_until, minutes: Math.round((ctl.rescue_until - Date.now()) / 60000), ref: ctl.rescue_ref, synced: true });
    } else if (focus && focus.kind === "rescue" && !focus.synced) {
      await chrome.storage.local.set({ focus: { ...focus, synced: true } });
    }
  } else if (focus && focus.kind === "rescue" && focus.synced && !focus.completedAt) {
    await stopFocus("ended_elsewhere"); // ended from the tray or status page
  }
}

async function deliverNudge(nudge) {
  const current = await getCurrent();
  if (current) {
    try { await chrome.tabs.sendMessage(current.tabId, { kind: "dg_nudge", nudge }); return; } catch (_) { /* fall through */ }
  }
  // You're on one of FocusGuard AI's own pages (e.g. the blocked screen): show it there.
  try {
    const r = await chrome.runtime.sendMessage({ kind: "dg_nudge_page", nudge });
    if (r && r.shown) return;
  } catch (_) { /* no FocusGuard AI page open */ }
  chrome.notifications.create("dg-" + nudge.id, {
    type: "basic", iconUrl: "icons/icon128.png", priority: 2,
    title: nudge.headline, message: nudge.explanation,
    buttons: [{ title: nudge.action ? (nudge.cta || "Start") : `Start Focus Rescue (${nudge.rescue_minutes} min)` }, { title: "Not now" }],
  });
  await chrome.storage.session.set({ ["nudge-" + nudge.id]: nudge });
}

chrome.notifications.onButtonClicked.addListener(async (notifId, idx) => {
  if (!notifId.startsWith("dg-") || notifId === "dg-focus-done") return;
  const id = notifId.slice(3);
  const key = "nudge-" + id;
  const { [key]: nudge } = await chrome.storage.session.get(key);
  chrome.notifications.clear(notifId);
  if (idx === 0 && nudge && nudge.action) await acceptAgent({ ref: id, action: nudge.action, task: nudge.task, minutes: nudge.minutes }, null);
  else if (idx === 0) await startFocus({ kind: "rescue", minutes: nudge && nudge.rescue_minutes, ref: id, why: nudge && nudge.headline });
  else { await enqueue({ type: "nudge_response", app: "driftguard", ref: id, reason: "dismiss" }); flushSoon(); }
});
chrome.notifications.onClicked.addListener((id) => {
  if (id === "dg-focus-done") { chrome.tabs.create({ url: FOCUS_PAGE }); chrome.notifications.clear(id); }
});

async function askCoach(days, labels, modes) {
  const settings = await getSettings();
  if (!settings.token) return { ok: false, error: "not_connected" };
  try {
    const res = await fetch(backend(settings, "/api/coach"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-DriftGuard-Token": settings.token },
      body: JSON.stringify({ days, labels, modes }),
    });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    return { ok: true, ...(await res.json()) };
  } catch (_) {
    return { ok: false, error: "not_running" };
  }
}

// ---------------------------------------------------------------------------
// Timers. The alarm survives the worker being suspended; the interval gives
// quicker reactions (limits running out, tray rescue) while you're on a site.
chrome.alarms.create("dg_tick", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "dg_focus_end") return completeFocus();
  if (alarm.name !== "dg_tick") return;
  const focus = await getFocus();
  if (focus && !focus.completedAt && focus.until <= Date.now()) await completeFocus();
  if (focus && focus.completedAt && focus.kind === "rescue" && Date.now() - focus.completedAt > 90000) await ackFocus(null);
  if (focus && focus.completedAt && Date.now() - focus.completedAt > 30 * 60000) await chrome.storage.local.remove("focus");
  const { profile } = await chrome.storage.local.get("profile");
  const prot = dgProtectActive(profile);
  const { wasProtected = false } = await chrome.storage.session.get("wasProtected");
  if (prot && !wasProtected) await sweepTabs();   // protected hours just began
  await chrome.storage.session.set({ wasProtected: prot });
  await evaluate("tick");
  await heartbeat();
  updateBadge();
  flush();
});

async function heartbeat() {
  const current = await getCurrent();
  if (current && Date.now() - (current.lastBeat || 0) >= HEARTBEAT_MS) {
    await setCurrent({ ...current, lastBeat: Date.now() });
    await enqueue({ type: "heartbeat", app: current.app, surface: current.surface,
                    session_id: current.sessionId, duration_ms: Date.now() - current.startedAt });
  }
}

setInterval(async () => {
  const current = await getCurrent();
  if (current) { await evaluate("check"); flush(); } // limit reached or pass expired → redirect
  updateBadge();
}, 10000);

chrome.runtime.onStartup.addListener(() => { evaluate("startup"); updateBadge(); });
chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  const { settings } = await chrome.storage.local.get("settings");
  const merged = { ...DEFAULT_SETTINGS, ...(settings || {}) };
  merged.focus = { ...DG_DEFAULT_FOCUS, ...(merged.focus || {}) };
  if (!Array.isArray(merged.rules)) merged.rules = DG_DEFAULT_RULES;
  // Add built-in sites introduced by an update, keeping your existing choices.
  for (const r of DG_DEFAULT_RULES) if (!merged.rules.some((x) => x.id === r.id)) merged.rules.push(r);
  delete merged.sites; delete merged.disabledApps; // v0.1 settings
  await chrome.storage.local.set({ settings: merged });
  if (reason === "install") chrome.tabs.create({ url: chrome.runtime.getURL("onboarding.html") });
  flush();
  evaluate("installed");
});

// ---------------------------------------------------------------------------
// The app as a hub: the extension shares its state (stats, tasks, sites) with
// the FocusGuard AI app on this computer, and runs commands the app queues —
// e.g. "start a 25-minute focus" asked through Claude via the MCP server.
let stateDirty = true;
let stateTimer = null;
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.days || changes.tasks || changes.settings || changes.focus || changes.profile) {
    stateDirty = true;
    if (!stateTimer) stateTimer = setTimeout(async () => { stateTimer = null; await pushState(await getSettings()); }, 3000);
  }
});

async function pushState(settings) {
  if (!settings.token) return;
  const { days = {}, tasks = [], focus = null, profile = null } = await chrome.storage.local.get(["days", "tasks", "focus", "profile"]);
  const keep = new Set(dgLastDays(30));
  const recent = Object.fromEntries(Object.entries(days).filter(([k]) => keep.has(k)));
  try {
    const res = await fetch(backend(settings, "/api/state"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-DriftGuard-Token": settings.token },
      body: JSON.stringify({ days: recent, tasks, rules: settings.rules, focus, focusSettings: settings.focus, profile }),
    });
    if (res.ok) {
      stateDirty = false;
      await runCommands((await res.json()).commands);
    }
  } catch (_) { /* app not running */ }
}

async function appPost(path, body) {
  const settings = await getSettings();
  if (!settings.token) return { ok: false, error: "not_connected" };
  try {
    const res = await fetch(backend(settings, path), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-DriftGuard-Token": settings.token },
      body: JSON.stringify(body),
    });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    return { ok: true, ...(await res.json()) };
  } catch (_) {
    return { ok: false, error: "not_running" };
  }
}

function findTask(tasks, ref) {
  if (!ref) return null;
  const r = String(ref).toLowerCase().trim();
  return tasks.find((t) => t.id === ref) || tasks.find((t) => !t.done && t.title.toLowerCase() === r)
      || tasks.find((t) => !t.done && t.title.toLowerCase().includes(r)) || null;
}

async function runCommands(cmds) {
  if (!cmds || !cmds.length) return;
  for (const c of cmds) {
    try { await runCommand(c); } catch (e) { console.warn("command failed", c, e); }
  }
  // Report the result straight away so the dashboard and Claude see it.
  stateDirty = true;
  setTimeout(async () => pushState(await getSettings()), 500);
}

async function runCommand({ type, args = {} }) {
  if (type === "add_task" && args.title) {
    await serial(async () => {
      const { tasks = [] } = await chrome.storage.local.get("tasks");
      const parent = args.parent ? findTask(tasks, args.parent) : null;
      const t = { id: crypto.randomUUID(), title: String(args.title).slice(0, 140), done: false, createdAt: Date.now(),
                  doneAt: null, pomodoros: 0, focusMs: 0 };
      if (parent) {
        t.parentId = parent.id;
        let at = tasks.indexOf(parent) + 1;
        while (at < tasks.length && tasks[at].parentId === parent.id) at++;
        tasks.splice(at, 0, t);
      } else {
        tasks.push(t);
      }
      await chrome.storage.local.set({ tasks });
    });
  } else if (type === "complete_task") {
    let done = false;
    await serial(async () => {
      const { tasks = [] } = await chrome.storage.local.get("tasks");
      const t = findTask(tasks, args.id || args.title);
      if (t && !t.done) { t.done = true; t.doneAt = Date.now(); done = true; await chrome.storage.local.set({ tasks }); }
    });
    if (done) await updateDay((d) => { d.tasksDone += 1; });
  } else if (type === "start_focus") {
    let taskId = null;
    if (args.task) {
      await serial(async () => {
        const { tasks = [] } = await chrome.storage.local.get("tasks");
        let t = findTask(tasks, args.task);
        if (!t) {
          t = { id: crypto.randomUUID(), title: String(args.task).slice(0, 140), done: false, createdAt: Date.now(), doneAt: null, pomodoros: 0, focusMs: 0 };
          tasks.push(t);
          await chrome.storage.local.set({ tasks });
        }
        taskId = t.id;
      });
    }
    await startFocus({ kind: "pomodoro", minutes: args.minutes, taskId });
    chrome.tabs.create({ url: FOCUS_PAGE, active: true });
  } else if (type === "stop_focus") {
    await stopFocus("ended_early");
  } else if (type === "pause") {
    await pause(Number(args.minutes) || 0);
  } else if (type === "set_site_mode" && args.site && ["off", "track", "limit", "block"].includes(args.mode)) {
    const settings = await getSettings();
    const q = String(args.site).toLowerCase().trim();
    let rule = settings.rules.find((r) => r.id === q || r.label.toLowerCase() === q)
            || settings.rules.find((r) => !r.paths && r.hosts.includes(dgNormalizeHost(q)));
    if (!rule) {
      const host = dgNormalizeHost(q);
      if (!host) return;
      rule = { id: dgRuleIdForHost(host), label: host, hosts: [host], mode: "block", limitMin: 15, builtin: false };
      settings.rules.push(rule);
    }
    rule.mode = args.mode;
    if (args.limit_minutes) rule.limitMin = Math.max(1, Math.min(600, Number(args.limit_minutes) || 15));
    await chrome.storage.local.set({ settings });
    await sweepTabs();
    await evaluate("rules");
  }
}

// The AI agent offered something (a planned focus session, or a way out of a
// loop) and you clicked it.
async function acceptAgent({ ref, action, task, minutes }, tabId) {
  await enqueue({ type: "nudge_response", app: "driftguard", ref, reason: "accept" });
  if (action === "focus") {
    let taskId = null;
    if (task) {
      const { tasks = [] } = await chrome.storage.local.get("tasks");
      const t = findTask(tasks, task);
      taskId = t ? t.id : null;
    }
    await startFocus({ kind: "pomodoro", minutes, taskId });
  } else {
    await startFocus({ kind: "away", minutes: minutes || 10, label: "A break away from the screen" });
  }
  if (tabId != null) { try { await chrome.tabs.update(tabId, { url: FOCUS_PAGE }); } catch (_) { /* tab gone */ } }
  else chrome.tabs.create({ url: FOCUS_PAGE });
  flushSoon();
  return { ok: true };
}
