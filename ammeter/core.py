"""Collect every provider into one list of normalised dicts.

Schema
------
Each provider entry is::

    {
        "key":     "claude",                      # stable id used for colours, icons, lookups
        "name":    "CLAUDE",                      # display name
        "sub":     "pro",                         # plan or account note, may be ""
        "rows":    [row, ...],
        "stale":   True,                          # optional: served from the last-good cache
        "why":     "rate limited (HTTP 429)",     # optional: why it is stale
        "error":   "offline",                     # optional: nothing to show at all
        "reached": True,                          # optional: the plan limit is hit
    }

A row is either a *quota row* (``label``, ``pct`` 0-100, ``reset`` ISO timestamp, ``note``) or a
*balance row* (``label``, ``pct`` None, ``note``). Both are rendered by every front-end, which is why
the notes are built once, here, instead of in each of them.
"""
from datetime import datetime, timezone

from . import TITLES, cache, filters
from .formatting import absolute_reset, error_text, parse_dt
from .providers import PROVIDERS

_last_good = {}


def collect():
    """Fetch every provider and never raise: a failure degrades to cached data or an error row."""
    remembered = cache.load()
    collected = []
    for key, fetch in PROVIDERS:
        try:
            entry = fetch()
            _last_good[key] = entry
            remembered[key] = entry
        except Exception as failure:
            previous = _last_good.get(key) or remembered.get(key)
            if previous:
                entry = dict(previous, stale=True, why=error_text(failure))
            else:
                entry = {"name": TITLES.get(key, key.upper()), "sub": "", "rows": [],
                         "error": error_text(failure)}
        entry["key"] = key
        drop_expired_windows(entry)
        collected.append(entry)
    cache.save(remembered)
    return collected


def drop_expired_windows(entry):
    """A quota window that already rolled over makes its stored percentage meaningless."""
    now = datetime.now(timezone.utc)
    for row in entry.get("rows") or []:
        if row.get("pct") is None:
            continue
        rolled = parse_dt(row.get("reset"))
        if rolled and rolled <= now:
            row["pct"] = None
            row["note"] = f"window reset {absolute_reset(row['reset'])} \u2014 awaiting fresh numbers"


def live_providers(data):
    """How many providers have something to show (used in the header and the CLI footer)."""
    return sum(1 for entry in data if entry.get("rows"))


def problems(data):
    """(provider name, reason) for everything the user should be told about, most serious first."""
    found = []
    for entry in data:
        if entry.get("error"):
            found.append((entry["name"], entry["error"]))
        elif entry.get("stale"):
            found.append((entry["name"], f"{entry['why']} \u2014 cached numbers"))
        elif entry.get("reached"):
            found.append((entry["name"], "plan limit reached"))
    return found


# failures that only mean "this provider was never set up on this machine"
NOT_CONFIGURED = ("FileNotFoundError", "no oauth token", "no tokens in", "no OPENROUTER_API_KEY",
                  "no DEEPSEEK_API_KEY")


def configured(entry):
    """False for an entry that has nothing to show only because it has no login or API key."""
    error = entry.get("error") or ""
    return bool(entry.get("rows")) or not any(text in error for text in NOT_CONFIGURED)


def visible(data, hidden=None):
    """What every front-end shows: providers that are set up and not hidden by the user."""
    hidden = filters.load() if hidden is None else hidden
    return [entry for entry in data if entry.get("key") not in hidden and configured(entry)]
