// "Get to know you": eight quick questions, one per screen. Every answer
// changes something real: which sites are blocked, what you see instead,
// your tasks, when you're protected, and what the coach talks about.

const $ = (id) => document.getElementById(id);
let a = { name: "", blockSites: ["youtube_shorts", "instagram_reels", "tiktok"], otherSites: [], times: [], triggers: [],
          mainGoal: "", tasks: [], instead: [], allowPasses: true, focusMin: 25,
          protect: { enabled: false, start: "22:00", end: "01:00" } };
let step = 0;
let settings = null;

const SITE_OPTIONS = () => (settings.rules || DG_DEFAULT_RULES).filter((r) => r.builtin).map((r) => ({ id: r.id, label: r.label }));

function choiceGroup(options, selected, { multi = true, wide = false } = {}) {
  const box = document.createElement("div");
  box.className = "choices" + (wide ? " one" : "");
  box.setAttribute("role", "group");
  for (const o of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "choice" + (multi ? "" : " radio");
    b.dataset.id = o.id;
    b.setAttribute("aria-pressed", String(multi ? selected.includes(o.id) : String(selected) === String(o.id)));
    const tick = document.createElement("span"); tick.className = "tick";
    const txt = document.createElement("span");
    const bl = document.createElement("b"); bl.textContent = o.label;
    txt.appendChild(bl);
    if (o.text) { const t = document.createElement("span"); t.className = "t"; t.textContent = o.text; txt.appendChild(t); }
    b.append(tick, txt);
    box.appendChild(b);
  }
  return box;
}

function bindChoices(box, key, multi = true, parse = (v) => v) {
  box.addEventListener("click", (e) => {
    const b = e.target.closest(".choice");
    if (!b) return;
    if (multi) {
      const set = new Set(a[key]);
      set.has(b.dataset.id) ? set.delete(b.dataset.id) : set.add(b.dataset.id);
      a[key] = [...set];
      b.setAttribute("aria-pressed", String(set.has(b.dataset.id)));
    } else {
      a[key] = parse(b.dataset.id);
      box.querySelectorAll(".choice").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    }
  });
}

function field(labelText, value, placeholder, onInput, cls = "") {
  const wrap = document.createElement("div"); wrap.className = "field";
  const id = "f" + Math.random().toString(36).slice(2);
  const l = document.createElement("label"); l.htmlFor = id; l.textContent = labelText;
  const i = document.createElement("input"); i.id = id; i.className = "input " + cls; i.value = value || ""; i.placeholder = placeholder;
  i.maxLength = 140; i.autocomplete = "off";
  i.addEventListener("input", () => onInput(i.value));
  i.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("next").click(); } });
  wrap.append(l, i);
  return wrap;
}

// A small "type and press Enter" list: websites, tasks, your own ideas.
function listInput(labelText, key, placeholder, { max = 8, normalize = (v) => v.trim(), error = "" } = {}) {
  const wrap = document.createElement("div"); wrap.className = "field";
  const id = "l" + Math.random().toString(36).slice(2);
  const l = document.createElement("label"); l.htmlFor = id; l.textContent = labelText;
  const row = document.createElement("div"); row.className = "list-row";
  const i = document.createElement("input"); i.id = id; i.className = "input"; i.placeholder = placeholder; i.maxLength = 140; i.autocomplete = "off";
  const add = document.createElement("button"); add.type = "button"; add.className = "btn btn-quiet"; add.textContent = "Add";
  row.append(i, add);
  const chips = document.createElement("ul"); chips.className = "chips-list";
  const err = document.createElement("p"); err.className = "err"; err.hidden = true;
  const draw = () => {
    chips.replaceChildren(...a[key].map((v, idx) => {
      const li = document.createElement("li");
      const span = document.createElement("span"); span.textContent = v;
      const x = document.createElement("button"); x.type = "button"; x.textContent = "×"; x.setAttribute("aria-label", `Remove ${v}`);
      x.addEventListener("click", () => { a[key].splice(idx, 1); draw(); });
      li.append(span, x);
      return li;
    }));
  };
  const commit = () => {
    const v = normalize(i.value);
    if (!i.value.trim()) return false;
    if (!v) { err.textContent = error; err.hidden = false; return true; }
    err.hidden = true;
    if (!a[key].includes(v) && a[key].length < max) a[key].push(v);
    i.value = "";
    draw();
    return true;
  };
  add.addEventListener("click", () => { commit(); i.focus(); });
  i.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); if (!commit()) $("next").click(); }
  });
  wrap.append(l, row, err, chips);
  wrap.commit = commit;
  draw();
  return wrap;
}

let pendingInputs = [];   // list inputs on the current screen; typed-but-not-added text is kept on Continue

