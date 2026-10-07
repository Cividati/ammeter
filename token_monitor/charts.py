"""Native charts for the Usage page, drawn live from the entry's data with GTK's own renderer.

GTK-only (the SVG export in plot.py stays the stdlib path). Each chart is a Gtk.Widget that paints
into a Gtk.Snapshot: rectangles, rounded clips, stroked paths and Pango text, so no pycairo bridge
(python3-gi-cairo) or other extra package is needed. Needs GTK 4.14+ for Gsk paths.

Colours follow the app palette in ``__init__.py`` (blue / amber / red) and the libadwaita light or
dark theme; text takes the widget's own foreground colour. The pure helpers (day ranges, axis steps,
pointer -> bar) live in chart_math so --selftest can check them without GTK.
"""
from datetime import date, datetime, timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import STATUS
from .chart_math import (DEFAULT_RANGE, HOUR_RANGES, aggregate_models, RANGES, axis_tokens, bar_index, fill_days,
                         future_days, history_window, hour_label, hour_rows, hour_ticks, moving_average,
                         nice_scale, parse_day, tick_every, top_models)
from .formatting import human_tokens, money
from .plot import cumulative

DARK = {"input": "#8ab4f8", "output": "#f9c74f", "cache": "#6a70a6", "cost": "#8ab4f8",
        "ok": STATUS["ok"], "warn": STATUS["warn"], "crit": STATUS["crit"]}
LIGHT = {"input": "#3b7dd8", "output": "#e0a100", "cache": "#a9aed0", "cost": "#3b7dd8",
         "ok": "#3b7dd8", "warn": "#d08c00", "crit": "#d62f3f"}


def colour(value, alpha=1.0):
    """'#rrggbb' or an (r, g, b) tuple of 0..1 floats -> Gdk.RGBA."""
    if isinstance(value, str):
        h = value.lstrip("#")
        value = tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = value[0], value[1], value[2], alpha
    return c


def rect(x, y, w, h):
    r = Graphene.Rect()
    r.init(x, y, w, h)
    return r


def point(x, y):
    p = Graphene.Point()
    p.init(x, y)
    return p


def rounded(x, y, w, h, tl, tr=None, br=None, bl=None):
    tr, br, bl = (tl if v is None else v for v in (tr, br, bl))
    rr = Gsk.RoundedRect()
    size = lambda r: (lambda sz: (sz.init(r, r), sz)[1])(Graphene.Size())  # noqa: E731
    rr.init(rect(x, y, w, h), size(tl), size(tr), size(br), size(bl))
    return rr


def hour_title(row):
    """'Wed 07 Oct \u00b7 14:00\u201315:00' for the tooltip of an hourly bar."""
    start = datetime.fromisoformat(row["hour"])
    return f"{start:%a %d %b} \u00b7 {start:%H:00}\u2013{(start + timedelta(hours=1)):%H:00}"


def ease(t):
    return 1 - (1 - t) ** 3


