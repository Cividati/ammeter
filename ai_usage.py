#!/usr/bin/env python3
"""ai-usage - floating desktop widget: Claude Code / Codex / OpenRouter usage.

Reads the logins the CLIs already store locally; nothing is uploaded anywhere.
  ~/.claude/.credentials.json          -> api.anthropic.com/api/oauth/usage
  ~/.codex/auth.json                   -> chatgpt.com/backend-api/wham/usage
  ~/.hermes/.env OPENROUTER_API_KEY    -> openrouter.ai/api/v1/credits + /key

Usage:
  ai-usage            native GTK4 + libadwaita app
  ai-usage --float    the tkinter always-on-top widget (GTK4 cannot do always-on-top on Wayland)
  ai-usage --text     print the same numbers to stdout and exit
  ai-usage --json     machine-readable dump
  ai-usage --selftest runnable checks (bars, resets, cache)
"""
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
REFRESH_S = 120
BAR_W = 12
FILLED, EMPTY = "\u2593", "\u2591"

BG = "#0b0b11"
PANEL = "#14141d"
LINE = "#23232f"
DIM = "#7a7a8c"
FG = "#e6e6f0"
ACCENT = {"claude": "#d97757", "codex": "#412991", "openrouter": "#2dbe7f", "deepseek": "#4d6bfe"}
ICONS = {"claude": "\u2733", "codex": "\u25c6", "openrouter": "\u21c4", "deepseek": "\u25c9"}
NAME = {"claude": "CLAUDE", "codex": "CODEX", "openrouter": "OPENROUTER", "deepseek": "DEEPSEEK"}


def _env_key(name):
    """Read KEY=... from ~/.hermes/.env (the file Hermes itself uses for provider keys)."""
    env = HOME / ".hermes" / ".env"
    if env.exists():
        m = re.search(r'(?im)^\s*(?:export\s+)?' + name + r'\s*=\s*["\']?([^\s"\']+)',
                      env.read_text(errors="ignore"))
        if m:
            return m.group(1)
    return None


def _get(url, headers, timeout=20):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _dt(iso=None, epoch=None):
    """Parse an ISO string or a unix timestamp into an aware datetime; None if unusable."""
    try:
        d = datetime.fromtimestamp(epoch, timezone.utc) if epoch else datetime.fromisoformat(iso)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(epoch=None):
    d = _dt(epoch=epoch)
    return d.isoformat() if d else None


def _when(iso=None, epoch=None):
    """Absolute reset time: 14:20 if today, else Wed 07 Oct 19:59 (year only if it differs)."""
    d = _dt(iso, epoch)
    if not d:
        return None
    local, now = d.astimezone(), datetime.now().astimezone()
    if local.date() == now.date():
        return local.strftime("%H:%M")
    return local.strftime("%a %d %b %H:%M" if local.year == now.year else "%a %d %b %Y %H:%M")


