// FocusGuard AI — shared storage shapes and formatting helpers.
// Loaded by the background worker (importScripts) and by extension pages.
//
// chrome.storage.local keys:
//   settings  { rules, focus: {workMin, breakMin, strict, passMinutes, passesPerDay}, backendUrl, token, pausedUntil }
//   days      { "YYYY-MM-DD": Day }          — written only by the background worker
//   tasks     [ {id, title, done, createdAt, doneAt, pomodoros, focusMs} ] — written by pages
//   focus     { kind: "pomodoro"|"rescue"|"break", minutes, startedAt, until, taskId, ref, why, completedAt }
//   passes    { [ruleId]: untilMs }
//
// Day = { apps: {[id]: {ms, visits, shorts}}, blocked: {[id]: n}, hours: [24 × ms],
//         focusMs, pomodoros, tasksDone, passes, rescues }

/* exported DG_DEFAULT_FOCUS, dgDayKey, dgEmptyDay, dgFmtDuration, dgFmtClock, dgLastDays, dgDistractMs */

const DG_DEFAULT_FOCUS = { workMin: 25, breakMin: 5, strict: false, passMinutes: 5, passesPerDay: 3 };

function dgDayKey(ts) {
  const d = new Date(ts == null ? Date.now() : ts);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function dgEmptyDay() {
  return { apps: {}, blocked: {}, hours: new Array(24).fill(0), focusMs: 0, pomodoros: 0, tasksDone: 0, passes: 0, rescues: 0 };
}

// "1 h 05 min", "12 min", "under a minute"
function dgFmtDuration(ms) {
  const min = Math.round((ms || 0) / 60000);
  if (min < 1) return ms > 0 ? "under a minute" : "0 min";
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")} min`;
}

// "24:59"
function dgFmtClock(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Keys for the last n days, oldest first, ending today.
function dgLastDays(n) {
  const out = [];
  const now = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i, 12);
    out.push(dgDayKey(d.getTime()));
  }
  return out;
}

function dgDistractMs(day) {
  if (!day) return 0;
  return Object.values(day.apps || {}).reduce((a, x) => a + (x.ms || 0), 0);
}

if (typeof module !== "undefined") {
  module.exports = { DG_DEFAULT_FOCUS, dgDayKey, dgEmptyDay, dgFmtDuration, dgFmtClock, dgLastDays, dgDistractMs };
}
