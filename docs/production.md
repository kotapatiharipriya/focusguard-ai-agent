# Production personal release

FocusGuard AI is production-ready for one local user after the machine checklist
below. The browser extension and Python agent must run on the same computer.

The autonomous policy is intentionally bounded: it can observe coarse browser
signals, create nudges, start the configured rescue flow, schedule focus, and
tighten a site only for the current day. It cannot read page contents, send
messages, install software, or make external purchases. Every autonomous action
is recorded in `agent_log`, and the model can never bypass the Python safety
checks.

## Install on macOS

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python agent/doctor.py
.venv/bin/python agent/driftguard.py --no-tray
```

Load `extension/` from `chrome://extensions` with Developer mode enabled. After
confirming the extension is connected, install the login item:

```bash
.venv/bin/python agent/autostart.py install
```

Run diagnostics again after restarting the Mac:

```bash
.venv/bin/python agent/doctor.py
```

## Recovery and privacy

- All runtime data is in `data/`. Stop FocusGuard AI before copying the database.
- Back up `data/driftguard.db`, `data/config.json`, and `data/.collector_token`.
- Restoring the database and token together preserves the extension pairing.
- Do not share `.collector_token`; it authorizes local API requests.
- The agent stores coarse site/surface labels and behavioral counters, not page
  URLs, titles, searches, or video identifiers.
- OpenAI is disabled by default. To enable cloud reasoning, install
  `requirements-optional.txt`, export `OPENAI_API_KEY`, and set
  `openai.enabled` to `true` in `data/config.json`. Only the coarse agent
  overview and tool-call context are sent to the selected model; keep it off
  if you want fully local operation.

## Release acceptance checklist

- Diagnostics reports PASS for Python, data directory, manifest, token, database,
  and agent after restart.
- Chrome extension shows Connected.
- A blocked site opens the Focus page.
- A test intervention appears and its response appears in the report.
- A focus session blocks distracting sites and restores them when finished.
- Dashboard, reports, RAG coach, and local backup work.
- Uninstalling the login item does not delete user data.
- `GET /api/health` reports a fresh runtime heartbeat.
- The agent recovers from a failed tick and records the error.
- The autonomy budget prevents repeated autonomous interventions from becoming
  an endless loop.

## Safe autonomy controls

To pause autonomous decisions while continuing to collect data, set this in
`data/config.json`:

```json
{"agent": {"enabled": false}}
```

The dashboard's pause control remains available for a temporary pause. For a
full emergency stop, stop the background process or uninstall the login item;
the database and extension pairing remain intact.
