// Report page: what you watched, how often FocusGuard AI turned you around,
// and how much you focused. Data never leaves this computer.

const $ = (id) => document.getElementById(id);
let range = 7;
let days = {};
let rules = [];
let current = null;

const label = (id) => ((rules.find((r) => r.id === id) || {}).label) || id.replace(/^site_/, "").replace(/_/g, ".");
const sum = (arr) => arr.reduce((a, b) => a + b, 0);
const minutes = (ms) => Math.round(ms / 60000);

async function load() {
  const st = await chrome.runtime.sendMessage({ kind: "dg_status" });
  ({ days = {} } = await chrome.storage.local.get("days"));
  rules = st.rules || [];
  current = st.current;
  render();
  loadCoach();
}

function liveMs(key) {
  return current && key === dgDayKey() ? Date.now() - current.startedAt : 0;
}

function totals(keys) {
  const t = { distract: 0, focus: 0, blocked: 0, pomodoros: 0, tasks: 0, shorts: 0, apps: {} };
  for (const k of keys) {
    const d = days[k];
    t.distract += liveMs(k);
    if (current && k === dgDayKey()) {
      const a = (t.apps[current.app] = t.apps[current.app] || { ms: 0, visits: 0, shorts: 0, blocked: 0 });
      a.ms += liveMs(k);
    }
    if (!d) continue;
    t.distract += dgDistractMs(d);
    t.focus += d.focusMs || 0;
    t.pomodoros += d.pomodoros || 0;
    t.tasks += d.tasksDone || 0;
    for (const [id, a] of Object.entries(d.apps || {})) {
      const x = (t.apps[id] = t.apps[id] || { ms: 0, visits: 0, shorts: 0, blocked: 0 });
      x.ms += a.ms || 0; x.visits += a.visits || 0; x.shorts += a.shorts || 0;
      t.shorts += a.shorts || 0;
    }
    for (const [id, n] of Object.entries(d.blocked || {})) {
      const x = (t.apps[id] = t.apps[id] || { ms: 0, visits: 0, shorts: 0, blocked: 0 });
      x.blocked += n;
      t.blocked += n;
    }
  }
  return t;
}

function prevKeys(n) {
  const all = dgLastDays(n * 2);
  return all.slice(0, n);
}

// ---------------------------------------------------------------- summary
function renderSummary(t) {
  const el = $("summary");
  el.replaceChildren();
  const num = (text, cls = "num") => { const s = document.createElement("span"); s.className = cls; s.textContent = text; return s; };
  if (!t.distract && !t.focus && !t.blocked) {
    el.textContent = range === 1 ? "Nothing tracked yet today." : "Nothing tracked yet. Browse as usual and your report fills in.";
    return;
  }
  if (range === 1) {
    el.append("Today: ", num(dgFmtDuration(t.distract)), " on distracting sites and ", num(dgFmtDuration(t.focus)), " focused.");
    return;
  }
  const prev = totals(prevKeys(range)).distract;
  el.append(num(dgFmtDuration(t.distract)), ` on distracting sites in the last ${range} days`);
  if (prev > 60000) {
    const change = Math.round(((t.distract - prev) / prev) * 100);
    if (change <= -1) el.append(", ", num(`${Math.abs(change)}% less`), ` than the ${range} days before.`);
    else if (change >= 1) el.append(", ", num(`${change}% more`, "num up"), ` than the ${range} days before.`);
    else el.append(", about the same as before.");
  } else {
    el.append(".");
  }
}

function renderFigures(t) {
  const items = [
    [String(t.blocked), t.blocked === 1 ? "time turned around" : "times turned around"],
    [dgFmtDuration(t.focus), "focused"],
    [String(t.pomodoros), t.pomodoros === 1 ? "focus session finished" : "focus sessions finished"],
    [String(t.tasks), t.tasks === 1 ? "task done" : "tasks done"],
  ];
  $("figures").replaceChildren(...items.map(([v, k]) => {
    const d = document.createElement("div");
    d.className = "figure";
    const b = document.createElement("b"); b.textContent = v;
    const s = document.createElement("span"); s.textContent = k;
    d.append(b, s);
    return d;
  }));
}

// ------------------------------------------------------------------ chart
function niceMax(v) {
  const steps = [10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 480, 600, 720];
  return steps.find((s) => s >= v) || Math.ceil(v / 120) * 120;
}

function svgEl(tag, attrs, text) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}

