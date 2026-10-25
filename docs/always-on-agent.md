# FocusGuard AI as an always-on agent

FocusGuard AI runs in the background on your computer. It doesn't wait for you to open it or ask it anything. You never open a chat window or a dashboard to use it: it notices the loop and steps in by itself.

```
              ┌──────────────── your computer ─────────────────┐
 Chrome       │  agent/driftguard.py  (starts at login)         │
 extension ───┼─▶ collector   store signals in SQLite           │
   ▲          │   engine      rules every 10s → alert           │
   │ nudge    │               outcome tracking (left / stayed)  │
   └──────────┼── reply       pause / rescue state              │
              │   tray icon   status, Rescue, Pause, Quit       │
              │   notify      desktop notification fallback     │
              │   :8765/      status page                       │
              └─────────────────────────────────────────────────┘
```

## What it does on its own

| Stage | What happens |
|---|---|
| **Detect** | The extension reports which app has your attention, Shorts swiped, scroll volume. |
| **Understand** | Every 10 seconds (and right after each batch of events) the deterministic rules in `detector.py` check for the fast-swipe loop, endless feed scrolling, app-hopping and repeated reopening. No LLM in this path. |
| **Redirect** | A card appears in the page you're scrolling: what's happening, in numbers, plus **Start Focus Rescue** or **5 more min**. If the card can't be shown within 20 seconds, you get a desktop notification instead. |
| **Protect** | Focus Rescue sends every tracked site to a calm timer page until it ends — including new tabs and sneaking back via the address bar. It can be started from the card, the tray icon or the status page. |
| **Recover** | At the end, a one-tap check-in (better / same / worse). Nudges you didn't answer are resolved after 3 minutes as *left* or *stayed*. FocusGuard AI stays quiet for 30 minutes after a rescue. |

"5 more min" is honoured once; the next nudge for that loop is firmer and offers only Rescue or Not now. The same kind of nudge for the same app won't repeat within 20 minutes.

## Install once

```bash
pip install pystray pillow            # optional: menu-bar / tray icon
python3 agent/autostart.py install    # starts now and at every login
```

Then load the extension (`chrome://extensions` → Developer mode → Load unpacked → `extension/`). It pairs with the running app automatically. You don't need to copy a token.

- macOS: a LaunchAgent in `~/Library/LaunchAgents`. Restarted automatically if it crashes.
- Windows: a hidden launcher in your Startup folder (uses `pythonw`, so no console window).
- Linux: a systemd user service, or an autostart entry if there's no systemd.

`python3 agent/autostart.py uninstall` removes it. Your data in `data/` is untouched.

## The tray icon

Green: watching. Amber: a nudge is out. Blue: Focus Rescue running. Grey: paused.
The menu has Start/End Focus Rescue, Pause 1 hour / Resume, Open FocusGuard AI (status page), Pair a browser, and Quit.

Without pystray, FocusGuard AI runs the same way, just without the icon. Use the status page at http://127.0.0.1:8765/ instead.

## Tuning

`data/config.json` is created on first run:

```json
{
  "rules": { "shorts_min_minutes": 8, "shorts_min_per_minute": 3.0, "shorts_hard_count": 40,
             "feed_min_minutes": 15, "feed_min_px_per_minute": 2500,
             "hop_window_minutes": 10, "hop_min_switches": 5, "hop_min_apps": 3,
             "reopen_window_minutes": 30, "reopen_min_count": 3, "reopen_max_gap_seconds": 300 },
  "rescue_minutes": 10,
  "cooldown_minutes": 20,
  "snooze_minutes": 5,
  "after_rescue_quiet_minutes": 30,
  "ollama": { "enabled": false, "model": "llama3.2" }
}
```

Restart FocusGuard AI after editing (tray → Quit; it relaunches at next login, or run `python3 agent/driftguard.py`).

With `ollama.enabled`, a local model rephrases the nudge text. The rule still makes the decision. If Ollama is slow or off, the built-in wording is used.

## Pairing and privacy

The first extension that asks the app for its token gets it; after that, pairing is closed until you choose **Pair a browser** in the tray (open for 5 minutes). Web pages can't pair or read anything: any request carrying a website's `Origin` is refused.

Nothing leaves your computer. The app listens only on `127.0.0.1`. Your rescue intentions ("Get water", or what you type) stay in the extension's local storage and are never sent anywhere, not even to the local app.

## Connecting to your existing agent

The engine's alerts table and `/api/status` give your coach and RAG layer the material for explanations after the fact: "You left Shorts 4 of 6 times when nudged this week, mostly after 10pm." The intended split:

- **Real time:** rules → nudge (fast, predictable, offline)
- **Reflection:** coach + RAG over `alerts`, sessions and your goals docs (in the dashboard, not in the moment)
