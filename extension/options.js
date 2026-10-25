// Settings: site modes, focus sessions, app connection, data.

const $ = (id) => document.getElementById(id);
const MODES = [["off", "Off"], ["track", "Track"], ["limit", "Limit"], ["block", "Block"]];
let settings = null;
let toastTimer = null;

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 1600);
}

async function load() {
  ({ settings } = await chrome.storage.local.get("settings"));
  settings = settings || {};
  settings.rules = Array.isArray(settings.rules) && settings.rules.length ? settings.rules : DG_DEFAULT_RULES.map((r) => ({ ...r }));
  settings.focus = { ...DG_DEFAULT_FOCUS, ...(settings.focus || {}) };
  render();
  const st = await chrome.runtime.sendMessage({ kind: "dg_flush" });
  $("connDot").className = "dot" + (st.connected ? " on" : "");
  $("connText").textContent = st.connected
    ? "Connected. Nudges and coaching are on."
    : "Not running. Start it with python3 agent/driftguard.py, or install it to start at login.";
}

async function save(msg = "Saved") {
  await chrome.storage.local.set({ settings });
  await chrome.runtime.sendMessage({ kind: "dg_rules_changed" });
  toast(msg);
}

function siteRow(rule) {
  const row = document.createElement("div");
  row.className = "site";
  const name = document.createElement("div");
  name.className = "name";
  const b = document.createElement("b"); b.textContent = rule.label;
  const s = document.createElement("span");
  s.textContent = rule.hosts.join(", ") + (rule.paths ? ` (${rule.paths.map((p) => p.replace(/\/$/, "")).join(", ")} only)` : "");
  name.append(b, s);

  const seg = document.createElement("div");
  seg.className = "seg";
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", `What happens on ${rule.label}`);
  for (const [mode, text] of MODES) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.mode = mode;
    btn.textContent = text;
    btn.setAttribute("aria-pressed", String(rule.mode === mode));
    btn.addEventListener("click", async () => {
      rule.mode = mode;
      render();
      await save(`${rule.label}: ${text}`);
    });
    seg.appendChild(btn);
  }
  row.append(name, seg);

  if (rule.mode === "limit") {
    const lim = document.createElement("label");
    lim.className = "limit";
    const inp = document.createElement("input");
    inp.className = "input"; inp.type = "number"; inp.min = "1"; inp.max = "600";
    inp.value = rule.limitMin || 15;
    inp.setAttribute("aria-label", `Daily minutes allowed on ${rule.label}`);
    inp.addEventListener("change", async () => {
      rule.limitMin = Math.max(1, Math.min(600, Number(inp.value) || 15));
      inp.value = rule.limitMin;
      await save(`${rule.label}: ${rule.limitMin} min a day`);
    });
    lim.append(inp, "min a day");
    row.appendChild(lim);
  }
  if (!rule.builtin) {
    const rm = document.createElement("button");
    rm.className = "btn btn-text remove";
    rm.textContent = "Remove";
    rm.setAttribute("aria-label", `Remove ${rule.label}`);
    rm.addEventListener("click", async () => {
      settings.rules = settings.rules.filter((r) => r !== rule);
      render();
      await save(`Removed ${rule.label}`);
    });
    row.appendChild(rm);
  }
  return row;
}

function render() {
  $("sites").replaceChildren(...settings.rules.map(siteRow));
  const f = settings.focus;
  $("workMin").value = f.workMin;
  $("breakMin").value = f.breakMin;
  $("strict").checked = f.strict;
  $("passesPerDay").value = f.passesPerDay;
  $("passMinutes").value = f.passMinutes;
  $("passRow1").hidden = $("passRow2").hidden = f.strict;
  $("backendUrl").value = settings.backendUrl || "http://127.0.0.1:8765";
  $("token").value = settings.token || "";
}

