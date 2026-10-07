"""Token Monitor - GitHub Copilot spend vs. monthly budget (GitHub quota or a budget portal).

Forked from Cividati/ammeter. The data source is pluggable: ``mock`` (default) or a user-supplied
``curl`` command; see docs/WIRING.md. Nothing is uploaded anywhere.
"""

__version__ = "0.1.0"

APP_ID = "dev.cividati.TokenMonitor"
APP_NAME = "Token Monitor"
REFRESH_SECONDS = 120

# Palette shared by both front-ends.
COLOURS = {
    "copilot": "#6e40c9",
}

# Severity colours, used by the bars, the badges and the window's status dot.
STATUS = {"ok": "#8ab4f8", "warn": "#f9c74f", "crit": "#ff5555"}

# Titles for the CLI/tk output; the GTK app uses the same words in its group headers.
TITLES = {
    "copilot": "COPILOT",
}

# Icons shipped in data/icons, named so the GTK icon theme can find them.
ICON_NAMES = {
    "copilot": "token-monitor-copilot-symbolic",
}

# Fallbacks for the tkinter front-end, which cannot load svg (glyph DejaVu has).
MARKS = {"copilot": "\u25c9"}