function renderChart(keys) {
  const W = 640, H = 220, L = 36, B = 24, T = 8;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  let series;
  if (range === 1) {
    $("chartTitle").textContent = "Hour by hour";
    $("chartLede").textContent = "When today's time on distracting sites happened.";
    $("legendF").hidden = true;
    const d = days[keys[0]] || dgEmptyDay();
    series = d.hours.map((ms, h) => ({ x: h % 6 === 0 ? `${((h + 11) % 12) + 1}${h < 12 ? "a" : "p"}` : "", d: minutes(ms), f: null, title: `${((h + 11) % 12) + 1} ${h < 12 ? "AM" : "PM"}` }));
  } else {
    $("chartTitle").textContent = "Each day";
    $("chartLede").textContent = "Time on distracting sites next to time spent focusing.";
    $("legendF").hidden = false;
    series = keys.map((k, i) => {
      const dt = new Date(k + "T12:00:00");
      const x = range === 7 ? dt.toLocaleDateString(undefined, { weekday: "short" })
                            : (i % 5 === 0 || i === keys.length - 1 ? String(dt.getDate()) : "");
      const d = days[k];
      return { x, d: minutes(dgDistractMs(d) + liveMs(k)), f: minutes((d && d.focusMs) || 0),
               title: dt.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" }) };
    });
  }
  const max = niceMax(Math.max(1, ...series.map((s) => Math.max(s.d, s.f || 0))));
  const plotH = H - B - T;
  const y = (v) => T + plotH - (v / max) * plotH;
  for (let i = 0; i <= 3; i++) {
    const v = Math.round((max / 3) * i);
    svg.append(svgEl("line", { class: "grid", x1: L, x2: W, y1: y(v), y2: y(v) }));
    const lbl = v < 60 ? `${v}m` : v % 60 === 0 ? `${v / 60}h` : `${Math.floor(v / 60)}h${v % 60}`;
    svg.append(svgEl("text", { class: "axis", x: L - 8, y: y(v) + 4, "text-anchor": "end" }, lbl));
  }
  const slot = (W - L) / series.length;
  const pair = series[0].f !== null;
  const bw = Math.max(2, Math.min(pair ? 16 : 18, slot * (pair ? 0.34 : 0.6)));
  series.forEach((s, i) => {
    const cx = L + slot * i + slot / 2;
    const bars = pair ? [["bar-d", s.d, cx - bw - 1], ["bar-f", s.f, cx + 1]] : [["bar-d", s.d, cx - bw / 2]];
    for (const [cls, v, x] of bars) {
      const h = Math.max(v > 0 ? 2 : 0, (v / max) * plotH);
      const r = svgEl("rect", { class: cls, x: x.toFixed(1), y: (T + plotH - h).toFixed(1), width: bw.toFixed(1), height: h.toFixed(1), rx: Math.min(3, bw / 2) });
      r.append(svgEl("title", {}, `${s.title}: ${v} min ${cls === "bar-d" ? "on distracting sites" : "focused"}`));
      svg.append(r);
    }
    if (s.x) svg.append(svgEl("text", { class: "axis", x: cx, y: H - 6, "text-anchor": "middle" }, s.x));
  });
  svg.setAttribute("aria-label", series.map((s) => `${s.title}: ${s.d} minutes distracted${s.f !== null ? `, ${s.f} focused` : ""}`).join("; "));
  $("chart").replaceChildren(svg);
}

// ------------------------------------------------------------------ sites
function renderSites(t) {
  const rows = Object.entries(t.apps).filter(([, a]) => a.ms > 30000 || a.blocked > 0).sort((a, b) => b[1].ms - a[1].ms);
  const table = $("sites");
  table.replaceChildren();
  const head = document.createElement("tr");
  for (const h of ["Site", "Time", "Visits", "Shorts", "Turned around"]) { const th = document.createElement("th"); th.textContent = h; head.appendChild(th); }
  table.appendChild(head);
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5; td.className = "muted"; td.style.textAlign = "left";
    td.textContent = "No time on tracked sites in this period.";
    tr.appendChild(td); table.appendChild(tr);
    return;
  }
  const max = Math.max(...rows.map(([, a]) => a.ms), 1);
  for (const [id, a] of rows) {
    const tr = document.createElement("tr");
    const site = document.createElement("td");
    const wrap = document.createElement("div"); wrap.className = "site";
    const name = document.createElement("span"); name.textContent = label(id);
    const meter = document.createElement("div"); meter.className = "meter";
    const fill = document.createElement("i"); fill.style.width = `${Math.max(2, (a.ms / max) * 100)}%`;
    meter.appendChild(fill); wrap.append(name, meter); site.appendChild(wrap);
    tr.appendChild(site);
    for (const v of [dgFmtDuration(a.ms), a.visits, a.shorts || "–", a.blocked || "–"]) {
      const td = document.createElement("td"); td.textContent = v; tr.appendChild(td);
    }
    table.appendChild(tr);
  }
}

