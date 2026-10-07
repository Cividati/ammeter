"""Command line entry point: text/json output, the self-check, and launching a front-end.

    token-monitor             native GTK4 + libadwaita app (the default)
    token-monitor --float     tkinter always-on-top widget; GTK4 cannot pin a window on Wayland
    token-monitor --text      plain text snapshot
    token-monitor --json      the same data as JSON - this is what the GNOME extension consumes
    token-monitor --plot [--out FILE]
                        write the usage SVG (default .token-monitor/usage.svg)
    token-monitor --selftest  runnable checks, no network needed
"""
import json
import os
import sys
import time
import urllib.error
from pathlib import Path

from . import (APP_ID, APP_NAME, MARKS, REFRESH_SECONDS, TITLES, __version__, cache, history)
from .core import collect, live_providers, problems, visible
from .formatting import (
    BAR_WIDTH,
    EMPTY,
    FILLED,
    absolute_reset,
    bar,
    error_text,
    human_tokens,
    money,
    parse_dt,
    relative_reset,
    reset_note,
    severity,
    sparkline,
    used_label,
)

REPO = Path(__file__).resolve().parent.parent
SYSTEM_PYTHON = Path("/usr/bin/python3")  # where PyGObject, Gtk-4.0 and Adw-1.0 live


def render_text(data):
    """The --text view: one block per provider, bars included."""
    lines = []
    for entry in data:
        headline = f"{MARKS.get(entry.get('key'), ' ')}  {entry['name']}"
        if entry.get("sub"):
            headline += f"  ({entry['sub']})"
        if entry.get("stale"):
            headline += "  [stale" + (f": {entry['why']}" if entry.get("why") else "") + "]"
        lines.append(headline)
        if entry.get("error"):
            lines.append(f"     ! {entry['error']}")
        for row in entry["rows"]:
            if row["pct"] is None:
                lines.append(f"     {row['label']:<13}{row['note']}")
            else:
                lines.append(f"     {row['label']:<13}{used_label(row['pct']):>9}  "
                             f"{bar(row['pct'])}  {row['note']}")
        lines += usage_lines(entry)
    return "\n".join(lines)


def usage_lines(entry, days=14):
    """Sparklines of the last days of local usage, plus the calibration hint when there is one."""
    recent = (entry.get("daily") or [])[-days:]
    summary = entry.get("summary") or {}
    lines = []
    currency = summary.get("currency", "EUR")
    if recent and any(day["tokens"] or day["cost"] for day in recent):
        tokens, costs = [d["tokens"] for d in recent], [d["cost"] for d in recent]
        lines.append(f"     {'tokens/d':<13}{sparkline(tokens)}  {days}d, peak {human_tokens(max(tokens))}"
                     "  (local estimate)")
        real = any(day.get("cost_true") for day in recent)
        lines.append(f"     {'cost/d':<13}{sparkline(costs)}  {days}d, peak {money(max(costs), currency)}"
                     f"  ({'portal deltas + local estimate' if real else 'local estimate'})")
    calibration = summary.get("calibration")
    if calibration:
        applied = summary.get("factor_basis") == "calibrated"
        lines.append(f"     {'calibrate':<13}portal spend \u2248 {calibration['ratio']:.2f}\u00d7 local cost "
                     f"({calibration['pairs']} intervals, {money(calibration['local'], currency)} local); "
                     + ("applied to local estimates" if applied else
                        f"TOKEN_MONITOR_COST_FACTOR={calibration['ratio']:.2f}"))
    return lines


