"""Last-good cache on disk.

The source may rate-limit or the SSO cookie may expire, and the CLI, the GNOME extension and the
GTK app are separate processes, so an in-process cache is not enough: a fresh process has to be able
to show the previous numbers instead of an error.
"""
import json
from pathlib import Path

CACHE_FILE = Path.home() / ".cache" / "token-monitor" / "last-good.json"


def load():
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def save(cache):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache))
    except Exception:
        pass