class Pen:
    """A small drawing kit over one Gtk.Snapshot."""

    def __init__(self, snapshot, widget, fg):
        self.snap, self.widget, self.fg = snapshot, widget, fg

    # shapes
    def rect(self, x, y, w, h, col, alpha=1.0):
        if w > 0 and h > 0:
            self.snap.append_color(colour(col, alpha), rect(x, y, w, h))

    def rounded(self, x, y, w, h, r, col, alpha=1.0):
        if w <= 0 or h <= 0:
            return
        r = min(r, w / 2, h / 2)
        self.snap.push_rounded_clip(rounded(x, y, w, h, r))
        self.rect(x, y, w, h, col, alpha)
        self.snap.pop()

    def bar(self, x, y, w, h, r, segments):
        """A bar with rounded top corners holding stacked (colour, alpha, top, height) segments."""
        if w <= 0 or h <= 0:
            return
        r = min(r, w / 2, h)
        self.snap.push_rounded_clip(rounded(x, y, w, h, r, r, 0, 0))
        for col, alpha, sy, sh in segments:
            self.rect(x, sy, w, sh, col, alpha)
        self.snap.pop()

    def dot(self, cx, cy, r, col, alpha=1.0):
        self.rounded(cx - r, cy - r, 2 * r, 2 * r, r, col, alpha)

    def poly(self, points, col, alpha=1.0, width=1.0, dash=None, round_ends=False):
        if len(points) < 2:
            return
        path = Gsk.PathBuilder.new()
        path.move_to(*points[0])
        for pt in points[1:]:
            path.line_to(*pt)
        stroke = Gsk.Stroke.new(width)
        if dash:
            stroke.set_dash(dash)
        if round_ends:
            stroke.set_line_cap(Gsk.LineCap.ROUND)
            stroke.set_line_join(Gsk.LineJoin.ROUND)
        self.snap.append_stroke(path.to_path(), stroke, colour(col, alpha))

    def line(self, x1, y1, x2, y2, col, alpha=1.0, width=1.0, dash=None):
        self.poly([(x1, y1), (x2, y2)], col, alpha, width, dash)

    def clip(self, x, y, w, h):
        self.snap.push_clip(rect(x, y, w, h))

    def unclip(self):
        self.snap.pop()

    # text
    def layout(self, content, scale, bold):
        lay = self.widget.create_pango_layout(content)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(scale))
        if bold:
            attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
        lay.set_attributes(attrs)
        return lay

    def width_of(self, content, scale=0.9, bold=False):
        return self.layout(content, scale, bold).get_pixel_size()[0]

    def text(self, x, y, content, scale=0.9, col=None, alpha=1.0, anchor="start", bold=False):
        """Draw ``content`` with its baseline at ``y``; returns its width."""
        lay = self.layout(content, scale, bold)
        w = lay.get_pixel_size()[0]
        if anchor == "end":
            x -= w
        elif anchor == "middle":
            x -= w / 2
        base = lay.get_baseline() / Pango.SCALE
        self.snap.save()
        self.snap.translate(point(x, y - base))
        self.snap.append_layout(lay, colour(self.fg if col is None else col, alpha))
        self.snap.restore()
        return w

    def box(self, x, y, w, h, r, fill, border_alpha=0.18, shadow=0.3):
        """Rounded card with a soft shadow and a hairline border."""
        rr = rounded(x, y, w, h, r)
        self.snap.append_outset_shadow(rr, colour((0, 0, 0), shadow), 0, 3, 0, 10)
        self.snap.push_rounded_clip(rr)
        self.rect(x, y, w, h, fill, 0.98)
        self.snap.pop()
        self.snap.append_border(rr, [1, 1, 1, 1], [colour(self.fg, border_alpha)] * 4)


class Chart(Gtk.Widget):
    """Shared plumbing: theme colours, hover, the short grow-in on a new range."""
    LEFT, RIGHT, TOP, BOTTOM = 46, 12, 30, 26

    def __init__(self, min_height=230):
        super().__init__(hexpand=True, vexpand=True)
        self.set_size_request(300, min_height)
        self.hover = None
        self.progress = 1.0
        self._anim = None
        self._geo = None
        self._pointer = (0, 0)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.queue_draw())

    def _on_motion(self, _ctl, x, y):
        self._pointer = (x, y)
        index = bar_index(x, *self._geo) if self._geo else None
        self.hover = index
        self.queue_draw()

    def _on_leave(self, _ctl):
        self.hover = None
        self.queue_draw()

    def restart_animation(self):
        if self._anim is not None:
            self.remove_tick_callback(self._anim)
        self.progress = 0.0
        start = [None]

        def tick(_widget, clock):
            now = clock.get_frame_time()
            start[0] = start[0] or now
            self.progress = min(1.0, (now - start[0]) / 320000)
            self.queue_draw()
            if self.progress >= 1.0:
                self._anim = None
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._anim = self.add_tick_callback(tick)

    def do_snapshot(self, snapshot):
        dark = Adw.StyleManager.get_default().get_dark()
        fg = self.get_color()
        self.pal = dict(DARK if dark else LIGHT, dark=dark, fg=(fg.red, fg.green, fg.blue))
        self._geo = None
        self.paint(Pen(snapshot, self, self.pal["fg"]), self.get_width(), self.get_height())

    def paint(self, pen, width, height):
        raise NotImplementedError

    def legend(self, pen, x, y, items):
        for col, label, kind in items:
            if x + 19 + pen.width_of(label, 0.85) > self.get_width() - 6:
                break                          # narrow window: drop what does not fit
            if kind == "box":
                pen.rounded(x, y - 8, 9, 9, 2.5, col)
                x += 13
            else:
                pen.line(x, y - 3.5, x + 14, y - 3.5, col, 1.0, 2, [4, 3] if kind == "dash" else None)
                x += 19
            x += pen.text(x, y, label, 0.85, alpha=0.75) + 14

    def grid_line(self, pen, x0, x1, y):
        pen.rect(x0, round(y), x1 - x0, 1, pen.fg, 0.10)

    def x_labels(self, pen, first, count, x0, slot, y):
        every = tick_every(slot)
        shown = [i for i in range(count) if (count - 1 - i) % every == 0]
        for i in shown:
            day = first + timedelta(days=i)
            label = f"{day.day}"
            if i == shown[0] or day.day == 1:
                label += f" {day:%b}"
            pen.text(x0 + (i + 0.5) * slot, y, label, 0.85, alpha=0.65, anchor="middle")

    def hour_labels(self, pen, rows, x0, slot, y):
        for i, label in hour_ticks(rows, tick_every(slot, 40)):
            pen.text(x0 + (i + 0.5) * slot, y, label, 0.85, alpha=0.65, anchor="middle")

    def crosshair(self, pen, cx, top, bottom):
        pen.rect(round(cx), top - 4, 1, bottom - top + 4, pen.fg, 0.35)

    def tooltip(self, pen, width, x, y, title, lines):
        """lines: [(swatch colour or None, left text, right text)]."""
        pad, gap = 10, 16
        left_w = max(pen.width_of(a, 0.9) for _s, a, _b in lines)
        right_w = max(pen.width_of(b, 0.9, True) for _s, _a, b in lines)
        w = max(pen.width_of(title, 0.95, True), left_w + gap + right_w + 14) + pad * 2
        row_h = 17
        h = pad * 2 + 18 + len(lines) * row_h
        bx = x + 14 if x + 14 + w < width - 4 else x - 14 - w
        bx = max(4, bx)
        by = max(4, min(y - h / 2, self.get_height() - h - 4))
        dark = self.pal["dark"]
        pen.box(bx, by, w, h, 9, "#2a2a36" if dark else "#ffffff", shadow=0.45 if dark else 0.18)
        pen.text(bx + pad, by + pad + 11, title, 0.95, bold=True)
        row = by + pad + 18 + 12
        for swatch, a, b in lines:
            tx = bx + pad
            if swatch:
                pen.rounded(tx, row - 8, 8, 8, 2, swatch)
            tx += 13
            pen.text(tx, row, a, 0.9, alpha=0.75)
            pen.text(bx + w - pad, row, b, 0.9, anchor="end", bold=True)
            row += row_h


