"""Start FocusGuard AI automatically when you log in.

    python3 agent/autostart.py install     # start now and at every login
    python3 agent/autostart.py uninstall
    python3 agent/autostart.py status

macOS: LaunchAgent (~/Library/LaunchAgents) · Windows: Startup folder ·
Linux: systemd user service, or an XDG autostart entry if systemd isn't there.
No admin rights needed anywhere; everything runs as you.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parent / "driftguard.py"
ROOT = AGENT.parent.parent
LOG = ROOT / "data" / "driftguard.log"
LABEL = "com.driftguard.agent"


def _python() -> str:
    exe = sys.executable
    if sys.platform.startswith("win"):
        w = Path(exe).with_name("pythonw.exe")  # no console window
        if w.exists():
            return str(w)
    return exe


# -- macOS ------------------------------------------------------------------
def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _mac_install():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{_python()}</string><string>{AGENT}</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>{LOG}</string>
  <key>StandardErrorPath</key><string>{LOG}</string>
</dict></plist>
"""
    p = _mac_plist()
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    p.write_text(plist)
    subprocess.run(["launchctl", "load", str(p)], check=True)
    return p


def _mac_uninstall():
    p = _mac_plist()
    if p.exists():
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        p.unlink()


# -- Windows ----------------------------------------------------------------
def _win_shortcut() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "FocusGuard AI.vbs"


def _win_install():
    p = _win_shortcut()
    # A .vbs launcher starts pythonw with no flashing console window.
    p.write_text(
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{ROOT}"\r\n'
        f'sh.Run """{_python()}"" ""{AGENT}""", 0, False\r\n'
    )
    subprocess.Popen(["wscript", str(p)])
    return p


def _win_uninstall():
    p = _win_shortcut()
    if p.exists():
        p.unlink()


# -- Linux ------------------------------------------------------------------
def _linux_unit() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "driftguard.service"


def _linux_desktop() -> Path:
    return Path.home() / ".config" / "autostart" / "driftguard.desktop"


def _linux_install():
    if shutil.which("systemctl") and subprocess.run(["systemctl", "--user", "is-system-running"],
                                                    capture_output=True).returncode in (0, 1):
        p = _linux_unit()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"""[Unit]
Description=FocusGuard AI anti-doom-scrolling agent
After=graphical-session.target

[Service]
ExecStart={_python()} {AGENT}
WorkingDirectory={ROOT}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
""")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "driftguard.service"], check=True)
        return p
    p = _linux_desktop()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"""[Desktop Entry]
Type=Application
Name=FocusGuard AI
Exec={_python()} {AGENT}
Path={ROOT}
X-GNOME-Autostart-enabled=true
""")
    subprocess.Popen([_python(), str(AGENT)], cwd=ROOT, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


def _linux_uninstall():
    if _linux_unit().exists():
        subprocess.run(["systemctl", "--user", "disable", "--now", "driftguard.service"], capture_output=True)
        _linux_unit().unlink()
    if _linux_desktop().exists():
        _linux_desktop().unlink()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if sys.platform == "darwin":
        install, uninstall, where = _mac_install, _mac_uninstall, _mac_plist()
    elif sys.platform.startswith("win"):
        install, uninstall, where = _win_install, _win_uninstall, _win_shortcut()
    else:
        install, uninstall = _linux_install, _linux_uninstall
        where = _linux_unit() if _linux_unit().exists() else _linux_desktop()

    if cmd == "install":
        p = install()
        print(f"FocusGuard AI will now start at login ({p}).")
        print("It's starting now too — look for the icon, or open http://127.0.0.1:8765/")
    elif cmd == "uninstall":
        uninstall()
        print("FocusGuard AI won't start at login anymore. Your data in data/ is untouched.")
    else:
        print("installed" if where.exists() else "not installed", f"({where})")


if __name__ == "__main__":
    main()