const STEPS = [
  {
    q: "Hi. What should FocusGuard AI call you?",
    help: "Just a first name. It stays on this computer.",
    render(body) {
      const f = field("Your name", a.name, "e.g. Priya", (v) => { a.name = v.trim(); }, "big-input");
      f.querySelector("label").className = "sr-only";
      body.appendChild(f);
      setTimeout(() => f.querySelector("input").focus(), 50);
    },
  },
  {
    q: () => `${a.name ? a.name + ", which" : "Which"} sites do you want to block?`,
    help: "When you open one of these, FocusGuard AI shows you your own plan instead. Short videos are picked to start; change anything.",
    render(body) {
      const box = choiceGroup(SITE_OPTIONS(), a.blockSites);
      bindChoices(box, "blockSites");
      body.appendChild(box);
      const li = listInput("Add any other website", "otherSites", "e.g. netflix.com, then press Enter",
        { normalize: (v) => dgNormalizeHost(v), error: "That doesn't look like a website. Try something like netflix.com." });
      body.appendChild(li);
      pendingInputs.push(li);
    },
  },
  {
    q: "When do you usually lose time?",
    help: "FocusGuard AI can protect these moments for you.",
    render(body) {
      const box = choiceGroup(DG_TIMES, a.times);
      bindChoices(box, "times");
      body.appendChild(box);
    },
  },
  {
    q: "What usually pulls you in?",
    help: "Be honest. It helps the coach suggest things that actually fit you.",
    render(body) {
      const box = choiceGroup(DG_TRIGGERS, a.triggers);
      bindChoices(box, "triggers");
      body.appendChild(box);
    },
  },
  {
    q: "What do you want to get done?",
    help: "Your goal and tasks appear every time you open a blocked site, so there's always something better one click away.",
    render(body) {
      body.appendChild(field("Your main goal right now", a.mainGoal, "e.g. Get an internship offer by March", (v) => { a.mainGoal = v.trim(); }));
      const li = listInput("Tasks for this week (up to 5)", "tasks", "e.g. Update my resume, then press Enter", { max: 5 });
      body.appendChild(li);
      pendingInputs.push(li);
    },
  },
  {
    q: "When you feel the urge to scroll, what could you do instead?",
    help: "Pick a few quick things. FocusGuard AI offers them the moment you open a blocked site.",
    render(body) {
      const box = choiceGroup(DG_INSTEAD, a.instead.filter((x) => DG_INSTEAD.some((d) => d.id === x)));
      box.addEventListener("click", (e) => {
        const b = e.target.closest(".choice");
        if (!b) return;
        const set = new Set(a.instead);
        set.has(b.dataset.id) ? set.delete(b.dataset.id) : set.add(b.dataset.id);
        a.instead = [...set];
        b.setAttribute("aria-pressed", String(set.has(b.dataset.id)));
      });
      body.appendChild(box);
      // Your own ideas live in the same list as free text.
      const custom = { list: a.instead.filter((x) => !DG_INSTEAD.some((d) => d.id === x)) };
      a._customInstead = custom.list;
      const li = listInput("Your own idea", "_customInstead", "e.g. Do 10 push-ups, then press Enter", { max: 4 });
      body.appendChild(li);
      pendingInputs.push(li);
    },
    beforeNext() {
      const known = a.instead.filter((x) => DG_INSTEAD.some((d) => d.id === x));
      a.instead = [...known, ...(a._customInstead || [])];
    },
  },
  {
    q: "If you really need a blocked site, should FocusGuard AI let you in?",
    help: "For example to check one message. You'd type why, and it opens for 5 minutes.",
    render(body) {
      const opts = [
        { id: "yes", label: "Yes, but only a few times a day", text: "Up to 3 times, 5 minutes each. Good for most people." },
        { id: "no", label: "No, keep them blocked", text: "No exceptions. Change it later in Settings if you need to." },
      ];
      const box = choiceGroup(opts, a.allowPasses ? "yes" : "no", { multi: false, wide: true });
      bindChoices(box, "allowPasses", false, (v) => v === "yes");
      body.appendChild(box);
    },
  },
  {
    q: "How long can you focus before you need a break?",
    help: "This sets your focus timer. Shorter is fine; starting matters more.",
    render(body) {
      const opts = [15, 25, 45, 60].map((m) => ({ id: String(m), label: `${m} minutes`,
        text: { 15: "Easing in", 25: "The classic", 45: "Deep work", 60: "Long stretch" }[m] }));
      const box = choiceGroup(opts, a.focusMin, { multi: false });
      bindChoices(box, "focusMin", false, Number);
      body.appendChild(box);
    },
  },
  {
    q: () => (a.name ? `Here's your plan, ${a.name}` : "Here's your plan"),
    help: "Based on your answers. You can change any of it later in Settings.",
    render(body) {
      const win = dgProtectWindow(a.times);
      if (win && !a._protectTouched) a.protect = { enabled: true, ...win };
      const draw = () => {
        body.replaceChildren();
        const { changes } = dgApplyProfile(settings, a);
        const ul = document.createElement("ul"); ul.className = "plan";
        const lines = [...changes];
        if (a.mainGoal) lines.splice(1, 0, `Your goal on every blocked screen: “${a.mainGoal}”.`);
        for (const c of lines) { const li = document.createElement("li"); li.textContent = c; ul.appendChild(li); }
        body.appendChild(ul);

        const box = document.createElement("div"); box.className = "protect";
        const row = document.createElement("div"); row.className = "row";
        const txt = document.createElement("div");
        const h = document.createElement("b"); h.textContent = "Protect my risky hours";
        const p = document.createElement("p");
        p.textContent = "During this time every tracked site is closed, not just the blocked ones.";
        txt.append(h, p);
        const sw = document.createElement("label"); sw.className = "switch";
        const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = a.protect.enabled;
        cb.setAttribute("aria-label", "Protect my risky hours");
        sw.append(cb, document.createElement("span"));
        row.append(txt, sw);
        box.appendChild(row);
        const times = document.createElement("div"); times.className = "times"; times.hidden = !a.protect.enabled;
        const from = document.createElement("input"); from.type = "time"; from.className = "input"; from.value = a.protect.start; from.setAttribute("aria-label", "From");
        const to = document.createElement("input"); to.type = "time"; to.className = "input"; to.value = a.protect.end; to.setAttribute("aria-label", "Until");
        times.append("From", from, "until", to);
        box.appendChild(times);
        cb.addEventListener("change", () => { a._protectTouched = true; a.protect.enabled = cb.checked; draw(); });
        from.addEventListener("change", () => { a._protectTouched = true; a.protect.start = from.value; draw(); });
        to.addEventListener("change", () => { a._protectTouched = true; a.protect.end = to.value; draw(); });
        body.appendChild(box);
      };
      draw();
    },
    next: "Looks good, start FocusGuard AI",
  },
];

