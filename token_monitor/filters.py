"""Which providers and models the user chose to hide, kept in ~/.config/token-monitor/hidden.json.

The file looks like ``{"providers": ["copilot"], "models": ["github-copilot/gpt-5"]}``.
A missing or damaged file just means nothing is hidden. Stdlib only.
"""
import json
import os

from . import settings

KINDS = ("providers", "models")


def _path():
    return settings.CONFIG_DIR / "hidden.json"      # looked up late so the selftest can redirect it


def load():
    """{"providers": set, "models": set}; never raises."""
    found = {kind: set() for kind in KINDS}
    try:
        raw = json.loads(_path().read_text())
        for kind in KINDS:
            values = raw.get(kind) if isinstance(raw, dict) else None
            if isinstance(values, list):
                found[kind] = {v for v in values if isinstance(v, str)}
    except (OSError, ValueError):
        pass
    return found


def is_hidden(kind, ident, hidden=None):
    return ident in (hidden or load()).get(kind, ())


def set_hidden(kind, ident, hidden):
    """Hide (True) or show (False) one id and write the file atomically."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind '{kind}'")
    state = load()
    (state[kind].add if hidden else state[kind].discard)(ident)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps({k: sorted(state[k]) for k in KINDS}, indent=2))
    os.replace(temp, path)
