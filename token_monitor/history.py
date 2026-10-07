"""Append-only history of portal snapshots, kept in ``.token-monitor/history.jsonl`` (gitignored).

One JSON line per successful portal fetch: ``ts`` (UTC ISO), ``spent``, ``budget``, ``currency``,
``period_end`` (ISO or null), and for the github source ``basis`` (which API field ``spent`` was derived
from: ``remaining``, ``quota_remaining`` or ``credits_used``) and ``reported`` (the raw ``credits_used``, for diagnosis). It is what the offline fallback serves when the portal is unreachable,
and what the local-cost calibration is computed from. Override the directory with
``TOKEN_MONITOR_HISTORY_DIR``. Nothing secret is stored: only the four numbers above.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .settings import REPO, setting


def history_dir():
    """TOKEN_MONITOR_HISTORY_DIR, else ./.token-monitor in a writable checkout, else ~/.local/share/token-monitor
    (an installed package such as the .deb lives in a read-only /usr/lib)."""
    configured = setting("TOKEN_MONITOR_HISTORY_DIR")
    if configured:
        return Path(configured)
    local = REPO / ".token-monitor"
    if local.is_dir() or os.access(REPO, os.W_OK):
        return local
    return Path.home() / ".local" / "share" / "token-monitor"


def history_file():
    return history_dir() / "history.jsonl"


def load():
    """Every readable snapshot, oldest first. Missing file or corrupt lines are skipped silently."""
    found = []
    try:
        lines = history_file().read_text().splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            snap = json.loads(line)
            snap["spent"], snap["budget"] = float(snap["spent"]), float(snap["budget"])
            datetime.fromisoformat(snap["ts"])
            snap.setdefault("currency", "EUR")
            snap.setdefault("period_end", None)
            snap.setdefault("basis", None)
        except (ValueError, KeyError, TypeError):
            continue
        found.append(snap)
    return found


def same(a, b):
    """Near-identical: same budget, currency and period, spend within a cent."""
    return (abs(a["spent"] - b["spent"]) < 0.01 and abs(a["budget"] - b["budget"]) < 0.01
            and a["currency"] == b["currency"] and a["period_end"] == b["period_end"]
            and a.get("basis") == b.get("basis"))


def append(snapshot, now=None):
    """Record a snapshot unless it repeats the previous one. Returns True when a line was written."""
    now = now or datetime.now(timezone.utc)
    entry = {"ts": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
             "spent": round(float(snapshot["spent"]), 2), "budget": round(float(snapshot["budget"]), 2),
             "currency": snapshot.get("currency") or "EUR", "period_end": snapshot.get("period_end")}
    for extra in ("basis", "reported"):
        if snapshot.get(extra) is not None:
            entry[extra] = snapshot[extra]
    previous = load()
    if previous and same(previous[-1], entry):
        return False
    try:
        path = history_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        return False        # a read-only checkout must not break the monitor
    return True


def latest():
    snaps = load()
    return snaps[-1] if snaps else None
