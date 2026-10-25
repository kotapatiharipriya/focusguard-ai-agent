# Browser event collector

The collector replaces simulated usage events with real ones from Chrome. It covers the **Detect** stage of Detect → Understand → Redirect → Protect → Recover, and nothing else: it measures, it doesn't judge. Detection rules, the coach and Focus Rescue stay in the Python backend.

## Pieces

```
Chrome
 ├─ content.js  (on tracked sites)   scroll bursts every 15s, one event per short swiped
 └─ background.js (service worker)   which app has your attention, session start/end,
        │                            heartbeat, offline queue, batching
        │  POST /api/events   (X-DriftGuard-Token, batches of ≤200, retried until 2xx)
        ▼
agent/collector.py  (127.0.0.1:8765)
 ├─ ingest_batch()    validate → SQLite table browser_events (idempotent by event id)
 └─ build_sessions()  raw events → one row per visit, with the signals the rules need
        ▼
personal_agent.py detectors  (doom-scrolling, app-hopping, repeated reopening)
```

## What gets collected

Every event is `{id, ts, type, app, surface, session_id, …counts}`.

| type | sent when | extra fields |
|---|---|---|
| `session_start` | a tracked app gets your attention (tab switch, navigation, window focus, back from idle) | `reason` |
| `session_end` | attention leaves it | `duration_ms`, `reason` (`switch`, `tab_switch`, `window_focus`, `idle`, `locked`, `paused`…) |
| `heartbeat` | every 60s during a session | `duration_ms` so far |
| `short_viewed` | you leave a Short / Reel | `dwell_ms` on that one |
| `scroll_burst` | every 15s of scrolling activity | `scroll_px`, `wheel_ticks`, `key_nav`, `duration_ms` |
| `pause` | you pause tracking from the popup | `duration_ms` |

`app` is the unit the detectors reason about. YouTube Shorts (`youtube_shorts`) and Instagram Reels (`instagram_reels`) are separate apps from the rest of their sites because their loop is different: hopping from Shorts to the YouTube home feed is a real switch.

**Never collected:** URLs, page titles, video IDs, channel names, search terms, anything you type. The extension turns a URL into `(app, surface)` locally in `sites.js` and drops it. The backend's validator stores only known fields, so even a buggy client can't push a URL into the database. Only one session is open at a time — the active tab in the focused window — so background tabs and a second monitor playing something don't count.

## Session rows (`build_sessions`)

| field | use for |
|---|---|
| `duration_s`, `status` (`closed` / `open` / `stale`) | long-session rules; `stale` = browser died, ended at last heartbeat |
| `shorts_viewed`, `shorts_per_min`, `median_short_dwell_s` | doom-scrolling: fast swiping with short dwell is the loop signature |
| `scroll_px`, `scroll_px_per_min`, `wheel_ticks`, `key_nav` | feed scrolling on Reddit, X, etc. |
| `reopened_after_s` | repeated reopening: seconds since you last left this same app |
| `came_from_app`, `ended_by` | app-hopping chains and how sessions end (for Recover tracking) |

## Setup

1. Start FocusGuard AI: `python3 agent/driftguard.py` (or `python3 agent/autostart.py install` to have it start at every login — see `always-on-agent.md`).
2. Chrome → `chrome://extensions` → Developer mode → **Load unpacked** → choose the `extension/` folder.
3. The extension pairs with the running app by itself. The popup shows "Synced" once it has.

If FocusGuard AI isn't running, events queue in the extension (up to 5,000) and are sent later. `agent/collector.py` can still run on its own as a bare collector; then paste the token it prints into the extension's Settings page.

## Wiring into personal_agent.py

Either run the collector as its own process and have the agent read the same SQLite file, or mount it in-process:

```python
from collector import open_db, build_sessions, serve
import threading, time

server = serve()                          # same DB file the agent uses (DRIFTGUARD_DB)
threading.Thread(target=server.serve_forever, daemon=True).start()

conn = open_db()
sessions = build_sessions(conn, since_ms=int((time.time() - 3600) * 1000))
# feed `sessions` to the existing detectors in place of the simulated events
```

Set `DRIFTGUARD_DB` so both point at your existing database.

## Endpoints

| method | path | auth | |
|---|---|---|---|
| POST | `/api/events` | token | batch ingest → `{accepted, duplicates, rejected}` |
| GET | `/api/health` | optional | `{ok, token_ok}`, plus `events_total` with a valid token |
| GET | `/api/browser/sessions?hours=24` | token | reconstructed sessions |
| GET | `/api/browser/events?limit=100` | token | raw events, newest first |

Requests carrying a web-page `Origin` are refused (403), so a website you visit can't write to or read from your backend even if it guesses the port.

## Adding a site

Add the domain to `DG_DEFAULT_SITES` in `extension/src/sites.js`, add it to both lists in `manifest.json`, reload the extension. Add a branch in `dgClassify` if the site has a distinct short-video surface.

## Tests

```
python3 -m unittest agent/test_collector.py
```

The extension was also checked end to end in Chromium: four Shorts swipes and a wheel scroll produced one `youtube_shorts` session (4 shorts, 2,400 px scrolled, ended by switch) followed by a `youtube` feed session with `came_from_app: youtube_shorts`.
