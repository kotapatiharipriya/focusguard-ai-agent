// FocusGuard AI — the "get to know you" profile and how answers become settings.
// Shared by the onboarding page, Settings and the background worker.
//
// chrome.storage.local "profile":
//   { name, blockSites: [ruleId], otherSites: [host], times: [id], triggers: [id],
//     mainGoal, tasks: [title], instead: [id or your own words], allowPasses, focusMin,
//     protect: {enabled, start, end}, completedAt }

/* exported DG_TIMES, DG_TRIGGERS, DG_GOALS, DG_STRICTNESS, DG_INSTEAD, dgApplyProfile, dgProtectWindow, dgProtectActive, dgFmtTime, dgInsteadLabel */

// Quick things to do instead of scrolling. Shown on the blocked screen.
const DG_INSTEAD = [
  { id: "walk",     label: "Take a short walk" },
  { id: "stretch",  label: "Stretch for a few minutes" },
  { id: "water",    label: "Get water or a snack" },
  { id: "read",     label: "Read 10 pages" },
  { id: "tidy",     label: "Tidy up one thing" },
  { id: "message",  label: "Message a friend" },
  { id: "music",    label: "Play or listen to music" },
  { id: "practice", label: "Practice a skill" },
];

function dgInsteadLabel(item) {
  const known = DG_INSTEAD.find((x) => x.id === item);
  return known ? known.label : String(item);
}

const DG_TIMES = [
  { id: "morning",   label: "Mornings",              start: "07:30", end: "10:30" },
  { id: "afternoon", label: "Afternoons",            start: "13:00", end: "17:00" },
  { id: "evening",   label: "Evenings",              start: "19:00", end: "22:00" },
  { id: "late",      label: "Late at night",         start: "22:00", end: "01:00" },
  { id: "bed",       label: "In bed before sleep",   start: "22:30", end: "01:30" },
  { id: "work",      label: "While I should be working or studying" },
];

const DG_TRIGGERS = [
  { id: "bored",    label: "Boredom" },
  { id: "avoiding", label: "Avoiding something hard" },
  { id: "stress",   label: "Stress or anxiety" },
  { id: "tired",    label: "Being tired" },
  { id: "habit",    label: "Pure habit, I don't even notice" },
  { id: "fomo",     label: "Not wanting to miss out" },
];

const DG_GOALS = [
  { id: "study",    label: "Studies and exams" },
  { id: "career",   label: "Job search or career" },
  { id: "project",  label: "A project I care about" },
  { id: "sleep",    label: "Better sleep" },
  { id: "health",   label: "Exercise and health" },
  { id: "people",   label: "Time with family and friends" },
  { id: "reading",  label: "Reading and learning" },
  { id: "create",   label: "Making things: art, music, writing" },
];

const DG_STRICTNESS = [
  { id: "gentle",   label: "Gentle", text: "Give me 30 minutes a day on each site, then block it." },
  { id: "balanced", label: "Balanced", text: "Block short videos completely. Allow 15 minutes a day on the rest." },
  { id: "strict",   label: "Strict", text: "Block them all, and no “I need it” passes." },
];

const SHORT_FORM = new Set(["youtube_shorts", "instagram_reels", "tiktok"]);

// Suggested protected hours: the latest risky window you picked.
function dgProtectWindow(times) {
  const picked = DG_TIMES.filter((t) => t.start && (times || []).includes(t.id));
  if (!picked.length) return null;
  const night = picked.filter((t) => ["evening", "late", "bed"].includes(t.id));
  const pool = night.length ? night : picked;
  return { start: pool[0].start, end: pool[pool.length - 1].end };
}

function toMin(hhmm) {
  const [h, m] = String(hhmm || "0:0").split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

function dgProtectActive(profile, now = new Date()) {
  const p = profile && profile.protect;
  if (!p || !p.enabled || !p.start || !p.end) return false;
  const t = now.getHours() * 60 + now.getMinutes();
  const s = toMin(p.start), e = toMin(p.end);
  return s <= e ? t >= s && t < e : t >= s || t < e; // windows can cross midnight
}

function dgFmtTime(hhmm) {
  const m = toMin(hhmm);
  const h = Math.floor(m / 60), mm = m % 60;
  return `${((h + 11) % 12) + 1}${mm ? ":" + String(mm).padStart(2, "0") : ""} ${h < 12 ? "AM" : "PM"}`;
}

// Turns answers into site rules and focus settings. Returns a list of plain
// sentences describing what changed, for the summary screen.
function dgApplyProfile(settings, profile) {
  const changes = [];
  const rules = settings.rules.map((r) => ({ ...r }));
  const block = new Set(profile.blockSites || profile.sites || []);
  const others = [...(profile.otherSites || []), ...(profile.otherSite ? [profile.otherSite] : [])];

  for (const raw of others) {
    const host = dgNormalizeHost(raw);
    if (!host) continue;
    let r = rules.find((x) => !x.paths && x.hosts.includes(host));
    if (!r) {
      r = { id: dgRuleIdForHost(host), label: host, hosts: [host], mode: "block", limitMin: 15, builtin: false };
      rules.push(r);
    }
    block.add(r.id);
  }
  for (const r of rules) if (block.has(r.id)) r.mode = "block";
  const blocked = rules.filter((r) => block.has(r.id)).map((r) => r.label);
  if (blocked.length) changes.push(`Blocked: ${blocked.join(", ")}. Opening them brings you to your own screen instead.`);

  const focus = { ...settings.focus };
  if (profile.focusMin) {
    focus.workMin = profile.focusMin;
    focus.breakMin = profile.focusMin >= 45 ? 10 : 5;
    changes.push(`Focus sessions last ${focus.workMin} minutes, with ${focus.breakMin}-minute breaks.`);
  }
  if (profile.allowPasses === false) {
    focus.strict = true;
    changes.push("No exceptions: blocked sites stay blocked, even when you ask.");
  } else if (profile.allowPasses === true) {
    focus.strict = false;
    changes.push(`If you really need a blocked site, you can open it for ${focus.passMinutes || 5} minutes, up to ${focus.passesPerDay || 3} times a day.`);
  }
  if ((profile.tasks || []).length) changes.push(`Your tasks are ready: ${profile.tasks.join(", ")}.`);
  if ((profile.instead || []).length) changes.push(`Instead of scrolling, FocusGuard AI will suggest: ${profile.instead.map(dgInsteadLabel).join(", ").toLowerCase()}.`);
  if (profile.protect && profile.protect.enabled) {
    changes.push(`Protected time ${dgFmtTime(profile.protect.start)} to ${dgFmtTime(profile.protect.end)}: every tracked site is closed then.`);
  }
  return { settings: { ...settings, rules, focus }, changes };
}

if (typeof module !== "undefined") {
  module.exports = { DG_TIMES, DG_TRIGGERS, DG_GOALS, DG_STRICTNESS, DG_INSTEAD, dgApplyProfile, dgProtectWindow, dgProtectActive, dgFmtTime, dgInsteadLabel };
}
