"""Collect every provider into one list of normalised dicts.

Schema
------
Each provider entry is::

    {
        "key":     "copilot",                     # stable id used for colours, icons, lookups
        "name":    "COPILOT",                     # display name
        "sub":     "pro",                         # plan or account note, may be ""
        "rows":    [row, ...],
        "stale":   True,                          # optional: served from the last-good cache
        "why":     "rate limited (HTTP 429)",     # optional: why it is stale
        "error":   "offline",                     # optional: nothing to show at all
        "reached": True,                          # optional: the plan limit is hit
        "summary": {...},                         # optional (copilot): budget, spent, burn, outlook,
                                                  #   out_date, estimate, calibration - feeds the plots
        "hourly":  [{...}, ...],                  # optional (copilot): last 72 clock hours, same row shape
                                                  #   plus "hour"; local estimate only (see usage.hourly)
        "daily":   [{...}, ...],                  # optional (copilot): per-day usage; cost = cost_true (portal
                                                  #   deltas) + cost_est (local estimate), see usage.py
    }

A row is either a *quota row* (``label``, ``pct`` 0-100, ``reset`` ISO timestamp, ``note``) or a
*balance row* (``label``, ``pct`` None, ``note``, and optionally ``level`` ``"crit"``/``"warn"``). Both are rendered by every front-end, which is why
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


# errors that only mean "this provider was never set up" - nothing to show, nothing to warn about
NOT_CONFIGURED = ("gh not found", "gh is not logged in", "curl source: set", "no mock data")


def configured(entry):
    """False for an entry that failed only because it has no credentials or source and has no data."""
    error = entry.get("error")
    return not (error and not entry.get("rows") and not entry.get("daily")
                and any(text in error for text in NOT_CONFIGURED))


def visible(data, hidden=None):
    """What every front-end should show: configured providers the user has not hidden,
    with hidden models taken out of each day's model breakdown (day totals stay as they are)."""
    hidden = hidden or filters.load()
    shown = []
    for entry in data:
        if entry.get("key") in hidden["providers"] or not configured(entry):
            continue
        gone = set(hidden["models"]) | {m for m, p in (entry.get("model_providers") or {}).items()
                                        if p in hidden["providers"]}      # a hidden provider hides its models
        if gone:
            drop = lambda rows: [dict(r, models={m: v for m, v in (r.get("models") or {}).items()
                                                 if m not in gone}) for r in rows]
            entry = dict(entry, **{k: drop(entry[k]) for k in ("daily", "hourly") if entry.get(k)})
        shown.append(entry)
    return shown


def models_seen(data):
    """{model: {"cost", "tokens", "messages"}} over every daily row of every provider."""
    seen = {}
    for entry in data:
        for day in entry.get("daily") or []:     # hourly rows are a subset, not counted twice
            for model, v in (day.get("models") or {}).items():
                total = seen.setdefault(model, {"cost": 0.0, "tokens": 0, "messages": 0})
                total["cost"] += v.get("cost", 0) or 0
                total["tokens"] += v.get("tokens", 0) or 0
                total["messages"] += v.get("messages", 0) or 0
    return seen