// ------------------------------------------------------------------ coach
function localInsights() {
  const keys = dgLastDays(7);
  const hours = new Array(24).fill(0);
  for (const k of keys) (days[k] && days[k].hours || []).forEach((ms, h) => { hours[h] += ms; });
  const t = totals(keys);
  const out = [];
  const peak = hours.indexOf(Math.max(...hours));
  if (hours[peak] > 5 * 60000) out.push(`Most of your drifting happens around ${((peak + 11) % 12) + 1} ${peak < 12 ? "AM" : "PM"}.`);
  const top = Object.entries(t.apps).sort((a, b) => b[1].ms - a[1].ms)[0];
  if (top && top[1].ms > 5 * 60000) out.push(`${label(top[0])} took the most time this week: ${dgFmtDuration(top[1].ms)}.`);
  if (t.blocked) out.push(`FocusGuard AI turned you around ${t.blocked} ${t.blocked === 1 ? "time" : "times"} this week.`);
  return out;
}

function list(items) {
  const ul = document.createElement("ul");
  for (const i of items) { const li = document.createElement("li"); li.textContent = i; ul.appendChild(li); }
  return ul;
}

async function loadCoach() {
  const body = $("coachBody");
  const keys = dgLastDays(14);
  const payload = {};
  for (const k of keys) if (days[k]) payload[k] = days[k];
  const labels = Object.fromEntries(rules.map((r) => [r.id, r.label]));
  const modes = Object.fromEntries(rules.map((x) => [x.id, x.mode]));
  const r = await chrome.runtime.sendMessage({ kind: "dg_coach", days: payload, labels, modes });
  body.replaceChildren();
  if (r && r.ok) {
    // The page headline already states the weekly total; show the coach's own
    // headline only when a local model wrote it.
    if (r.source === "ollama") { const h = document.createElement("p"); h.style.fontWeight = "600"; h.textContent = r.headline; body.appendChild(h); }
    body.appendChild(list(r.insights || []));
    if (r.suggestion) { const tip = document.createElement("p"); tip.className = "tip"; tip.textContent = r.suggestion; body.appendChild(tip); }
    if (r.goal) {
      const g = document.createElement("p"); g.className = "goal";
      const b = document.createElement("b"); b.textContent = "From your goals: ";
      g.append(b, `“${r.goal.text}”`);
      body.appendChild(g);
    }
    if (r.technique) {
      const t = document.createElement("div"); t.className = "technique";
      const h = document.createElement("h3"); h.textContent = `Technique to try: ${r.technique.title}`;
      const how = document.createElement("p"); how.textContent = r.technique.how;
      t.append(h, how);
      if (r.technique.try) { const tr = document.createElement("p"); tr.className = "muted"; tr.textContent = r.technique.try; t.appendChild(tr); }
      const src = document.createElement("p"); src.className = "src"; src.textContent = `From ${r.technique.source}`;
      t.appendChild(src);
      body.appendChild(t);
    }
    const src = document.createElement("p"); src.className = "src";
    src.textContent = r.source === "ollama" ? "Written by the AI model running on your computer." : "Based on your own data, on your computer. Turn on a local AI model in the FocusGuard AI app for more personal wording.";
    body.appendChild(src);
    return;
  }
  const facts = localInsights();
  if (facts.length) body.appendChild(list(facts));
  const p = document.createElement("p"); p.className = "tip";
  p.textContent = "Start the FocusGuard AI app on this computer for weekly coaching: what triggers your scrolling and one change to try next week.";
  body.appendChild(p);
}

function render() {
  const keys = dgLastDays(range);
  const t = totals(keys);
  renderSummary(t);
  renderFigures(t);
  renderChart(keys);
  renderSites(t);
}

document.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => {
  range = Number(b.dataset.range);
  document.querySelectorAll("[data-range]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  render();
}));
load();
