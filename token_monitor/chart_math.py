"""Pure helpers behind the native charts (no GTK, no cairo), so --selftest can check them.

Everything here is about turning days and numbers into positions: which days a range shows, how
tall the axis should be, which bar the pointer is over.
"""
from datetime import date, datetime, timedelta
from math import ceil, floor, log10

# hourly ranges: key -> hours shown (rolling, ending with the current hour); local estimate only
HOUR_RANGES = {"24h": 24, "72h": 72}
RANGES = (("24h", "24h", None), ("72h", "72h", None), ("7d", "7 days", 7), ("14d", "14 days", 14), ("30d", "30 days", 30), ("period", "Period", None))
DEFAULT_RANGE = "14d"


def bar_index(x, left, right, count):
    """Index of the bar slot under pointer ``x`` in a plot spanning left..right, or None outside."""
    if count <= 0 or right <= left or x < left or x >= right:
        return None
    return min(count - 1, int((x - left) / (right - left) * count))


def nice_scale(top, intervals=4):
    """(step, ceiling) with a round step so that ``intervals`` steps cover ``top``."""
    if top <= 0:
        return 1.0, float(intervals)
    raw = top / intervals
    power = 10 ** floor(log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * power
        if step * intervals >= top * 0.999:
            return step, step * intervals
    return 10 * power, 10 * power * intervals


def tick_every(slot_width, min_gap=44):
    """Label every n-th day so labels stay ``min_gap`` pixels apart."""
    return max(1, ceil(min_gap / max(slot_width, 1)))


def axis_tokens(value):
    """Short axis label: 0, 500K, 2.5M."""
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if value >= size:
            text = f"{value / size:.1f}".rstrip("0").rstrip(".")
            return text + unit
    return f"{value:.0f}"


def history_window(key, today, period_start, available_days=35):
    """First day shown for a range key, ending today."""
    days = dict((k, d) for k, _label, d in RANGES if k not in HOUR_RANGES)[key]
    if days is None:
        first = period_start
        if (today - first).days < 6:           # a fresh period: still show a week
            first = today - timedelta(days=6)
        return first
    return today - timedelta(days=min(days, available_days) - 1)


def future_days(history_days, days_to_reset, days_to_out=None):
    """How many days after today the cost chart makes room for (projection, reset marker)."""
    want = max(2, history_days // 2)
    if days_to_out is not None:
        want = max(want, days_to_out + 1)
    return max(0, min(days_to_reset, want, max(history_days, 7)))


def fill_days(daily, first, last):
    """One row per day first..last; missing days are zero rows."""
    by_day = {r["day"]: r for r in daily}
    rows, day = [], first
    while day <= last:
        key = day.isoformat()
        rows.append(by_day.get(key) or {"day": key, "tokens": 0, "input": 0, "output": 0, "cache": 0,
                                        "cost": 0.0, "messages": 0, "models": {}})
        day += timedelta(days=1)
    return rows


def moving_average(values, window=7):
    return [sum(values[max(0, i - window + 1):i + 1]) / len(values[max(0, i - window + 1):i + 1])
            for i in range(len(values))]


def top_models(row, count=2):
    """[(model, cost, share_of_cost)] busiest first."""
    models = row.get("models") or {}
    total = sum(m.get("cost", 0) for m in models.values()) or 1.0
    ranked = sorted(models.items(), key=lambda kv: (-kv[1].get("cost", 0), -kv[1].get("tokens", 0)))
    return [(name, m.get("cost", 0), m.get("cost", 0) / total) for name, m in ranked[:count]]


def parse_day(text):
    return date.fromisoformat(text[:10])


def short_model(name):
    """'github-copilot/claude-sonnet-5.5' -> 'claude-sonnet-5.5' (provider prefix dropped)."""
    name = str(name or "unknown").strip()
    return name.rsplit("/", 1)[-1] or name


def aggregate_models(rows, by="cost", top=6):
    """Per-model totals over day rows, ranked by ``by`` ('cost' or 'tokens').

    Returns [{"name", "tokens", "cost", "share", "other"}]: the first ``top`` models, the rest summed
    into one "Other" entry. ``share`` is the fraction of the chosen metric. Missing model data, or
    models without numbers, count as zero; models with nothing at all are dropped.
    """
    totals = {}
    for row in rows or []:
        for raw, data in (row.get("models") or {}).items():
            data = data if isinstance(data, dict) else {}
            entry = totals.setdefault(short_model(raw), {"tokens": 0, "cost": 0.0})
            entry["tokens"] += data.get("tokens") or 0
            entry["cost"] += data.get("cost") or 0.0
    key = "tokens" if by == "tokens" else "cost"
    other_key = "cost" if key == "tokens" else "tokens"
    ranked = sorted(((n, v) for n, v in totals.items() if v["tokens"] or v["cost"]),
                    key=lambda kv: (-kv[1][key], -kv[1][other_key], kv[0]))
    total = sum(v[key] for _n, v in ranked) or 1.0
    out = [{"name": n, "tokens": v["tokens"], "cost": v["cost"], "share": v[key] / total, "other": False}
           for n, v in ranked[:top]]
    rest = ranked[top:]
    if rest:
        tokens, cost = sum(v["tokens"] for _n, v in rest), sum(v["cost"] for _n, v in rest)
        out.append({"name": f"Other ({len(rest)})", "tokens": tokens, "cost": cost,
                    "share": (tokens if key == "tokens" else cost) / total, "other": True})
    return out


def hour_rows(hourly, key):
    """The last N hourly rows for an hourly range key ([] when there is no hourly data)."""
    return list(hourly or [])[-HOUR_RANGES[key]:]


def hour_label(row, with_date=False):
    """'14:00' for an hourly row, '14:00 \u00b7 07 Oct' with the date."""
    stamp = datetime.fromisoformat(row["hour"])
    return f"{stamp:%H:%M}" + (f" \u00b7 {stamp:%d %b}" if with_date else "")


def hour_ticks(rows, min_gap_slots):
    """[(index, label)]: a label every ``min_gap_slots`` hours, on round hours (00:00, 06:00, ...).

    A label at midnight carries the date, so a 72h chart shows where each day starts.
    """
    every = next((n for n in (1, 2, 3, 4, 6, 8, 12, 24) if n >= min_gap_slots), 24)
    out = []
    for i, row in enumerate(rows):
        stamp = datetime.fromisoformat(row["hour"])
        if stamp.hour % every:
            continue
        out.append((i, f"{stamp:%d %b}" if stamp.hour == 0 else f"{stamp:%H:00}"))
    return out
