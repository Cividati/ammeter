#!/usr/bin/python3
"""ai-usage-gtk - GTK4 + libadwaita front-end for ai-usage.

Native Ubuntu/GNOME look: headerbar, Adw.PreferencesPage groups per provider, real progress bars,
a warning banner when data is stale, and an Adw.AboutDialog.

Runs on the system interpreter (/usr/bin/python3) because that is where PyGObject, Gtk-4.0 and
Adw-1.0 are installed; the data layer in ai_usage.py is stdlib-only.
"""
import os
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import ai_usage as core  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

APP_ID = "dev.cividati.AIUsage"
TITLE = "AI Usage"

# provider accents match the tk front-end and the GNOME extension
ACCENT = {"claude": "#d97757", "codex": "#10a37f", "openrouter": "#c9a227", "deepseek": "#5b7cfa"}
MARK = {"claude": "\u2733", "codex": "\u25c6", "openrouter": "\u21c4", "deepseek": "\u25c9"}

CSS = """
.badge {
  border-radius: 999px;
  min-width: 26px; min-height: 26px;
  font-family: "DejaVu Sans";
  font-size: 13px;
  color: #ffffff;
}
%s
progressbar.quota { min-width: 110px; }
progressbar.quota progress { background-color: #8ab4f8; }
progressbar.quota.warn progress { background-color: #f9c74f; }
progressbar.quota.crit progress { background-color: #ff5555; }
.amount { font-family: "DejaVu Sans Mono"; font-size: 13px; }
.pct { font-family: "DejaVu Sans Mono"; font-size: 13px; }
""" % "\n".join(f".badge.{k} {{ background-color: {v}; }}" for k, v in ACCENT.items())


def _severity(pct):
    return "crit" if pct >= 90 else "warn" if pct >= 70 else ""


# one-line explanation per provider, shown next to the plan name in the group header
BLURB = {
    "claude": "5-hour and weekly quota",
    "codex": "plan quota",
    "openrouter": "prepaid credit",
    "deepseek": "prepaid credit",
}


class UsageWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=TITLE, default_width=520, default_height=880)
        self._busy = False

        self.spinner = Adw.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.title_widget = Adw.WindowTitle(title=TITLE, subtitle="loading\u2026")

        header = Adw.HeaderBar()
        header.set_title_widget(self.title_widget)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh now")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_start(refresh)
        header.pack_start(self.spinner)

        menu = Gio.Menu()
        menu.append("Refresh now", "win.refresh")
        menu.append("About AI Usage", "win.about")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main menu"))

        self.banner = Adw.Banner(button_label="Refresh")
        self.banner.connect("button-clicked", lambda *_: self.refresh())
        self.toolbar = Adw.ToolbarView()
        self.toolbar.add_top_bar(header)
        self.toolbar.add_top_bar(self.banner)
        self.set_content(self.toolbar)

        for name, cb in (("refresh", lambda *_: self.refresh()), ("about", lambda *_: self.about())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)

        GLib.timeout_add_seconds(core.REFRESH_S, self._tick)
        self.refresh()

    # ------------------------------------------------------------------ data
    def _tick(self):
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.spinner.set_visible(True)
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        """Runs off the main loop: collect() does blocking HTTP."""
        GLib.idle_add(self._apply, core.collect())

    def _apply(self, data):
        self._busy = False
        self.spinner.set_visible(False)
        self._render(data)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ ui
    def _render(self, data):
        page = Adw.PreferencesPage()
        problems = []
        live = 0

        for d in data:
            rows = d.get("rows") or []
            if not d.get("error"):
                live += 1
            if d.get("error"):
                problems.append((d["name"], d["error"]))
            elif d.get("stale"):
                problems.append((d["name"], f"{d['why']} \u2014 cached numbers"))
            elif d.get("reached"):
                problems.append((d["name"], "plan limit reached"))

            notes = (d.get("sub"), BLURB.get(d.get("key")), "stale data" if d.get("stale") else None)
            group = Adw.PreferencesGroup(title=d["name"],
                                         description=" \u00b7 ".join(n for n in notes if n) or None)

            if d.get("error"):
                row = Adw.ActionRow(title="Unavailable", subtitle=d["error"])
                row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
                group.add(row)
            else:
                for r in rows:
                    group.add(self._row(d["key"], r, r.get("pct")))
            page.add(group)

        self.toolbar.set_content(page)
        self.title_widget.set_subtitle(f"updated {datetime.now():%H:%M:%S} \u00b7 "
                                       f"{live}/{len(data)} providers")
        self.banner.set_revealed(bool(problems))
        if len(problems) == 1:
            self.banner.set_title(f"{problems[0][0]}: {problems[0][1]}")
        elif problems:
            self.banner.set_title(f"{len(problems)} providers \u00b7 "
                                  + " \u00b7 ".join(sorted({r for _, r in problems})))

    def _row(self, key, r, pct):
        row = Adw.ActionRow(title=r["label"], subtitle_lines=2)
        badge = Gtk.Label(label=MARK.get(key, "\u25cf"), valign=Gtk.Align.CENTER)
        badge.add_css_class("badge")
        badge.add_css_class(key)
        row.add_prefix(badge)

        if pct is None:
            amount = Gtk.Label(label=r["note"], valign=Gtk.Align.CENTER, xalign=1.0)
            amount.add_css_class("amount")
            row.add_suffix(amount)
            return row
        else:
            sev = _severity(pct)
            bar = Gtk.ProgressBar(fraction=pct / 100.0, valign=Gtk.Align.CENTER)
            bar.add_css_class("quota")
            if sev:
                bar.add_css_class(sev)
            label = Gtk.Label(label=f"{int(pct)}%", valign=Gtk.Align.CENTER,
                              xalign=1.0, width_chars=4)
            label.add_css_class("pct")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.append(bar)
            box.append(label)
            row.add_suffix(box)
        row.set_subtitle(r["note"] or "")
        return row

    def about(self):
        Adw.AboutDialog(
            application_name=TITLE,
            application_icon="utilities-system-monitor-symbolic",
            version="0.2",
            developer_name="Rubens Cividati",
            comments="Claude Code, Codex, OpenRouter and DeepSeek usage and balances.\n"
                     "Reads the logins the CLIs already store locally; nothing leaves this machine.",
            license_type=Gtk.License.MIT_X11,
        ).present(self)


def run():
    app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def on_activate(_app):
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        UsageWindow(_app).present()

    app.connect("activate", on_activate)
    return app.run(None)


if __name__ == "__main__":
    sys.exit(run())
