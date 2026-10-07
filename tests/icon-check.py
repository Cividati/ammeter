#!/usr/bin/python3
"""Check the shipped svgs: well-formed XML, and resolvable by the icon theme when a display exists.

Run it under a display (or xvfb-run): the GTK icon theme cannot be queried without one.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ICONS = HERE / "data" / "icons"
NEEDED = ["token-monitor-copilot-symbolic", "dev.cividati.TokenMonitor"]

fails = 0
for f in sorted(ICONS.glob("*.svg")):
    try:
        ET.parse(f)
        print(f"xml ok    {f.name}")
    except Exception as e:
        fails += 1
        print(f"XML BROKEN {f.name}: {e}")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

Gtk.init()
display = Gdk.Display.get_default()
if display is None:
    print("no display: skipping icon-theme resolution")
else:
    theme = Gtk.IconTheme.get_for_display(display)
    theme.add_search_path(str(ICONS))
    for name in NEEDED:
        found = theme.has_icon(name)
        if not found:
            fails += 1
        print(f"{'found  ' if found else 'MISSING'}  {name}")

print("icon-check ok" if fails == 0 else f"{fails} FAILURES")
sys.exit(1 if fails else 0)