def _in(iso=None, epoch=None):
    """Relative reset time: 'in 28d3h', 'in 2h33m', 'in 4m'; None if unknown."""
    d = _dt(iso, epoch)
    if not d:
        return None
    secs = int((d - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return "now"
    days, rest = divmod(secs, 86400)
    hours, mins = divmod(rest // 60, 60)
    if days:
        return f"in {days}d{hours}h"
    return f"in {hours}h{mins:02d}m" if hours else f"in {mins}m"


def _reset_note(iso=None, epoch=None):
    """When a quota window resets, absolute and relative, e.g. 'resets Wed 07 Oct 19:59 - in 2d5h'."""
    when, rel = _when(iso, epoch), _in(iso, epoch)
    if not when:
        return ""
    return f"resets {when} \u00b7 {rel}" if rel else f"resets {when}"


def _pct_row(label, pct, resets_at=None, prefix=""):
    return {"label": label, "pct": round(float(pct)), "reset": resets_at,
            "note": (f"{prefix} \u00b7 " if prefix else "") + _reset_note(iso=resets_at)}


def _err_text(e):
    """Short, non-alarming name for a fetch failure."""
    if isinstance(e, urllib.error.HTTPError) and e.code == 429:
        return "rate limited (HTTP 429)"
    if isinstance(e, urllib.error.URLError):
        return "offline"
    return f"{type(e).__name__}: {e}"


CACHE_FILE = HOME / ".cache" / "ai-usage" / "last-good.json"


def _load_cache():
    """Last good result per provider from the previous run, so a 429 on start still shows numbers."""
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache))
    except Exception:
        pass


def _expire(d):
    """A window that already rolled over makes its old number meaningless: drop the bar and say so."""
    now = datetime.now(timezone.utc)
    for row in d.get("rows") or []:
        if row.get("pct") is None:
            continue
        rolled = _dt(row.get("reset"))
        if rolled and rolled <= now:
            row["pct"] = None
            row["note"] = f"window reset {_when(iso=row['reset'])} \u2014 awaiting fresh numbers"


def fetch_claude():
    cred = json.loads((HOME / ".claude" / ".credentials.json").read_text())
    tok = (cred.get("claudeAiOauth") or {}).get("accessToken")
    if not tok:
        raise RuntimeError("no oauth token")
    j = _get("https://api.anthropic.com/api/oauth/usage",
             {"Authorization": f"Bearer {tok}",
              "anthropic-beta": "oauth-2025-04-20",
              "User-Agent": "claude-code/2.0",
              "Accept": "application/json"})
    sub = (cred.get("claudeAiOauth") or {}).get("subscriptionType") or ""
    rows = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d"),
                       ("seven_day_opus", "opus 7d"), ("seven_day_sonnet", "sonnet 7d")):
        v = j.get(key) or {}
        if v.get("utilization") is not None:
            rows.append(_pct_row(label, v["utilization"], v.get("resets_at")))
    return {"name": "CLAUDE", "rows": rows, "sub": sub}


def fetch_codex():
    auth = json.loads((HOME / ".codex" / "auth.json").read_text())
    t = auth.get("tokens") or {}
    tok, acct = t.get("access_token"), t.get("account_id")
    if not (tok and acct):
        raise RuntimeError("no codex tokens")
    j = _get("https://chatgpt.com/backend-api/wham/usage",
             {"Authorization": f"Bearer {tok}", "chatgpt-account-id": acct,
              "User-Agent": "codex-cli/0.1", "Accept": "application/json"})
    rows = []
    rl = j.get("rate_limit") or {}
    for win, label in (("primary_window", "session"), ("secondary_window", "weekly")):
        w = rl.get(win) or {}
        if w.get("used_percent") is not None:
            # the reset date + countdown already say how far out the window is; no need for its span
            rows.append(_pct_row(label, w["used_percent"], _iso(w.get("reset_at"))))
    cr = j.get("credits") or {}
    if cr.get("balance") is not None:
        rows.append({"label": "balance", "pct": None, "note": f"${cr['balance']}"})
    return {"name": "CODEX", "rows": rows, "sub": j.get("plan_type") or "",
            "reached": bool(rl.get("limit_reached"))}