class TokensChart(Chart):
    """Stacked bars (cache / input / output) with the 7-day average of the total."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.first = date.today()
        self.hourly = False

    def set_data(self, rows, first, hourly=False):
        self.rows, self.first, self.hourly = rows, first, hourly
        self.queue_draw()

    def paint(self, pen, width, height):
        rows, n, pal = self.rows, len(self.rows), self.pal
        x0, x1 = self.LEFT, width - self.RIGHT
        top, bottom = self.TOP, height - self.BOTTOM
        self.legend(pen, x0, 14, [(pal["output"], "output", "box"), (pal["input"], "input", "box"),
                                  (pal["cache"], "cache", "box"), (pal["fg"], "6h average" if self.hourly else "7d average", "dash")])
        if n == 0:
            return
        step, ceiling = nice_scale(max(r["tokens"] for r in rows) * 1.02)
        slot = (x1 - x0) / n
        self._geo = (x0, x1, n)
        grow = ease(self.progress)

        def y_of(v):
            return bottom - (bottom - top) * v / ceiling

        for k in range(5):
            self.grid_line(pen, x0, x1, y_of(step * k))
            pen.text(x0 - 7, y_of(step * k) + 4, axis_tokens(step * k), 0.85, alpha=0.65, anchor="end")
        if self.hourly:
            self.hour_labels(pen, rows, x0, slot, height - 8)
        else:
            self.x_labels(pen, self.first, n, x0, slot, height - 8)

        bw = max(2.0, min(slot * 0.68, 34))
        for i, row in enumerate(rows):
            if self.hover == i:
                pen.rect(x0 + i * slot, top - 4, slot, bottom - top + 4, pen.fg, 0.07)
            if row["tokens"] <= 0:
                continue
            bx = x0 + (i + 0.5) * slot - bw / 2
            alpha = 1.0 if self.hover in (None, i) else 0.55
            segments, base = [], bottom
            for key in ("cache", "input", "output"):
                seg = (bottom - y_of(row[key])) * grow
                segments.append((pal[key], alpha, base - seg, seg))
                base -= seg
            pen.bar(bx, base, bw, bottom - base, min(4, bw / 2), segments)

        avg = moving_average([r["tokens"] for r in rows], 6 if self.hourly else 7)
        pts = [(x0 + (i + 0.5) * slot, bottom - (bottom - y_of(v)) * grow) for i, v in enumerate(avg)]
        pen.poly(pts, pen.fg, 0.85, 1.6, [5, 4], True)

        if self.hover is not None:
            i = self.hover
            row = rows[i]
            cx = x0 + (i + 0.5) * slot
            self.crosshair(pen, cx, top, bottom)
            pen.dot(cx, pts[i][1], 3.2, pen.fg)
            lines = [(None, "total", human_tokens(row["tokens"])),
                     (pal["output"], "output", human_tokens(row["output"])),
                     (pal["input"], "input", human_tokens(row["input"])),
                     (pal["cache"], "cache", human_tokens(row["cache"])),
                     (None, "6-hour avg" if self.hourly else "7-day avg", human_tokens(avg[i])),
                     (None, "cost", f"{row['cost']:.2f}")]
            for name, _cost, share in top_models(row):
                lines.append((None, name[:22], f"{share * 100:.0f}%"))
            title = hour_title(row) if self.hourly else f"{(self.first + timedelta(days=i)):%a %d %b}"
            self.tooltip(pen, width, cx, self._pointer[1], title, lines)


class CostChart(Chart):
    """Cost bars, the running spend against the budget, and where the budget runs out."""
    RIGHT = 44

    def __init__(self):
        super().__init__()
        self.model = None

    def set_data(self, entry, first, history_n, future_n, today):
        summary = entry["summary"]
        start = parse_day(summary["period_start"])
        rows = fill_days(entry["daily"], first, today)
        costs_since = [r["cost"] for r in fill_days(entry["daily"], start, today)] if today >= start else []
        series = cumulative(costs_since, summary["spent"]) if costs_since else []
        offset = (start - first).days
        spend_at = {offset + i: v for i, v in enumerate(series) if 0 <= offset + i < history_n}
        self.model = dict(summary=summary, first=first, rows=rows, start=start, future=future_n,
                          n=history_n + future_n, ti=history_n - 1, spend_at=spend_at)
        self.queue_draw()

    def paint(self, pen, width, height):
        m = self.model
        if not m:
            return
        pal, s = self.pal, m["summary"]
        n, ti, first, rows = m["n"], m["ti"], m["first"], m["rows"]
        budget, spent, burn, cur = s["budget"], s["spent"], s["burn"], s["currency"]
        x0, x1 = self.LEFT, width - self.RIGHT
        top, bottom = self.TOP, height - self.BOTTOM
        slot = (x1 - x0) / n
        self._geo = (x0, x1, n)
        grow = ease(self.progress)

        proj_end = spent + burn * (n - 1 - ti)
        lstep, lceil = nice_scale(max(max((r["cost"] for r in rows), default=0), 0.01) * 1.05)
        rstep, rceil = nice_scale(max(budget * 1.1, spent * 1.05, 0.01))   # both: four grid steps

        def yl(v):
            return bottom - (bottom - top) * v / lceil

        def yr(v):
            return bottom - (bottom - top) * v / rceil

        over = proj_end > budget
        self.legend(pen, x0, 14, [(pal["cost"], "cost/day", "box"), (pal["fg"], "spend", "line"),
                                  (pal["warn"], "budget", "dash"),
                                  (pal["crit"] if over else pal["fg"], "projection", "dash")])
        if m["future"]:                       # days still to come are lightly shaded
            pen.rect(x0 + (ti + 1) * slot, top - 4, (n - ti - 1) * slot, bottom - top + 4, pen.fg, 0.045)
        for k in range(5):
            y = bottom - (bottom - top) * k / 4
            self.grid_line(pen, x0, x1, y)
            pen.text(x0 - 7, y + 4, f"{lstep * k:.0f}" if lstep >= 1 else f"{lstep * k:.1f}", 0.85,
                     alpha=0.65, anchor="end")
            pen.text(x1 + 7, y + 4, f"{rstep * k:.0f}", 0.85, alpha=0.65)
        self.x_labels(pen, first, n, x0, slot, height - 8)

        # bars; days before the period start are dimmed, they belong to the last period
        bw = max(2.0, min(slot * 0.68, 34))
        for i, row in enumerate(rows):
            if self.hover == i:
                pen.rect(x0 + i * slot, top - 4, slot, bottom - top + 4, pen.fg, 0.07)
            if row["cost"] <= 0:
                continue
            h = (bottom - yl(row["cost"])) * grow
            alpha = 0.4 if first + timedelta(days=i) < m["start"] else 1.0
            if self.hover not in (None, i):
                alpha *= 0.6
            pen.bar(x0 + (i + 0.5) * slot - bw / 2, bottom - h, bw, h, min(4, bw / 2),
                    [(pal["cost"], alpha, bottom - h, h)])

        by = yr(budget)
        pen.clip(x0, top - 6, x1 - x0, bottom - top + 12)
        pen.line(x0, by, x1, by, pal["warn"], 0.95, 1.5, [6, 4])
        reset_pos = (parse_day(s["period_end"]) - first).days
        if 0 < reset_pos <= n:
            rx = x0 + reset_pos * slot
            pen.line(round(rx) + 0.5, top, round(rx) + 0.5, bottom, pen.fg, 0.4, 1, [2, 3])

        # spend line: plain, amber once past 70% of the budget, red past 90%
        keys = sorted(m["spend_at"])
        for ka, kb in zip(keys, keys[1:]):
            va, vb = m["spend_at"][ka], m["spend_at"][kb]
            share = vb / budget if budget else 0
            col = pal["crit"] if share >= 0.9 else pal["warn"] if share >= 0.7 else pal["fg"]
            pen.poly([(x0 + (ka + 0.5) * slot, bottom - (bottom - yr(va)) * grow),
                      (x0 + (kb + 0.5) * slot, bottom - (bottom - yr(vb)) * grow)],
                     col, 0.95, 2.2, None, True)
        pen.poly([(x0 + (ti + 0.5) * slot, yr(spent)), (x0 + (n - 0.5) * slot, yr(proj_end))],
                 pal["crit"] if over else pen.fg, 0.9 if over else 0.55, 1.8, [5, 4], True)
        pen.unclip()
        pen.dot(x0 + (ti + 0.5) * slot, yr(spent), 3.8, pal["fg"])
        pen.text(x1 - 6, by - 5, f"budget {money(budget, cur)}", 0.85, pal["warn"], anchor="end")
        if 0 < reset_pos <= n:
            pen.text(min(x0 + reset_pos * slot - 4, x1), bottom - 6,
                     f"reset {parse_day(s['period_end']):%d %b}", 0.8, alpha=0.6, anchor="end")

        # where the budget runs out
        if s.get("out_date") and s["outlook"] in ("short", "exhausted"):
            out = datetime.fromisoformat(s["out_date"])
            pos = (out.date() - first).days + out.hour / 24
            if 0 <= pos <= n:
                ox = x0 + pos * slot
                pen.dot(ox, by, 5.5, pal["crit"])
                pen.dot(ox, by, 2.2, "#2a2a36" if pal["dark"] else "#ffffff")
                label = f"budget out ~{out:%a %d %b}"
                lw = pen.width_of(label, 0.85, True)
                lx = ox - lw - 9 if ox - lw - 12 > x0 else ox + 9
                pen.rounded(lx - 4, by + 6, lw + 8, 17, 5, "#2a2a36" if pal["dark"] else "#ffffff", 0.88)
                pen.text(lx, by + 18, label, 0.85, pal["crit"], bold=True)

        if self.hover is not None:
            i = self.hover
            day = first + timedelta(days=i)
            cx = x0 + (i + 0.5) * slot
            self.crosshair(pen, cx, top, bottom)
            if i <= ti:
                row = rows[i]
                lines = [(pal["cost"], "cost", money(row["cost"], cur))]
                if i in m["spend_at"]:
                    lines.append((None, "spend so far", money(m["spend_at"][i], cur)))
                    pen.dot(cx, yr(m["spend_at"][i]), 3.4, pen.fg)
                lines += [(None, "tokens", human_tokens(row["tokens"])),
                          (pal["output"], "output", human_tokens(row["output"])),
                          (pal["input"], "input", human_tokens(row["input"])),
                          (pal["cache"], "cache", human_tokens(row["cache"]))]
                for name, cost, _share in top_models(row):
                    lines.append((None, name[:22], money(cost, cur)))
                title = f"{day:%a %d %b}"
            else:
                value = spent + burn * (i - ti)
                lines = [(None, "projected spend", money(value, cur)),
                         (None, "at", f"{money(burn, cur)}/day")]
                pen.dot(cx, yr(value), 3.4, pal["crit"] if over else pen.fg)
                title = f"{day:%a %d %b} \u00b7 forecast"
            self.tooltip(pen, width, cx, self._pointer[1], title, lines)


class HourCostChart(Chart):
    """Estimated cost per hour: bars only. The budget lines are day-based and stay on the day ranges."""

    def __init__(self):
        super().__init__()
        self.rows, self.currency = [], ""

    def set_data(self, rows, currency):
        self.rows, self.currency = rows, currency
        self.queue_draw()

    def paint(self, pen, width, height):
        rows, n, pal, cur = self.rows, len(self.rows), self.pal, self.currency
        x0, x1 = self.LEFT, width - self.RIGHT
        top, bottom = self.TOP, height - self.BOTTOM
        self.legend(pen, x0, 14, [(pal["cost"], "estimated cost/hour", "box")])
        if n == 0:
            return
        step, ceiling = nice_scale(max(max(r["cost"] for r in rows), 0.01) * 1.05)
        slot = (x1 - x0) / n
        self._geo = (x0, x1, n)
        grow = ease(self.progress)
        for k in range(5):
            y = bottom - (bottom - top) * k / 4
            self.grid_line(pen, x0, x1, y)
            pen.text(x0 - 7, y + 4, f"{step * k:.0f}" if step >= 1 else f"{step * k:.2f}", 0.85,
                     alpha=0.65, anchor="end")
        self.hour_labels(pen, rows, x0, slot, height - 8)
        bw = max(2.0, min(slot * 0.68, 34))
        for i, row in enumerate(rows):
            if self.hover == i:
                pen.rect(x0 + i * slot, top - 4, slot, bottom - top + 4, pen.fg, 0.07)
            if row["cost"] <= 0:
                continue
            h = (bottom - (bottom - (bottom - top) * row["cost"] / ceiling)) * grow
            pen.bar(x0 + (i + 0.5) * slot - bw / 2, bottom - h, bw, h, min(4, bw / 2),
                    [(pal["cost"], 1.0 if self.hover in (None, i) else 0.55, bottom - h, h)])
        if self.hover is not None:
            i = self.hover
            row = rows[i]
            cx = x0 + (i + 0.5) * slot
            self.crosshair(pen, cx, top, bottom)
            lines = [(pal["cost"], "cost (est.)", money(row["cost"], cur)),
                     (None, "tokens", human_tokens(row["tokens"])),
                     (None, "messages", f"{row['messages']:,}")]
            for name, cost, _share in top_models(row):
                lines.append((None, name[:22], money(cost, cur)))
            self.tooltip(pen, width, cx, self._pointer[1], hour_title(row), lines)


class ModelsChart(Chart):
    """Ranked horizontal share bars, one row per model. Height follows the number of rows."""
    ROW = 40

    def __init__(self):
        super().__init__(min_height=60)
        self.items, self.by, self.currency = [], "cost", ""
        self.row_hover = None
        self.set_vexpand(False)

    def set_data(self, items, by, currency):
        self.items, self.by, self.currency = items, by, currency
        self.set_size_request(280, max(1, len(items)) * self.ROW + 6)
        self.queue_draw()

    def _on_motion(self, _ctl, x, y):
        self.row_hover = int(y // self.ROW) if self.items and 0 <= y < len(self.items) * self.ROW else None
        self.queue_draw()

    def _on_leave(self, _ctl):
        self.row_hover = None
        self.queue_draw()

    def paint(self, pen, width, height):
        pal, grow = self.pal, ease(self.progress)
        x0, x1 = 8, width - 8
        for i, item in enumerate(self.items):
            y = i * self.ROW
            if self.row_hover == i:
                pen.rounded(0, y + 2, width, self.ROW - 4, 8, pen.fg, 0.06)
            detail = f"{human_tokens(item['tokens'])}  \u00b7  {money(item['cost'], self.currency)}"
            pct = f"{item['share'] * 100:.0f}%" if item["share"] >= 0.005 else "<1%"
            pw = pen.width_of(pct, 0.95, True)
            pen.text(x1, y + 17, pct, 0.95, anchor="end", bold=True)
            dw = pen.width_of(detail, 0.85)
            pen.text(x1 - pw - 12, y + 17, detail, 0.85, alpha=0.7, anchor="end")
            name, room = item["name"], x1 - pw - 12 - dw - 14 - x0
            while len(name) > 4 and pen.width_of(name, 0.95, True) > room:
                name = name[:-2].rstrip() + "\u2026"
            pen.text(x0, y + 17, name, 0.95, alpha=0.85 if item["other"] else 1.0,
                     bold=not item["other"])
            track = x1 - x0
            pen.rounded(x0, y + 25, track, 7, 3.5, pen.fg, 0.10)
            col = pen.fg if item["other"] else pal["cost"]
            alpha = 0.4 if item["other"] else (1.0 if self.row_hover in (None, i) else 0.6)
            pen.rounded(x0, y + 25, max(7, track * item["share"] * grow), 7, 3.5, col, alpha)


class ModelsCard(Gtk.Box):
    """'Most used models': title, a cost / tokens switch, the ranked bars, or a short empty line."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("card")
        self.add_css_class("chart-card")
        self.by = "cost"
        self.chart = ModelsChart()
        head = Gtk.Label(label="Most used models", xalign=0, hexpand=True, wrap=True)
        head.add_css_class("heading")
        self.group = Adw.ToggleGroup(halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        for key, label in (("cost", "Cost"), ("tokens", "Tokens")):
            self.group.add(Adw.Toggle(name=key, label=label))
        self.group.set_active_name(self.by)
        self.group.connect("notify::active-name", self._on_by)
        top = Gtk.Box(spacing=8, margin_top=10, margin_start=14, margin_end=10)
        top.append(head)
        top.append(self.group)
        self.sub = Gtk.Label(label="", xalign=0, wrap=True, margin_start=14, margin_end=14,
                             margin_bottom=6)
        self.sub.add_css_class("dim-label")
        self.sub.add_css_class("caption")
        self.empty = Gtk.Label(label="No per-model data in this range.", xalign=0, margin_start=14,
                               margin_bottom=14, visible=False)
        self.empty.add_css_class("dim-label")
        self.chart.set_margin_start(6)
        self.chart.set_margin_end(6)
        self.chart.set_margin_bottom(8)
        for w in (top, self.sub, self.chart, self.empty):
            self.append(w)
        self.rows, self.currency = [], ""

    def _on_by(self, group, _pspec):
        name = group.get_active_name()
        if name and name != self.by:
            self.by = name
            self.refresh(animate=True)

    def set_rows(self, rows, currency, animate=False):
        self.rows, self.currency = rows, currency
        self.refresh(animate)

    def refresh(self, animate=False):
        items = aggregate_models(self.rows, self.by)
        self.chart.set_data(items, self.by, self.currency)
        self.chart.set_visible(bool(items))
        self.empty.set_visible(not items)
        self.sub.set_label(f"Ranked by {'tokens' if self.by == 'tokens' else 'cost'} \u00b7 "
                           "selected range \u00b7 local estimate" if items else "")
        if animate and items:
            self.chart.restart_animation()


class Card(Gtk.Box):
    def __init__(self, title, subtitle, chart):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, vexpand=True)
        self.add_css_class("card")
        self.add_css_class("chart-card")
        head = self.title = Gtk.Label(label=title, xalign=0, wrap=True)
        head.add_css_class("heading")
        self.sub = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        self.sub.add_css_class("dim-label")
        self.sub.add_css_class("caption")
        self.append(head)
        self.append(self.sub)
        self.append(chart)
        for w in (head, self.sub):
            w.set_margin_start(14)
            w.set_margin_end(14)
        head.set_margin_top(12)
        chart.set_margin_start(6)
        chart.set_margin_end(6)
        chart.set_margin_bottom(8)


