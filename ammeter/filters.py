"""Which providers the user chose to hide, kept in ~/.config/ammeter/hidden.json.

The file looks like ``{"providers": ["deepseek"]}``. A missing or damaged file just means nothing
is hidden. Stdlib only.
"""
import json
import os
from pathlib import Path

HIDDEN_FILE = Path.home() / ".config" / "ammeter" / "hidden.json"


def load():
    """The set of hidden provider keys; never raises."""
    try:
        values = json.loads(HIDDEN_FILE.read_text()).get("providers")
        return {v for v in values if isinstance(v, str)}
    except Exception:
        return set()


def is_hidden(key, hidden=None):
    return key in (load() if hidden is None else hidden)


def set_hidden(key, hidden):
    """Hide (True) or show (False) one provider and write the file atomically."""
    state = load()
    (state.add if hidden else state.discard)(key)
    HIDDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = HIDDEN_FILE.with_name(HIDDEN_FILE.name + ".tmp")
    temp.write_text(json.dumps({"providers": sorted(state)}, indent=2))
    os.replace(temp, HIDDEN_FILE)
