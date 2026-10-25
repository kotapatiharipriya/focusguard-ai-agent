"""Menu-bar / system-tray icon for FocusGuard AI (optional: pip install pystray pillow).

The icon colour tells you the state at a glance:
  green = watching · amber = a nudge is out · blue = Focus Rescue · grey = paused
"""

from __future__ import annotations

import sys
import threading
import time
import webbrowser

import pystray  # noqa: F401  (ImportError here makes driftguard.py run without a tray)
from PIL import Image, ImageDraw

import notify

COLORS = {"watch": (47, 125, 79), "nudge": (196, 120, 40), "rescue": (52, 101, 164), "paused": (130, 126, 120)}


def _icon_image(state: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = COLORS[state]
    d.ellipse((4, 4, 60, 60), fill=c + (255,))
    d.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 235))  # a calm "eye"
    d.ellipse((27, 27, 37, 37), fill=c + (255,))
    return img


def run(engine, url: str):
    def state() -> str:
        ctl = engine.control()
        if ctl["rescue_until"]:
            return "rescue"
        if ctl["paused_until"]:
            return "paused"
        st = engine.status()
        if st["alerts"] and st["alerts"][0]["outcome"] is None:
            return "nudge"
        return "watch"

    def label(_item=None) -> str:
        ctl = engine.control()
        if ctl["rescue_until"]:
            return f"Focus Rescue · {max(1, round((ctl['rescue_until'] - time.time() * 1000) / 60000))} min left"
        if ctl["paused_until"]:
            return "Paused"
        t = engine.status()["today"]
        return f"Watching · {t['shorts']} shorts, {t['nudges']} nudges today"

    def refresh():
        icon.icon = _icon_image(state())
        icon.title = "FocusGuard AI — " + label()
        icon.update_menu()

    menu = pystray.Menu(
        pystray.MenuItem(label, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda _: f"Start Focus Rescue ({engine.cfg['rescue_minutes']} min)",
                         lambda: engine.start_rescue(),
                         visible=lambda _: not engine.control()["rescue_until"]),
        pystray.MenuItem("End Focus Rescue", lambda: engine.end_rescue("ended_early"),
                         visible=lambda _: bool(engine.control()["rescue_until"])),
        pystray.MenuItem("Pause for 1 hour", lambda: engine.pause(60),
                         visible=lambda _: not engine.control()["paused_until"]),
        pystray.MenuItem("Resume watching", lambda: engine.pause(0),
                         visible=lambda _: bool(engine.control()["paused_until"])),
        pystray.MenuItem("Open dashboard", lambda: threading.Thread(target=engine.open_dashboard, daemon=True).start(), default=True),
        pystray.MenuItem("Open status page", lambda: webbrowser.open(url)),
        pystray.MenuItem("Pair a browser (5 min)", lambda: engine.open_pairing(5)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda: (engine.stop(), icon.stop())),
    )
    icon = pystray.Icon("FocusGuard AI", _icon_image("watch"), "FocusGuard AI", menu)
    engine.listeners.append(refresh)
    if not sys.platform == "darwin":  # macOS: osascript notifications look more native
        notify.register_tray(lambda title, msg: icon.notify(msg, title))

    def setup(ic):
        ic.visible = True
        while ic.visible and not engine._stop.is_set():
            try:
                refresh()
            except Exception:
                pass
            time.sleep(15)

    icon.run(setup=setup)
