"""Small pure helpers: progress bars, reset times, the row shape, error wording.

No network, no UI, no imports from the rest of the package, so this is the easiest part to test.
"""
import urllib.error
from datetime import datetime, timezone

from . import COLOURS

BAR_WIDTH = 12
FILLED, EMPTY = "\u2593", "\u2591"


def bar(pct, width=BAR_WIDTH):
    """A block-character bar: 0% -> all empty, 100% -> all filled."""
    cells = max(0, min(width, round(pct / 100 * width)))
    return FILLED * cells + EMPTY * (width - cells)


def severity(pct):
    """CSS/severity class for a quota percentage."""
    return "crit" if pct >= 90 else "warn" if pct >= 70 else ""


def parse_dt(iso=None, epoch=None):
    """Parse an ISO string or a unix timestamp into an aware datetime; None if unusable."""
    try:
        parsed = datetime.fromtimestamp(epoch, timezone.utc) if epoch else datetime.fromisoformat(iso)
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def to_iso(epoch=None):
    parsed = parse_dt(epoch=epoch)
    return parsed.isoformat() if parsed else None


def absolute_reset(iso=None, epoch=None):
    """14:20 if the reset is today, else Wed 07 Oct 19:59 (with the year if it differs)."""
    parsed = parse_dt(iso, epoch)
    if not parsed:
        return None
    local, now = parsed.astimezone(), datetime.now().astimezone()
    if local.date() == now.date():
        return local.strftime("%H:%M")
    pattern = "%a %d %b %H:%M" if local.year == now.year else "%a %d %b %Y %H:%M"
    return local.strftime(pattern)


def relative_reset(iso=None, epoch=None):
    """'in 28d3h', 'in 2h33m', 'in 4m', or 'now' once it has passed."""
    parsed = parse_dt(iso, epoch)
    if not parsed:
        return None
    seconds = int((parsed - datetime.now(timezone.utc)).total_seconds())
    if seconds <= 0:
        return "now"
    days, rest = divmod(seconds, 86400)
    hours, minutes = divmod(rest // 60, 60)
    if days:
        return f"in {days}d{hours}h"
    return f"in {hours}h{minutes:02d}m" if hours else f"in {minutes}m"


def reset_note(iso=None, epoch=None):
    """'resets Wed 07 Oct 19:59 - in 2d8h'. Empty when the timestamp is unusable."""
    absolute, relative = absolute_reset(iso, epoch), relative_reset(iso, epoch)
    if not absolute:
        return ""
    return f"resets {absolute} \u00b7 {relative}" if relative else f"resets {absolute}"


def quota_row(label, pct, resets_at=None):
    """A row carrying a percentage and the window it belongs to."""
    return {"label": label, "pct": round(float(pct)), "reset": resets_at,
            "note": reset_note(iso=resets_at)}


def used_label(pct):
    """Spell out that a percentage is consumption, not what is left.

    A bare '33%' next to a bar is read both ways; the bar fills up as the quota is consumed, so say
    'used' and let the balance rows carry the money instead.
    """
    return f"{int(pct)}% used"


def balance_row(label, note):
    """A row carrying money instead of a quota: no bar, so no percentage."""
    return {"label": label, "pct": None, "note": note}


def error_text(exception):
    """Short, non-alarming name for a fetch failure."""
    if isinstance(exception, urllib.error.HTTPError) and exception.code == 429:
        return "rate limited (HTTP 429)"
    if isinstance(exception, urllib.error.URLError):
        return "offline"
    return f"{type(exception).__name__}: {exception}"


def colour_for(key):
    return COLOURS.get(key, "#e6e6f0")