def fetch_openrouter():
    key = _env_key("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("no OPENROUTER_API_KEY in ~/.hermes/.env")
    h = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    cred = _get("https://openrouter.ai/api/v1/credits", h)["data"]
    k = _get("https://openrouter.ai/api/v1/key", h)["data"]
    total, used = float(cred.get("total_credits") or 0), float(cred.get("total_usage") or 0)
    left = max(total - used, 0.0)
    # prepaid credit, same model as DeepSeek: headline the balance, never a quota percentage
    rows = [{"label": "balance", "pct": None,
             "note": f"${left:.2f} left of ${total:.2f}" if total else "no credit purchased"}]
    if k.get("usage_daily") is not None:
        rows.append({"label": "spend", "pct": None,
                     "note": f"${float(k.get('usage_daily') or 0):.2f} today   "
                             f"${float(k.get('usage_monthly') or 0):.2f} month"})
    return {"name": "OPENROUTER", "rows": rows, "sub": ""}


def bar(pct):
    cells = max(0, min(BAR_W, round(pct / 100 * BAR_W)))
    return FILLED * cells + EMPTY * (BAR_W - cells)


def fetch_deepseek():
    key = _env_key("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("no DEEPSEEK_API_KEY in ~/.hermes/.env")
    j = _get("https://api.deepseek.com/user/balance",
             {"Authorization": f"Bearer {key}", "Accept": "application/json"})
    rows = []
    for b in j.get("balance_infos") or []:
        cur = b.get("currency") or ""
        note = f"{cur} {b.get('total_balance')}".strip()
        if float(b.get("granted_balance") or 0):
            note += f"  (granted {b['granted_balance']})"
        rows.append({"label": "balance", "pct": None, "note": note})
    if not rows:
        rows.append({"label": "balance", "pct": None, "note": "no balance reported"})
    return {"name": "DEEPSEEK", "rows": rows, "sub": "available" if j.get("is_available") else "unavailable"}


FETCHERS = (("claude", fetch_claude), ("codex", fetch_codex),
            ("openrouter", fetch_openrouter), ("deepseek", fetch_deepseek))

_LAST = {}  # last good result per provider, this process; the Claude endpoint 429s if polled hard


def collect():
    disk = _load_cache()
    out = []
    for key, fn in FETCHERS:
        try:
            d = fn()
            _LAST[key] = d
            if key in NAME:
                disk[key] = d
        except Exception as e:
            prev = _LAST.get(key) or disk.get(key)
            if prev:
                d = dict(prev, stale=True, why=_err_text(e))
            else:
                d = {"name": NAME.get(key, key.upper()), "rows": [], "sub": "",
                     "error": _err_text(e)}
        d["key"] = key
        _expire(d)
        out.append(d)
    _save_cache(disk)
    return out


# ---------------------------------------------------------------- text output
def render_text(data):
    lines = []
    for d in data:
        head = ICONS.get(d.get("key"), " ") + "  " + d["name"]
        if d.get("sub"):
            head += f"  ({d['sub']})"
        if d.get("stale"):
            head += "  [stale" + (f": {d['why']}" if d.get("why") else "") + "]"
        lines.append(head)
        if d.get("error"):
            lines.append(f"     ! {d['error']}")
        for r in d["rows"]:
            pct = "" if r["pct"] is None else f"{r['pct']:>3}%  " + bar(r["pct"])
            lines.append(f"     {r['label']:<9}{pct:<24}{r['note']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- tk widget
MONO = "DejaVu Sans Mono"
SANS = "DejaVu Sans"


def _rule(parent, row, tk):
    f = tk.Frame(parent, bg=LINE, height=1)
    f.grid(row=row, column=0, sticky="we", padx=14, pady=(0, 6))
    return f


def build_gui(data, refresh_s=REFRESH_S, framed=False):
    import tkinter as tk

    root = tk.Tk()
    root.title("ai-usage")
    if not framed:
        root.overrideredirect(True)
        root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.96)
    except tk.TclError:
        pass
    root.configure(bg=BG)

    wrap = tk.Frame(root, bg=BG, highlightbackground=LINE, highlightthickness=1)
    wrap.pack(fill="both", expand=True)

    head = tk.Frame(wrap, bg=BG)
    head.grid(row=0, column=0, sticky="we", padx=14, pady=(12, 5))
    head.columnconfigure(2, weight=1)
    dot = tk.Label(head, text="\u25cf", bg=BG, fg="#5b7cfa", font=(SANS, 11))
    dot.grid(row=0, column=0, sticky="w", padx=(0, 7))
    tk.Label(head, text="AI USAGE", bg=BG, fg=FG, font=(MONO, 11, "bold"),
             anchor="w").grid(row=0, column=1, sticky="w")
    clock = tk.Label(head, text=f"\u21bb {refresh_s}s", bg=BG, fg=DIM, font=(MONO, 9), anchor="e")
    clock.grid(row=0, column=2, sticky="e")

    _rule(wrap, 1, tk)

    body = tk.Frame(wrap, bg=BG)
    body.grid(row=2, column=0, sticky="we")

    foot = tk.Label(wrap, text="", bg=BG, fg=DIM, font=(MONO, 9), anchor="w")
    foot.grid(row=3, column=0, sticky="we", padx=14, pady=(10, 10))

    def redraw(data):
        for w in body.winfo_children():
            w.destroy()
        body.columnconfigure(0, minsize=28)
        r, worst = 0, 0
        for d in data:
            key = d.get("key")
            color = ACCENT.get(key, FG)
            stale = bool(d.get("stale"))
            tk.Label(body, text=ICONS.get(key, "\u25cf"), bg=BG, fg=color,
                     font=(SANS, 15)).grid(row=r, column=0, sticky="w", padx=(14, 6), pady=(5, 0))
            hbox = tk.Frame(body, bg=BG)
            hbox.grid(row=r, column=1, columnspan=3, sticky="w", pady=(5, 0))
            tk.Label(hbox, text=d["name"], bg=BG, fg=color, font=(MONO, 11, "bold")).pack(side="left")
            tag = (d.get("sub") or "") + ("   \u2014 stale" + (f" ({d['why']})" if d.get("why") else "")
                                          if stale else "")
            if tag:
                tk.Label(hbox, text=tag, bg=BG, fg="#f9c74f" if stale else DIM,
                         font=(MONO, 9)).pack(side="left", padx=(9, 0))
            r += 1
            if d.get("error") or not d["rows"]:
                tk.Label(body, text="\u26a0 " + (d.get("error") or "no data"), bg=BG, fg="#ff6b6b",
                         font=(MONO, 10), anchor="w").grid(
                    row=r, column=1, columnspan=3, sticky="w", padx=(0, 14))
                r += 2
                continue
            for row in d["rows"]:
                pct = row["pct"]
                tk.Label(body, text=row["label"], bg=BG, fg=DIM, font=(MONO, 10),
                         anchor="w", width=9).grid(row=r, column=1, sticky="w")
                if pct is None:
                    tk.Label(body, text=row["note"], bg=BG, fg=FG, font=(MONO, 10), anchor="w").grid(
                        row=r, column=2, columnspan=2, sticky="w", padx=(BAR_W + 4, 14))
                else:
                    worst = max(worst, int(pct))
                    tk.Label(body, text=bar(pct), bg=BG, width=BAR_W, anchor="w",
                             fg="#ff5555" if pct >= 90 else "#f9c74f" if pct >= 70 else color,
                             font=(MONO, 10)).grid(row=r, column=2, sticky="w")
                    tk.Label(body, text=f"{int(pct):>3}%   {row['note']}", bg=BG, fg=FG,
                             font=(MONO, 10), anchor="w").grid(row=r, column=3, sticky="w", padx=(4, 14))
                r += 1
            r += 1
        dot.config(fg="#ff5555" if worst >= 90 else "#f9c74f" if worst >= 70 else "#5b7cfa")

    redraw(data)

    # drag to move, right-click for menu / quit, Esc quits
    drag = {}

    def press(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def move(e):
        if "x" in drag:
            root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    menu = tk.Menu(root, tearoff=0)

    def quit_(*_):
        root.destroy()

    menu.add_command(label="Refresh now", command=lambda: kick())
    menu.add_command(label="Quit", command=quit_)

    def bind(w):
        w.bind("<Button-1>", press)
        w.bind("<B1-Motion>", move)
        w.bind("<ButtonRelease-1>", lambda e: drag.clear())
        w.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        for child in w.winfo_children():
            bind(child)

    q = queue.Queue()

    def worker():
        while True:
            q.put(collect())
            time.sleep(refresh_s)

    def kick():
        foot.config(text="refreshing\u2026")
        threading.Thread(target=lambda: q.put(collect()), daemon=True).start()

    def poll():
        try:
            while True:
                data = q.get_nowait()
                redraw(data)
                live = sum(1 for d in data if d["rows"])
                foot.config(text=f"updated {datetime.now():%H:%M:%S}   \u00b7   "
                                 f"{live}/{len(data)} providers   \u00b7   right-click for menu")
        except queue.Empty:
            pass
        root.after(2000, poll)

    bind(wrap)
    root.bind("<Escape>", quit_)
    root.geometry("+40+40")
    root.update_idletasks()
    threading.Thread(target=worker, daemon=True).start()
    root.after(2000, poll)
    root.mainloop()


def selftest():
    """Runnable checks: bar widths, reset formatting, window expiry, stale + disk-cache fallback."""
    assert len(bar(0)) == BAR_W and bar(0) == EMPTY * BAR_W
    assert bar(100) == FILLED * BAR_W and bar(50).count(FILLED) == BAR_W // 2
    assert bar(-5) == EMPTY * BAR_W and bar(999) == FILLED * BAR_W

    far = "2030-01-02T03:04:00+00:00"
    assert "02 Jan 2030" in _when(iso=far), "a reset days away must show its date"
    assert _in(iso=far).startswith("in ") and _in(epoch=1) == "now"
    assert _reset_note(iso=far).startswith("resets ") and _reset_note(None) == ""
    assert _when(iso="not-a-date") is None and _when() is None and _in(iso="nope") is None
    assert _err_text(urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)) == \
        "rate limited (HTTP 429)"
    assert _err_text(RuntimeError("nope")) == "RuntimeError: nope"

    # a window that already rolled over loses its bar instead of showing a stale percentage
    rolled = {"label": "5h", "pct": 40, "reset": "2020-01-01T00:00:00+00:00", "note": "x"}
    holder = {"rows": [rolled]}
    _expire(holder)
    assert rolled["pct"] is None and "awaiting fresh numbers" in rolled["note"]
    fresh = _pct_row("5h", 12, _iso(int(time.time()) + 3600))
    assert fresh["pct"] == 12 and "resets" in fresh["note"]

    ok = {"rows": [{"label": "5h", "pct": 18, "note": "n", "reset": far}], "name": "X", "sub": ""}
    boom = lambda: (_ for _ in ()).throw(RuntimeError("429"))

    global FETCHERS, CACHE_FILE
    saved_fetchers, saved_cache = FETCHERS, CACHE_FILE
    import tempfile
    CACHE_FILE = Path(tempfile.mkdtemp(prefix="ai-usage-selftest-")) / "last-good.json"
    try:
        FETCHERS = (("claude", lambda: ok), ("codex", boom))
        _LAST.clear()
        first = collect()
        assert first[0]["rows"] == ok["rows"], "a good fetch keeps its rows"
        assert first[1]["error"], "a failure with nothing cached must report an error"

        # disk cache stands in for a fresh process: memory cleared, provider now fails
        _LAST.clear()
        FETCHERS = (("claude", boom),)
        again = collect()[0]
        assert again["rows"] == ok["rows"], "the disk cache should restore the last good rows"
        assert again["stale"] is True and again["why"] == "RuntimeError: 429"
        assert "stale" in render_text([dict(again, name="X", key="claude")])

        # a provider that never once succeeded reports the failure rather than inventing rows
        FETCHERS = (("deepseek", boom),)
        assert collect()[0]["error"], "no cache plus a failure must surface the error"
    finally:
        FETCHERS, CACHE_FILE = saved_fetchers, saved_cache
        _LAST.clear()
    print("selftest ok")


REPO = Path(__file__).resolve().parent
GTK_APP = REPO / "ai_usage_gtk.py"
GTK_PY = Path("/usr/bin/python3")  # where PyGObject + Gtk-4.0 + Adw-1.0 live


def main():
    args = set(sys.argv[1:])
    if "--selftest" in args:
        selftest()
        return
    if "--text" in args or "--json" in args:
        data = collect()
        print(json.dumps(data, indent=2) if "--json" in args else render_text(data))
        return
    if not args & {"--float", "--framed"} and GTK_PY.is_file() and GTK_APP.is_file():
        # native GTK4/libadwaita app; execv keeps this a single process, no duplicate module state
        os.execv(str(GTK_PY), [str(GTK_PY), str(GTK_APP)])
    build_gui(collect(), framed="--framed" in args)


if __name__ == "__main__":
    main()
