// FocusGuard AI — content script on tracked sites.
// Measures in-page scrolling behaviour and short-video swipes, then hands
// coarse counts to the background worker. Nothing about *what* you watch is sent.

(() => {
  const FLUSH_MS = 15000; // aggregate scroll activity into 15-second bursts

  let rules = DG_DEFAULT_RULES;
  let lastUrl = location.href;
  let current = dgClassify(lastUrl, rules);
  let shortKey = dgShortKey(lastUrl);
  let shortStartedAt = shortKey ? Date.now() : 0;

  const burst = { scroll_px: 0, wheel_ticks: 0, key_nav: 0, startedAt: 0 };
  let lastScrollY = window.scrollY;

  function send(event) {
    if (!current) return;
    try {
      chrome.runtime.sendMessage({
        kind: "dg_event",
        event: { ...event, app: current.app, surface: current.surface, ts: Date.now() },
      });
    } catch (_) {
      // Extension was reloaded/updated; this page's script is orphaned. Stay silent.
    }
  }

  function markActivity() {
    if (!burst.startedAt) burst.startedAt = Date.now();
  }

  function flushBurst() {
    if (!burst.startedAt) return;
    send({
      type: "scroll_burst",
      scroll_px: Math.round(burst.scroll_px),
      wheel_ticks: burst.wheel_ticks,
      key_nav: burst.key_nav,
      duration_ms: Date.now() - burst.startedAt,
    });
    burst.scroll_px = 0; burst.wheel_ticks = 0; burst.key_nav = 0; burst.startedAt = 0;
  }

  // --- Short-video swipes (YouTube Shorts, Instagram Reels) -----------------
  function onUrlChange(newUrl) {
    const prevApp = current && current.app;
    const next = dgClassify(newUrl, rules);
    const nextShort = dgShortKey(newUrl);

    if (shortKey && nextShort !== shortKey) {
      // Leaving a short: record how long you stayed on it.
      send({ type: "short_viewed", dwell_ms: Date.now() - shortStartedAt });
    }
    if (prevApp !== (next && next.app)) flushBurst(); // don't mix apps in one burst

    current = next;
    shortKey = nextShort;
    shortStartedAt = nextShort ? Date.now() : 0;
    lastUrl = newUrl;
  }

  // YouTube fires this on SPA navigation; the poll below covers every other SPA.
  document.addEventListener("yt-navigate-finish", () => {
    if (location.href !== lastUrl) onUrlChange(location.href);
  });
  setInterval(() => {
    if (location.href !== lastUrl) onUrlChange(location.href);
  }, 1000);

  // --- Scroll signals -------------------------------------------------------
  window.addEventListener("scroll", () => {
    const y = window.scrollY;
    burst.scroll_px += Math.abs(y - lastScrollY);
    lastScrollY = y;
    markActivity();
  }, { passive: true });

  // Shorts/Reels scroll inside containers, so wheel + touch catch what window scroll misses.
  window.addEventListener("wheel", (e) => {
    burst.wheel_ticks += 1;
    if (!window.scrollY) burst.scroll_px += Math.abs(e.deltaY);
    markActivity();
  }, { passive: true, capture: true });

  let touchY = null;
  window.addEventListener("touchstart", (e) => { touchY = e.touches[0] && e.touches[0].clientY; }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    const y = e.touches[0] && e.touches[0].clientY;
    if (touchY != null && y != null) { burst.scroll_px += Math.abs(y - touchY); touchY = y; markActivity(); }
  }, { passive: true });

  const NAV_KEYS = new Set(["ArrowDown", "ArrowUp", "PageDown", "PageUp", " ", "j", "k"]);
  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (NAV_KEYS.has(e.key)) { burst.key_nav += 1; markActivity(); }
  }, { passive: true, capture: true });

  setInterval(flushBurst, FLUSH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) flushBurst();
  });
  window.addEventListener("pagehide", () => {
    if (shortKey) send({ type: "short_viewed", dwell_ms: Date.now() - shortStartedAt });
    flushBurst();
  });

  // --- Redirect: the in-page nudge card ------------------------------------
  // Rendered in a closed shadow root so the site's CSS can't touch it and the
  // site's scripts can't read it.
  let cardHost = null;

  function showNudge(n) {
    if (cardHost) cardHost.remove();
    cardHost = document.createElement("div");
    cardHost.style.cssText = "all:initial;position:fixed;z-index:2147483647;right:20px;bottom:20px;";
    const root = cardHost.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .card { --bg:#F7FAF9; --fg:#10282B; --muted:#4F6663; --line:#D3DEDB; --accent:#1F7A74; --accent-fg:#fff; --buoy:#E8912D;
          box-sizing:border-box; width:min(360px, calc(100vw - 40px)); padding:18px 18px 16px;
          background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:14px;
          border-left:4px solid var(--buoy);
          box-shadow:0 18px 50px rgba(16,40,43,.28); font:14px/1.45 "Avenir Next","Segoe UI Variable","Segoe UI",system-ui,sans-serif;
          animation: in .28s ease-out; }
        @media (prefers-color-scheme: dark) {
          .card { --bg:#132326; --fg:#E4EEEC; --muted:#9DB3AF; --line:#2A3F42; --accent:#5CC2B6; --accent-fg:#0B1C1E; }
        }
        @media (prefers-reduced-motion: reduce) { .card { animation:none; } }
        @keyframes in { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
        .top { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
        .tag { font-size:12.5px; color:var(--muted); font-weight:600; }
        h2 { margin:4px 0 6px; font-size:17px; line-height:1.3; font-weight:650; }
        p { margin:0 0 14px; color:var(--muted); }
        .x { all:unset; cursor:pointer; color:var(--muted); font-size:20px; line-height:1; padding:2px 4px; border-radius:6px; }
        .x:hover, .x:focus-visible { color:var(--fg); outline:2px solid var(--line); }
        .row { display:flex; gap:8px; flex-wrap:wrap; }
        button.b { all:unset; box-sizing:border-box; cursor:pointer; padding:9px 14px; border-radius:9px; font-weight:600; font-size:13.5px; text-align:center; }
        button.b.primary { background:var(--accent); color:var(--accent-fg); flex:1 1 auto; }
        button.b.ghost { border:1px solid var(--line); color:var(--fg); }
        button.b:focus-visible { outline:2px solid var(--fg); outline-offset:2px; }
      </style>
      <div class="card" role="dialog" aria-live="polite" aria-label="FocusGuard AI">
        <div class="top">
          <div class="tag">FocusGuard AI</div>
          <button class="x" data-a="dismiss" aria-label="Dismiss">×</button>
        </div>
        <div class="tag" data-drift hidden></div>
        <div class="tag" data-learning hidden></div>
        <h2></h2>
        <p></p>
        <div class="row">
          <button class="b primary" data-a="rescue"></button>
          <button class="b ghost" data-a="snooze"></button>
        </div>
      </div>`;
    root.querySelector("h2").textContent = n.headline;
    root.querySelector("p").textContent = n.explanation;
    if (Number.isFinite(Number(n.drift_score))) {
      const drift = root.querySelector("[data-drift]");
      drift.hidden = false;
      drift.textContent = `Drift level: ${n.drift_label || "drifting"} · ${n.drift_score}/100`;
    }
    if (n.adaptive_reason) {
      const learned = root.querySelector("[data-learning]");
      learned.hidden = false;
      learned.textContent = `Personalized: ${n.adaptive_reason}`;
    }
    // Cards from the AI agent carry their own action (a planned focus session,
    // or one concrete way out of a loop); the rest offer Focus Rescue.
    const agentCard = Boolean(n.action);
    root.querySelector(".tag").textContent = agentCard ? "FocusGuard AI agent" : "FocusGuard AI";
    root.querySelector('[data-a="rescue"]').textContent = agentCard ? (n.cta || "Start") : `Start Focus Rescue · ${n.rescue_minutes} min`;
    root.querySelector('[data-a="snooze"]').textContent = agentCard || n.level > 1 ? "Not now" : `${n.snooze_minutes} more min`;

    root.addEventListener("click", (e) => {
      const a = e.target && e.target.dataset && e.target.dataset.a;
      if (!a) return;
      try {
        if (a === "rescue") {
          if (agentCard) chrome.runtime.sendMessage({ kind: "dg_agent_accept", ref: n.id, action: n.action, task: n.task, minutes: n.minutes });
          else chrome.runtime.sendMessage({ kind: "dg_rescue_start", minutes: n.rescue_minutes, ref: n.id, why: n.headline });
        } else {
          chrome.runtime.sendMessage({ kind: "dg_nudge_response", ref: n.id, reason: a === "snooze" && n.level < 2 && !agentCard ? "snooze" : "dismiss" });
        }
      } catch (_) { /* orphaned */ }
      cardHost.remove();
      cardHost = null;
    });
    // No auto-focus: a stray Space keypress while scrolling must not start a rescue.
    document.documentElement.appendChild(cardHost);
  }

  try {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (msg && msg.kind === "dg_nudge") {
        showNudge(msg.nudge);
        sendResponse({ shown: true });
      }
    });
  } catch (_) { /* orphaned */ }

  // Pick up a custom site list if you've edited it in Options.
  try {
    chrome.storage.local.get("settings", ({ settings }) => {
      if (settings && Array.isArray(settings.rules)) {
        rules = settings.rules;
        current = dgClassify(location.href, rules);
      }
    });
    chrome.storage.onChanged.addListener((changes) => {
      if (changes.settings && changes.settings.newValue && changes.settings.newValue.rules) {
        rules = changes.settings.newValue.rules;
        current = dgClassify(location.href, rules);
      }
    });
  } catch (_) { /* orphaned script */ }
})();