function render() {
  const s = STEPS[step];
  pendingInputs = [];
  $("bar").style.width = `${((step + 1) / STEPS.length) * 100}%`;
  $("count").textContent = step < STEPS.length - 1 ? `Question ${step + 1} of ${STEPS.length - 1}` : "All set";
  $("q").textContent = typeof s.q === "function" ? s.q() : s.q;
  $("help").textContent = s.help;
  const body = $("body");
  body.replaceChildren();
  s.render(body);
  $("back").style.visibility = step === 0 ? "hidden" : "visible";
  $("skip").hidden = step === STEPS.length - 1;
  $("next").textContent = s.next || "Continue";
  $("q").focus({ preventScroll: true });
}

function leaveStep() {
  for (const li of pendingInputs) li.commit();   // keep anything typed but not yet added
  const s = STEPS[step];
  if (s.beforeNext) s.beforeNext();
}

async function finish() {
  $("next").disabled = true;
  const profile = { ...a, completedAt: Date.now() };
  delete profile._protectTouched;
  delete profile._customInstead;
  const { settings: next } = dgApplyProfile(settings, profile);
  await chrome.storage.local.set({ profile, settings: next });
  // Your goal and tasks become your task list, so blocked sites point at something real.
  const { tasks = [] } = await chrome.storage.local.get("tasks");
  const titles = [...(profile.mainGoal ? [profile.mainGoal] : []), ...(profile.tasks || [])];
  for (const title of titles.reverse()) {
    if (!tasks.some((t) => t.title === title && !t.done)) {
      tasks.unshift({ id: crypto.randomUUID(), title, done: false, createdAt: Date.now(), doneAt: null, pomodoros: 0, focusMs: 0 });
    }
  }
  await chrome.storage.local.set({ tasks });
  await chrome.runtime.sendMessage({ kind: "dg_rules_changed" });
  location.href = "focus.html?welcome=1";
}

$("next").addEventListener("click", () => {
  leaveStep();
  if (step < STEPS.length - 1) { step++; render(); } else finish();
});
$("skip").addEventListener("click", () => { leaveStep(); step++; render(); });
$("back").addEventListener("click", () => { leaveStep(); if (step > 0) { step--; render(); } });

(async () => {
  ({ settings } = await chrome.storage.local.get("settings"));
  settings = settings || {};
  settings.rules = Array.isArray(settings.rules) && settings.rules.length ? settings.rules : DG_DEFAULT_RULES;
  settings.focus = { ...DG_DEFAULT_FOCUS, ...(settings.focus || {}) };
  const { profile } = await chrome.storage.local.get("profile");
  if (profile) {
    // Retaking: start from your previous answers (older profiles used "sites").
    a = { ...a, ...profile, _protectTouched: true };
    if (!profile.blockSites && profile.sites) a.blockSites = profile.sites;
    a.tasks = []; // tasks already live in your list; only new ones are added
  }
  render();
})();
