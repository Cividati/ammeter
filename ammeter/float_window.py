"""The tkinter front-end: a frameless, always-on-top window.

GTK4 has no keep-above API on Wayland, so this stays as the pinned-window option
(``ammeter --float``). It is also the fallback when PyGObject is not installed.
"""
import queue
import threading
import time
import tkinter as tk
from datetime import datetime

from . import COLOURS, MARKS, REFRESH_SECONDS, STATUS
from .core import collect
from .formatting import bar, severity, used_label

BACKGROUND = "#0b0b11"
RULE = "#23232f"
DIM = "#7a7a8c"
FOREGROUND = "#e6e6f0"
MONO = "DejaVu Sans Mono"
SANS = "DejaVu Sans"
BAR_CELLS = 12


def _rule(parent, row, tk):
    line = tk.Frame(parent, bg=RULE, height=1)
    line.grid(row=row, column=0, sticky="we", padx=14, pady=(0, 6))
    return line


def run(data, refresh_seconds=REFRESH_SECONDS, framed=False):
    """Show the window and block until it is closed."""
    root = tk.Tk()
    root.title("Ammeter")
    if not framed:
        root.overrideredirect(True)
        root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.96)
    except tk.TclError:
        pass
    root.configure(bg=BACKGROUND)

    wrap = tk.Frame(root, bg=BACKGROUND, highlightbackground=RULE, highlightthickness=1)
    wrap.pack(fill="both", expand=True)

    header = tk.Frame(wrap, bg=BACKGROUND)
    header.grid(row=0, column=0, sticky="we", padx=14, pady=(12, 5))
    header.columnconfigure(2, weight=1)
    dot = tk.Label(header, text="\u25cf", bg=BACKGROUND, fg=STATUS["ok"], font=(SANS, 11))
    dot.grid(row=0, column=0, sticky="w", padx=(0, 7))
    tk.Label(header, text="AI USAGE", bg=BACKGROUND, fg=FOREGROUND, font=(MONO, 11, "bold"),
             anchor="w").grid(row=0, column=1, sticky="w")
    tk.Label(header, text=f"\u21bb {refresh_seconds}s", bg=BACKGROUND, fg=DIM, font=(MONO, 9),
             anchor="e").grid(row=0, column=2, sticky="e")
    _rule(wrap, 1, tk)

    body = tk.Frame(wrap, bg=BACKGROUND)
    body.grid(row=2, column=0, sticky="we")
    footer = tk.Label(wrap, text="", bg=BACKGROUND, fg=DIM, font=(MONO, 9), anchor="w")
    footer.grid(row=3, column=0, sticky="we", padx=14, pady=(10, 10))

    def value_colour(pct, base):
        level = severity(pct)
        return STATUS[level] if level else base

    def redraw(entries):
        for widget in body.winfo_children():
            widget.destroy()
        body.columnconfigure(0, minsize=28)
        line = 0
        worst = 0
        for entry in entries:
            key = entry.get("key")
            accent = COLOURS.get(key, FOREGROUND)
            stale = bool(entry.get("stale"))
            tk.Label(body, text=MARKS.get(key, "\u25cf"), bg=BACKGROUND, fg=accent,
                     font=(SANS, 15)).grid(row=line, column=0, sticky="w", padx=(14, 6), pady=(5, 0))
            headline = tk.Frame(body, bg=BACKGROUND)
            headline.grid(row=line, column=1, columnspan=3, sticky="w", pady=(5, 0))
            tk.Label(headline, text=entry["name"], bg=BACKGROUND, fg=accent,
                     font=(MONO, 11, "bold")).pack(side="left")
            note_parts = [entry.get("sub") or ""]
            if stale:
                note_parts.append("\u2014 stale" + (f" ({entry['why']})" if entry.get("why") else ""))
            note = "   ".join(part for part in note_parts if part)
            if note:
                tk.Label(headline, text=note, bg=BACKGROUND,
                         fg=STATUS["warn"] if stale else DIM,
                         font=(MONO, 9)).pack(side="left", padx=(9, 0))
            line += 1

            if entry.get("error") or not entry["rows"]:
                tk.Label(body, text="\u26a0 " + (entry.get("error") or "no data"), bg=BACKGROUND,
                         fg=STATUS["crit"], font=(MONO, 10), anchor="w").grid(
                    row=line, column=1, columnspan=3, sticky="w", padx=(0, 14))
                line += 2
                continue

            for row in entry["rows"]:
                pct = row["pct"]
                tk.Label(body, text=row["label"], bg=BACKGROUND, fg=DIM, font=(MONO, 10),
                         anchor="w", width=9).grid(row=line, column=1, sticky="w")
                if pct is None:
                    tk.Label(body, text=row["note"], bg=BACKGROUND, fg=FOREGROUND,
                             font=(MONO, 10), anchor="w").grid(
                        row=line, column=2, columnspan=2, sticky="w", padx=(BAR_CELLS + 4, 14))
                else:
                    worst = max(worst, int(pct))
                    tk.Label(body, text=bar(pct), bg=BACKGROUND, width=BAR_CELLS, anchor="w",
                             fg=value_colour(pct, accent), font=(MONO, 10)).grid(
                        row=line, column=2, sticky="w")
                    tk.Label(body, text=f"{used_label(pct):>9}   {row['note']}", bg=BACKGROUND,
                             fg=FOREGROUND, font=(MONO, 10), anchor="w").grid(
                        row=line, column=3, sticky="w", padx=(4, 14))
                line += 1
            line += 1
        dot.config(fg=STATUS[severity(worst)] if severity(worst) else STATUS["ok"])

    redraw(data)

    drag = {}

    def press(event):
        drag["x"], drag["y"] = event.x_root - root.winfo_x(), event.y_root - root.winfo_y()

    def move(event):
        if "x" in drag:
            root.geometry(f"+{event.x_root - drag['x']}+{event.y_root - drag['y']}")

    def close(*_):
        root.destroy()

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="Refresh now", command=lambda: refresh())
    menu.add_command(label="Quit", command=close)

    def bind(widget):
        widget.bind("<Button-1>", press)
        widget.bind("<B1-Motion>", move)
        widget.bind("<ButtonRelease-1>", lambda event: drag.clear())
        widget.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))
        for child in widget.winfo_children():
            bind(child)

    incoming = queue.Queue()

    def worker():
        while True:
            incoming.put(collect())
            time.sleep(refresh_seconds)

    def refresh():
        footer.config(text="refreshing\u2026")
        threading.Thread(target=lambda: incoming.put(collect()), daemon=True).start()

    def pump():
        try:
            while True:
                entries = incoming.get_nowait()
                redraw(entries)
                footer.config(text=f"updated {datetime.now():%H:%M:%S}   \u00b7   "
                                   f"{sum(1 for e in entries if e['rows'])}/{len(entries)} providers"
                                   "   \u00b7   right-click for menu")
        except queue.Empty:
            pass
        root.after(2000, pump)

    bind(wrap)
    root.bind("<Escape>", close)
    root.geometry("+40+40")
    root.update_idletasks()
    threading.Thread(target=worker, daemon=True).start()
    root.after(2000, pump)
    root.mainloop()