def selftest():
    """Checks that need no network: formatting, window expiry, stale/disk-cache fallback, copilot budget."""
    assert len(bar(0)) == BAR_WIDTH and bar(0) == EMPTY * BAR_WIDTH
    assert bar(100) == FILLED * BAR_WIDTH and bar(50).count(FILLED) == BAR_WIDTH // 2
    assert bar(-5) == EMPTY * BAR_WIDTH and bar(999) == FILLED * BAR_WIDTH
    assert used_label(25) == "25% used", "a bare percentage is read both ways"
    assert used_label(100.4) == "100% used"

    far = "2030-01-02T03:04:00+00:00"
    assert "02 Jan 2030" in absolute_reset(iso=far), "a reset days away must show its date"
    assert relative_reset(iso=far).startswith("in ") and relative_reset(epoch=1) == "now"
    assert reset_note(iso=far).startswith("resets ") and reset_note(None) == ""
    assert absolute_reset(iso="not-a-date") is None and relative_reset(iso="nope") is None
    assert error_text(urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)) == \
        "rate limited (HTTP 429)"
    assert error_text(RuntimeError("nope")) == "RuntimeError: nope"

    # a window that already rolled over loses its bar instead of showing a stale percentage
    from .core import drop_expired_windows
    row = {"label": "5h", "pct": 40, "reset": "2020-01-01T00:00:00+00:00", "note": "x"}
    holder = {"rows": [row]}
    drop_expired_windows(holder)
    assert row["pct"] is None and "awaiting fresh numbers" in row["note"]

    # stale + disk-cache fallback, against the real code path with a throwaway cache file
    import tempfile

    from . import core
    from .formatting import quota_row

    good = {"name": "COPILOT", "sub": "", "rows": [quota_row("budget", 18, far)]}
    boom = lambda: (_ for _ in ()).throw(RuntimeError("429"))

    saved_providers, saved_cache = core.PROVIDERS, cache.CACHE_FILE
    cache.CACHE_FILE = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-")) / "last-good.json"
    core.PROVIDERS = (("copilot", lambda: good), ("other", boom))
    try:
        core._last_good.clear()
        first = collect()
        assert first[0]["rows"] == good["rows"], "a good fetch keeps its rows"
        assert first[1]["error"], "a failure with nothing cached must report an error"

        core._last_good.clear()          # as if this were a fresh process
        core.PROVIDERS = (("copilot", boom),)
        recovered = collect()[0]
        assert recovered["rows"] == good["rows"], "the disk cache should restore the last good rows"
        assert recovered["stale"] is True and recovered["why"] == "RuntimeError: 429"
        assert "stale" in render_text([dict(recovered, name="X", key="copilot")])

        core.PROVIDERS = (("never-seen", boom),)
        assert collect()[0]["error"], "no cache plus a failure must surface the error"
    finally:
        core.PROVIDERS, cache.CACHE_FILE = saved_providers, saved_cache
        core._last_good.clear()

    from .selftest import run_copilot_checks
    run_copilot_checks()

    assert problems([{"name": "A", "reached": True, "rows": []}]) == [("A", "plan limit reached")]
    assert live_providers([{"rows": [1]}, {"rows": []}]) == 1
    print("selftest ok")


def launch_gtk():
    """Replace this process with the GTK app, on the interpreter that has PyGObject."""
    if not (SYSTEM_PYTHON.is_file() and (REPO / "token_monitor" / "gtk_app.py").is_file()):
        return False
    environment = dict(os.environ, PYTHONPATH=str(REPO))
    os.execve(str(SYSTEM_PYTHON), [str(SYSTEM_PYTHON), "-m", "token_monitor.gtk_app"], environment)
    return True  # unreachable: execve replaces the process


def write_plot(data, out=None):
    """Write the usage SVG for the copilot entry; returns the path, or None when there is no entry."""
    from .plot import render_svg
    entry = next((e for e in data if e.get("summary")), None)
    if entry is None:
        return None
    path = Path(out) if out else history.history_file().parent / "usage.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_svg(entry))
    return path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    if "--out" in argv:
        at = argv.index("--out")
        out = argv[at + 1] if at + 1 < len(argv) else None
        del argv[at:at + 2]
    args = set(argv)

    if "--version" in args:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args & {"-h", "--help"}:
        print(__doc__.strip())
        return 0
    if "--selftest" in args:
        selftest()
        return 0
    if "--plot" in args:
        written = write_plot(visible(collect()), out)
        if written is None:
            print("nothing to plot: no copilot data", file=sys.stderr)
            return 1
        print(written)
        return 0
    if args & {"--text", "--json"}:
        data = visible(collect())
        if "--json" in args:
            print(json.dumps(data, indent=2))
        else:
            print(render_text(data))
            print(f"\n{live_providers(data)}/{len(data)} providers \u00b7 "
                  f"{APP_NAME} {__version__} \u00b7 refreshed every {REFRESH_SECONDS}s")
        return 0

    if not args & {"--float", "--framed"} and launch_gtk():
        return 0
    from .float_window import run as run_float_window
    run_float_window(visible(collect()), framed="--framed" in args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
