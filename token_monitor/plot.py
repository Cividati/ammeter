"""Hand-built SVG usage report: tokens per day, and cost per day against the monthly budget.

Input is the copilot entry from core/providers (``daily`` rows and the ``summary`` dict); output is
one self-contained SVG string with its own dark background, so it looks the same in a browser, an
image viewer and the GTK app. Stdlib only, no network.
"""
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from .formatting import human_tokens, money
from .usage import coverage

W, H = 820, 660
LEFT, RIGHT = 64, 64
BG, CARD, GRID = "#16161e", "#1f2030", "#34344a"
TEXT, DIM = "#e6e6f0", "#9a9aa8"
INPUT, OUTPUT, CACHE = "#8ab4f8", "#f9c74f", "#5b5b8f"
COST, COST_EST, BUDGET, CRIT = "#8ab4f8", "#52688f", "#f9c74f", "#ff5555"
TOKEN_DAYS = 30
FONT = 'font-family="DejaVu Sans, Verdana, sans-serif"'


def _text(x, y, content, size=11, fill=TEXT, anchor="start", weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}" {FONT}>{escape(str(content))}</text>')


def _line(x1, y1, x2, y2, stroke=GRID, width=1, dash=None):
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" '
            f'stroke-width="{width}"{extra}/>')


def _rect(x, y, w, h, fill, rx=0):
    if h <= 0:
        return ""
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" rx="{rx}"/>'


def _polyline(points, stroke, width=2, dash=None):
    if len(points) < 2:
        return ""
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return (f'<polyline points="{coords}" fill="none" stroke="{stroke}" stroke-width="{width}" '
            f'stroke-linejoin="round"{extra}/>')


def _panel(top, height, title):
    return [f'<rect x="16" y="{top}" width="{W - 32}" height="{height}" rx="10" fill="{CARD}"/>',
            _text(32, top + 24, title, 13, TEXT, weight="bold")]


def _legend(x, y, items):
    out = []
    for colour, label in items:
        out.append(f'<rect x="{x:.1f}" y="{y - 9}" width="10" height="10" fill="{colour}" rx="2"/>')
        out.append(_text(x + 14, y, label, 10, DIM))
        x += 14 + 6.2 * len(label) + 14
    return out


def _empty(top, height, why):
    return [_text(W / 2, top + height / 2, why, 12, DIM, "middle")]


def _tokens_panel(top, height, rows):
    parts = _panel(top, height, f"Tokens per day \u00b7 last {TOKEN_DAYS} days \u00b7 local estimate")
    parts += _legend(W - 32 - 190, top + 24, [(INPUT, "input"), (OUTPUT, "output"), (CACHE, "cache")])
    rows = rows[-TOKEN_DAYS:]
    if not rows or not any(r["tokens"] for r in rows):
        return parts + _empty(top, height, "no local usage data")
    plot_top, plot_bottom = top + 44, top + height - 30
    x0, x1 = LEFT, W - RIGHT
    step = (x1 - x0) / len(rows)
    ceiling = max(r["tokens"] for r in rows) * 1.08

    def y_of(value):
        return plot_bottom - (plot_bottom - plot_top) * value / ceiling

    for frac in (0, 0.5, 1):
        y = y_of(ceiling / 1.08 * frac)
        parts += [_line(x0, y, x1, y), _text(x0 - 6, y + 4, human_tokens(ceiling / 1.08 * frac), 10, DIM, "end")]
    for i, row in enumerate(rows):
        x = x0 + i * step + step * 0.15
        base = plot_bottom
        for key, colour in (("cache", CACHE), ("input", INPUT), ("output", OUTPUT)):
            height_px = (plot_bottom - plot_top) * row[key] / ceiling
            parts.append(_rect(x, base - height_px, step * 0.7, height_px, colour))
            base -= height_px
        if i % 5 == 0 or i == len(rows) - 1:
            parts.append(_text(x + step * 0.35, plot_bottom + 14, row["day"][8:], 10, DIM, "middle"))
    average = []
    for i in range(len(rows)):
        window = rows[max(0, i - 6):i + 1]
        average.append((x0 + (i + 0.5) * step, y_of(sum(r["tokens"] for r in window) / len(window))))
    parts.append(_polyline(average, TEXT, 1.5, "4 3"))
    parts.append(_text(x1, top + 24 + 14, "dashed: 7-day average of total", 10, DIM, "end"))
    return parts


def _days_of(summary):
    start = datetime.fromisoformat(summary["period_start"]).date()
    end = datetime.fromisoformat(summary["period_end"]).date()
    return start, max(1, (end - start).days)


def cumulative(daily_costs, spent):
    """Running spend per day, anchored so the last day equals ``spent`` (never below zero)."""
    out, after = [], 0.0
    for cost in reversed(daily_costs):
        out.append(max(0.0, spent - after))
        after += cost
    return out[::-1]


