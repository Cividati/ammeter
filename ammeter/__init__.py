"""Ammeter - one meter for the usage, credits and quota limits of your AI accounts.

Reads the logins the CLIs already keep on the machine (Claude Code, Codex) and the API keys Hermes
keeps in ``~/.hermes/.env`` (OpenRouter, DeepSeek). Nothing is uploaded anywhere.
"""

__version__ = "0.3.0"

APP_ID = "dev.cividati.Ammeter"
APP_NAME = "Ammeter"
REFRESH_SECONDS = 120

# Palette shared by both front-ends. Claude coral and OpenAI purple are the simple-icons hexes;
# OpenRouter green and DeepSeek blue are the project's own choices.
COLOURS = {
    "claude": "#d97757",
    "codex": "#412991",
    "openrouter": "#2dbe7f",
    "deepseek": "#4d6bfe",
}

# Severity colours, used by the bars, the badges and the window's status dot.
STATUS = {"ok": "#8ab4f8", "warn": "#f9c74f", "crit": "#ff5555"}

# Titles for the CLI/tk output; the GTK app uses the same words in its group headers.
TITLES = {
    "claude": "CLAUDE",
    "codex": "CODEX",
    "openrouter": "OPENROUTER",
    "deepseek": "DEEPSEEK",
}

# Icons shipped in data/icons, named so the GTK icon theme can find them.
ICON_NAMES = {
    "claude": "ammeter-claude-symbolic",
    "codex": "ammeter-codex-symbolic",
    "openrouter": "ammeter-openrouter-symbolic",
    "deepseek": "ammeter-deepseek-symbolic",
}

# Fallbacks for the tkinter front-end, which cannot load svg. Chosen from glyphs DejaVu has;
# see tests/glyph-check.py.
MARKS = {"claude": "\u2733", "codex": "\u25c6", "openrouter": "\u21c4", "deepseek": "\u25c9"}
