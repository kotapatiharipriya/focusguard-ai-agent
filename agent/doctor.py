"""FocusGuard AI installation and runtime diagnostics.

Run from the project root:
    python3 agent/doctor.py

The command is intentionally stdlib-only and reports actionable checks without
printing the token or reading browser page content.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DRIFTGUARD_DATA", ROOT / "data"))
DB = Path(os.environ.get("DRIFTGUARD_DB", DATA / "driftguard.db"))
TOKEN = Path(os.environ.get("DRIFTGUARD_TOKEN_FILE", DATA / ".collector_token"))
URL = os.environ.get("DRIFTGUARD_URL", "http://127.0.0.1:8765").rstrip("/")


def check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run() -> list[dict]:
    results = [check("python", sys.version_info >= (3, 10), platform.python_version())]
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        probe = DATA / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        results.append(check("data directory", True, str(DATA)))
    except OSError as exc:
        results.append(check("data directory", False, str(exc)))

    results.append(check("extension manifest", (ROOT / "extension" / "manifest.json").exists(), "extension/manifest.json"))
    if TOKEN.exists():
        private = True
        if os.name == "posix":
            private = (TOKEN.stat().st_mode & 0o077) == 0
        results.append(check("token file", private, "present with owner-only permissions" if private else "permissions should be 0600"))
    else:
        results.append(check("token file", True, "will be created on first start"))

    if DB.exists():
        try:
            with sqlite3.connect(DB) as conn:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            results.append(check("database", integrity == "ok", integrity))
        except sqlite3.Error as exc:
            results.append(check("database", False, str(exc)))
    else:
        results.append(check("database", True, "will be created on first start"))

    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=2) as response:
            body = json.loads(response.read())
        runtime = body.get("runtime") or {}
        healthy = bool(body.get("ok")) and (not runtime or runtime.get("healthy", False))
        detail = f"reachable at {URL}"
        if runtime:
            detail += f"; heartbeat age {round(runtime.get('heartbeat_age_ms', 0) / 1000)}s"
        results.append(check("agent", healthy, detail))
    except (OSError, urllib.error.URLError) as exc:
        results.append(check("agent", False, f"not reachable at {URL}: {exc}"))
    return results


def main() -> int:
    results = run()
    failed = 0
    print("FocusGuard AI diagnostics")
    for item in results:
        mark = "PASS" if item["ok"] else "FAIL"
        print(f"[{mark}] {item['name']}: {item['detail']}")
        failed += not item["ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