class UsagePage(Gtk.Stack):
    """Range selector, two charts, status line; or an empty state. Persists across refreshes."""

    def __init__(self):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.range = DEFAULT_RANGE
        self.entry = None

        self.empty = Adw.StatusPage(title="No usage data", icon_name="dialog-information-symbolic",
                                    description="Nothing recorded for this month yet.")
        self.add_named(self.empty, "empty")

        self.group = Adw.ToggleGroup(halign=Gtk.Align.START, homogeneous=False)
        for key, label, _days in RANGES:
            self.group.add(Adw.Toggle(name=key, label=label))
        self.group.set_active_name(self.range)
        self.group.connect("notify::active-name", self._on_range)

        self.status = Gtk.Label(xalign=1.0, hexpand=True, wrap=True, justify=Gtk.Justification.RIGHT)
        self.status.add_css_class("caption")
        bar = Gtk.Box(spacing=12)
        bar.append(self.group)
        bar.append(self.status)

        self.tokens = TokensChart()
        self.cost = CostChart()
        self.tokens_card = Card("Tokens per day", "", self.tokens)
        self.cost_card = Card("Cost per day and spend", "", self.cost)
        self.hour_cost = HourCostChart()
        self.hour_cost_card = Card("Estimated cost per hour", "", self.hour_cost)
        self.hour_cost_card.set_visible(False)
        self.models_card = ModelsCard()
        self.note = Gtk.Label(xalign=0, wrap=True)
        self.note.add_css_class("dim-label")
        self.note.add_css_class("caption")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                       margin_bottom=16, margin_start=14, margin_end=14)
        body.append(bar)
        body.append(self.tokens_card)
        body.append(self.cost_card)
        body.append(self.hour_cost_card)
        body.append(self.models_card)
        body.append(self.note)
        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=700, child=body)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      vexpand=True, hexpand=True)
        scroller.set_child(clamp)
        self.add_named(scroller, "charts")

    def _on_range(self, group, _pspec):
        name = group.get_active_name()
        if name and name != self.range:
            self.range = name
            self._apply(animate=True)

    def set_entry(self, entry):
        first_time = self.entry is None
        self.entry = entry
        if not entry or not entry.get("summary") or not entry.get("daily"):
            self.set_visible_child_name("empty")
            return
        self.set_visible_child_name("charts")
        self._apply(animate=first_time)

    def _apply_hourly(self, entry, animate):
        """24h / 72h: tokens and estimated cost per clock hour; the budget view stays on day ranges."""
        s = entry["summary"]
        rows = hour_rows(entry.get("hourly"), self.range)
        self.tokens.set_data(rows, date.today(), hourly=True)
        self.hour_cost.set_data(rows, s.get("currency", ""))
        self.models_card.set_rows(rows, s.get("currency", ""), animate)
        if rows:
            span = f"{hour_label(rows[0], True)} \u2013 {hour_label(rows[-1], True)}"
            self.tokens_card.sub.set_label(f"{span} \u00b7 {human_tokens(sum(r['tokens'] for r in rows))} "
                                           "in total \u00b7 local estimate")
            self.hour_cost_card.sub.set_label(
                f"{money(sum(r['cost'] for r in rows), s.get('currency', ''))} in total \u00b7 "
                "local estimate, not billed credits")
        else:
            for card in (self.tokens_card, self.hour_cost_card):
                card.sub.set_label("No hourly data: it comes from OpenCode's local usage records.")
        self.status.set_label(" \u00b7 ".join(p for p in (entry.get("sub"), entry.get("why")) if p))
        self.note.set_label("Hourly numbers are OpenCode's own estimate. The portal's spend is not "
                            "split by hour, so the budget lines only appear on the day ranges.")
        if animate:
            self.tokens.restart_animation()
            self.hour_cost.restart_animation()

    def _apply(self, animate=False):
        entry = self.entry
        if not entry or not entry.get("summary") or not entry.get("daily"):
            return
        s = entry["summary"]
        hourly = self.range in HOUR_RANGES
        self.cost_card.set_visible(not hourly)
        self.hour_cost_card.set_visible(hourly)
        self.tokens_card.title.set_label("Tokens per hour" if hourly else "Tokens per day")
        if hourly:
            return self._apply_hourly(entry, animate)
        today = date.today()
        start = parse_day(s["period_start"])
        end = parse_day(s["period_end"])
        first = history_window(self.range, today, start)
        hist = (today - first).days + 1
        rows = fill_days(entry["daily"], first, today)
        self.tokens.set_data(rows, first)
        self.models_card.set_rows(rows, s.get("currency", ""), animate)
        if self.range == "period":
            future = max(0, (end - today).days)
        else:
            to_out = None
            if s.get("out_date") and s["outlook"] in ("short", "exhausted"):
                to_out = max(0, (parse_day(s["out_date"]) - today).days)
            future = future_days(hist, (end - today).days, to_out)
        self.cost.set_data(entry, first, hist, future, today)
        self.tokens_card.sub.set_label(f"{first:%d %b} \u2013 {today:%d %b} \u00b7 "
                                       f"{human_tokens(sum(r['tokens'] for r in rows))} in total \u00b7 "
                                       "local estimate")
        cur = s["currency"]
        self.cost_card.sub.set_label(
            f"{money(s['spent'], cur)} of {money(s['budget'], cur)} \u00b7 "
            f"{money(s['burn'], cur)}/day ({s['burn_basis']}) \u00b7 resets {end:%d %b}")
        label = " \u00b7 ".join(p for p in (entry.get("sub"), entry.get("why")) if p)
        self.status.set_label(label)
        for css in ("error", "dim-label"):
            self.status.remove_css_class(css)
        self.status.add_css_class("error" if entry.get("stale") else "dim-label")
        note = ("Cost is OpenCode's own estimate, not billed credits. "
                "The spend line follows the current spend.")
        if first < start:
            note += " Paler bars belong to the previous period."
        self.note.set_label(note)
        if animate:
            self.tokens.restart_animation()
            self.cost.restart_animation()
