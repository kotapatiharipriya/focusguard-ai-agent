"""Native desktop notifications with no required dependencies.

macOS: osascript · Linux: notify-send · Windows: PowerShell balloon tip.
If the tray icon is running, it registers itself here and is used instead,
so notifications come from FocusGuard AI's own icon.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

_tray_notify = None  # set by tray.py when the tray icon is up


def register_tray(fn) -> None:
    global _tray_notify
    _tray_notify = fn


def _esc_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _esc_ps(s: str) -> str:
    return s.replace("'", "''")


def notify(title: str, message: str) -> bool:
    """Show a notification. Returns False if no method worked; never raises."""
    try:
        if _tray_notify:
            _tray_notify(title, message)
            return True
        if sys.platform == "darwin":
            script = f'display notification "{_esc_applescript(message)}" with title "{_esc_applescript(title)}"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
            return True
        if sys.platform.startswith("win"):
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$n = New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon = [System.Drawing.SystemIcons]::Information;"
                f"$n.BalloonTipTitle = '{_esc_ps(title)}';"
                f"$n.BalloonTipText = '{_esc_ps(message)}';"
                "$n.Visible = $true; $n.ShowBalloonTip(8000); Start-Sleep -Seconds 9; $n.Dispose()"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", "FocusGuard AI", title, message], check=False, timeout=5)
            return True
    except Exception:  # a notification must never take the agent down
        pass
    return False


if __name__ == "__main__":
    print("sent" if notify("FocusGuard AI", "Notifications are working.") else "no notification method found")