$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const host = dgNormalizeHost($("newSite").value);
  const err = $("addErr");
  if (!host) { err.textContent = "That doesn't look like a website. Try something like netflix.com."; err.hidden = false; return; }
  const existing = settings.rules.find((r) => !r.paths && r.hosts.some((h) => h === host || host.endsWith("." + h)));
  if (existing) { err.textContent = `${existing.label} is already in your list.`; err.hidden = false; return; }
  err.hidden = true;
  settings.rules.push({ id: dgRuleIdForHost(host), label: host, hosts: [host], mode: "block", limitMin: 15, builtin: false });
  $("newSite").value = "";
  render();
  await save(`Added ${host}. It's set to Block.`);
});

for (const [id, key, min, max] of [["workMin", "workMin", 5, 120], ["breakMin", "breakMin", 1, 60], ["passesPerDay", "passesPerDay", 0, 20], ["passMinutes", "passMinutes", 1, 30]]) {
  $(id).addEventListener("change", async () => {
    settings.focus[key] = Math.max(min, Math.min(max, Math.round(Number($(id).value)) || min));
    render();
    await save();
  });
}
$("strict").addEventListener("change", async () => {
  settings.focus.strict = $("strict").checked;
  render();
  await save(settings.focus.strict ? "Strict mode on" : "Strict mode off");
});
$("backendUrl").addEventListener("change", async () => { settings.backendUrl = $("backendUrl").value.trim() || "http://127.0.0.1:8765"; await save(); });
$("token").addEventListener("change", async () => { settings.token = $("token").value.trim(); await save(); });

$("export").addEventListener("click", async () => {
  const all = await chrome.storage.local.get(["days", "tasks", "settings"]);
  if (all.settings) delete all.settings.token;
  const blob = new Blob([JSON.stringify({ exportedAt: new Date().toISOString(), ...all }, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `focusguard-ai-${dgDayKey()}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});
$("clear").addEventListener("click", async () => {
  if (!confirm("Clear all report history? Your tasks and settings stay.")) return;
  await chrome.storage.local.set({ days: {} });
  toast("Report history cleared");
});

// ---------------------------------------------------------------- About you
let profile = null;
async function loadProfile() {
  ({ profile } = await chrome.storage.local.get("profile"));
  const p = profile || {};
  const bits = [];
  if (p.name) bits.push(`Hi ${p.name}.`);
  if (p.mainGoal) bits.push(`Your goal: “${p.mainGoal}”.`);
  if (p.triggers && p.triggers.length) bits.push(`What pulls you in: ${DG_TRIGGERS.filter((t) => p.triggers.includes(t.id)).map((t) => t.label.toLowerCase()).join(", ")}.`);
  $("youSummary").textContent = bits.length ? bits.join(" ") : "Answer a few questions so FocusGuard AI can set itself up for you.";
  $("retake").textContent = p.completedAt ? "Retake the questions" : "Answer the questions";
  const pr = p.protect || { enabled: false, start: "22:00", end: "01:00" };
  $("protectOn").checked = Boolean(pr.enabled);
  $("protectStart").value = pr.start || "22:00";
  $("protectEnd").value = pr.end || "01:00";
  $("protectTimes").hidden = !pr.enabled;
}
async function saveProtect() {
  const p = profile || {};
  p.protect = { enabled: $("protectOn").checked, start: $("protectStart").value || "22:00", end: $("protectEnd").value || "01:00" };
  profile = p;
  await chrome.storage.local.set({ profile: p });
  await chrome.runtime.sendMessage({ kind: "dg_rules_changed" });
  $("protectTimes").hidden = !p.protect.enabled;
  toast(p.protect.enabled ? `Protected ${dgFmtTime(p.protect.start)} to ${dgFmtTime(p.protect.end)}` : "Protected hours off");
}
$("protectOn").addEventListener("change", saveProtect);
$("protectStart").addEventListener("change", saveProtect);
$("protectEnd").addEventListener("change", saveProtect);

load();
loadProfile();