def _cost_panel(top, height, entry, today):
    summary = entry["summary"]
    currency = summary["currency"]
    parts = _panel(top, height, f"Cost per day vs budget \u00b7 {money(summary['budget'], currency)} per period")
    parts += _legend(W - 32 - 330, top + 24, [(COST, "portal"), (COST_EST, "local est."), (BUDGET, "budget"),
                                              (TEXT, "spend")])
    start, count = _days_of(summary)
    start_dt = datetime.fromisoformat(summary["period_start"])
    by_day = {r["day"]: r for r in entry["daily"]}
    elapsed = max(1, min(count, (today - start).days + 1))
    days = [by_day.get((start + timedelta(days=i)).isoformat(), {}) for i in range(elapsed)]
    costs = [d.get("cost", 0.0) for d in days]
    spent, budget = summary["spent"], summary["budget"]
    series = cumulative(costs, spent)
    left_days = count - (today - start).days - 1 + 0.5     # rest of today plus the days after it
    projected = [(elapsed - 0.5, spent), (count, spent + summary["burn"] * max(left_days, 0))]
    plot_top, plot_bottom = top + 44, top + height - 30
    x0, x1 = LEFT, W - RIGHT
    step = (x1 - x0) / count
    left_max = max(max(costs, default=0), 10.0) * 1.15
    right_max = max(budget, spent, projected[1][1]) * 1.08

    def y_left(value):
        return plot_bottom - (plot_bottom - plot_top) * min(value, left_max) / left_max

    def y_right(value):
        return plot_bottom - (plot_bottom - plot_top) * min(value, right_max) / right_max

    for frac in (0, 0.5, 1):
        y = plot_top + (plot_bottom - plot_top) * (1 - frac)
        parts += [_line(x0, y, x1, y),
                  _text(x0 - 6, y + 4, f"{left_max / 1.15 * frac:.0f}", 10, COST, "end"),
                  _text(x1 + 6, y + 4, f"{right_max / 1.08 * frac:.0f}", 10, TEXT)]
    for i, day in enumerate(days):          # portal deltas solid, local estimates paler on top
        x, base = x0 + i * step + step * 0.15, plot_bottom
        for key, colour in (("cost_true", COST), ("cost_est", COST_EST)):
            height_px = plot_bottom - y_left(day.get(key, 0.0))
            parts.append(_rect(x, base - height_px, step * 0.7, height_px, colour))
            base -= height_px
    for i in range(count):
        if i % 5 == 0 or i == count - 1:
            parts.append(_text(x0 + (i + 0.5) * step, plot_bottom + 14,
                               (start + timedelta(days=i)).strftime("%d"), 10, DIM, "middle"))
    parts.append(_line(x0, y_right(budget), x1, y_right(budget), BUDGET, 1.5, "6 4"))
    parts.append(_text(x0 + 4, y_right(budget) - 4, f"budget {money(budget, currency)}", 10, BUDGET))
    if coverage(entry["daily"], spent, start_dt) >= 0.5:   # else local usage would draw a false curve
        parts.append(_polyline([(x0 + (i + 0.5) * step, y_right(v)) for i, v in enumerate(series)], TEXT, 2))
    over = projected[1][1] > budget
    parts.append(_polyline([(x0 + px * step, y_right(py)) for px, py in projected],
                           CRIT if over else DIM, 1.5, "4 3"))
    parts.append(f'<circle cx="{x0 + (elapsed - 0.5) * step:.1f}" cy="{y_right(spent):.1f}" r="3.5" fill="{TEXT}"/>')
    if summary.get("out_date") and summary["outlook"] in ("short", "exhausted"):
        out = datetime.fromisoformat(summary["out_date"])
        position = (out.date() - start).days + out.hour / 24
        if 0 <= position <= count:
            x = x0 + position * step
            parts += [_line(x, plot_top, x, plot_bottom, CRIT, 1.5, "3 3"),
                      _text(min(x, x1 - 4), plot_top - 4, f"out ~{out:%a %d %b}", 10, CRIT,
                            "end" if x > (x0 + x1) / 2 else "start")]
    return parts


def render_svg(entry, today=None):
    """The report for a copilot entry as an SVG string (always well-formed XML)."""
    today = today or date.today()
    summary = entry.get("summary")
    head = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
            f'<rect width="{W}" height="{H}" fill="{BG}"/>']
    if not summary:
        head.append(_text(W / 2, H / 2, entry.get("error") or "no data", 13, DIM, "middle"))
        return "\n".join(head + ["</svg>"])
    currency = summary["currency"]
    sub = entry.get("sub") or ("portal" if not entry.get("stale") else "")
    title = (f"{entry['name']}  {money(summary['spent'], currency)} of "
             f"{money(summary['budget'], currency)}")
    head += [_text(24, 30, title, 16, TEXT, weight="bold"),
             _text(W - 24, 30, " \u00b7 ".join(p for p in (sub, entry.get("why")) if p), 11,
                   CRIT if entry.get("stale") else DIM, "end")]
    body = _tokens_panel(44, 270, entry["daily"]) + _cost_panel(326, 270, entry, today)
    share = coverage(entry["daily"], summary["spent"], datetime.fromisoformat(summary["period_start"]))
    lasts = next((r for r in entry["rows"] if r["label"] == "lasts until"), {})
    foot = [_text(24, H - 50, f"Lasts until: {lasts.get('note', '')}", 11, CRIT if "level" in lasts else TEXT),
            _text(24, H - 34, "Bars: real daily spend from portal snapshots (solid), OpenCode estimate where there are none (pale). "
                              f"Burn rate: {money(summary['burn'], currency)}/day ({summary['burn_basis']}).",
                  10, DIM)]
    if share < 0.5:
        foot.append(_text(24, H - 18, f"Local usage covers only {share * 100:.0f}% of this period's spend "
                                      "(other clients?): spend curve hidden.", 10, DIM))
    else:
        foot.append(_text(24, H - 18, "Spend line is anchored to the current spend.", 10, DIM))
    return "\n".join(head + body + foot + ["</svg>"])
