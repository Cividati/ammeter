"""The GTK4 + libadwaita front-end: one Adw.PreferencesGroup per provider.

Needs PyGObject, which on this machine only exists on the system interpreter, so it is launched as

    /usr/bin/python3 -m ammeter.gtk_app

(the CLI does that for you). The data layer stays stdlib-only, so everything else runs anywhere.
"""
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import APP_ID, APP_NAME, COLOURS, ICON_NAMES, REFRESH_SECONDS, STATUS, __version__
from . import filters
from .core import collect, configured, live_providers, problems, visible
from .formatting import severity, used_label

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

ICONS_DIR = Path(__file__).resolve().parent.parent / "data" / "icons"
STYLESHEET = Path(__file__).with_name("ui.css")

# one line per provider, shown next to the plan name in the group header
BLURB = {
    "claude": "5-hour and weekly quota",
    "codex": "plan quota",
    "openrouter": "prepaid credit",
    "deepseek": "prepaid credit",
}


def load_stylesheet():
    """ui.css holds the structure, __init__.py holds the palette ({name} placeholders)."""
    css = STYLESHEET.read_text()
    for name, value in dict(COLOURS, **STATUS).items():
        css = css.replace("{" + name + "}", value)
    return css


class UsageWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=520, default_height=800)
        self._busy = False
        self._raw = None            # last collect() result: toggles re-render without a refetch
        self._filters = None

        self.spinner = Adw.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.title_widget = Adw.WindowTitle(title=APP_NAME, subtitle="loading\u2026")

        header = Adw.HeaderBar()
        header.set_title_widget(self.title_widget)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh now")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_start(refresh)
        header.pack_start(self.spinner)

        menu = Gio.Menu()
        menu.append("Refresh now", "win.refresh")
        menu.append("About " + APP_NAME, "win.about")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main menu"))

        self.banner = Adw.Banner(button_label="Refresh")
        self.banner.connect("button-clicked", lambda *_: self.refresh())
        self.toolbar = Adw.ToolbarView()
        self.toolbar.add_top_bar(header)
        self.toolbar.add_top_bar(self.banner)
        # two pages: the provider rows ("Budget") and the show/hide switches ("Filters")
        self.stack = Adw.ViewStack()
        self.toolbar.set_content(self.stack)
        self.toolbar.add_bottom_bar(Adw.ViewSwitcherBar(stack=self.stack, reveal=True))
        self.set_content(self.toolbar)

        for name, callback in (("refresh", lambda *_: self.refresh()),
                               ("about", lambda *_: self.about())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
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
        """Blocking HTTP, off the main loop; hand the result back on the main loop."""
        GLib.idle_add(self._apply, collect())

    def _apply(self, data):
        self._busy = False
        self.spinner.set_visible(False)
        self._render(data)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ view
    def _render(self, raw=None, keep_filters=False):
        if raw is not None:
            self._raw = raw
        if self._raw is None:
            return
        data = visible(self._raw)
        page = Adw.PreferencesPage()
        for entry in data:
            page.add(self._group(entry))
        if not (keep_filters and self._filters):     # keep the list (and its scroll spot) while toggling
            self._filters = self._filters_page(self._raw)
        self._show_pages(page, self._filters)

        self.title_widget.set_subtitle(f"updated {datetime.now():%H:%M:%S} \u00b7 "
                                       f"{live_providers(data)}/{len(data)} providers")
        found = problems(data)
        if len(found) == 1:
            self.banner.set_title(f"{found[0][0]}: {found[0][1]}")
        elif found:
            self.banner.set_title(f"{len(found)} providers \u00b7 "
                                  + " \u00b7 ".join(sorted({reason for _, reason in found})))
        self.banner.set_revealed(bool(found))

    def _show_pages(self, budget, filters_page):
        """Swap in freshly built pages, keeping whichever one the user was looking at."""
        current = self.stack.get_visible_child_name()
        while (child := self.stack.get_first_child()) is not None:
            self.stack.remove(child)
        self.stack.add_titled_with_icon(budget, "budget", "Budget", "view-list-symbolic")
        self.stack.add_titled_with_icon(filters_page, "filters", "Filters", "view-reveal-symbolic")
        if current:
            self.stack.set_visible_child_name(current)

    def _filters_page(self, raw):
        """One switch per provider that is set up; on means shown."""
        hidden = filters.load()
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Providers", description="Providers that are set up. Turn one off to hide it.")
        listed = [e for e in raw if configured(e) or e["key"] in hidden]
        for entry in listed:
            row = Adw.SwitchRow(title=entry["name"], active=entry["key"] not in hidden)
            row.connect("notify::active", self._toggled, entry["key"])
            group.add(row)
        if not listed:
            group.add(Adw.ActionRow(title="No providers are set up yet"))
        page.add(group)
        return page

    def _toggled(self, row, _pspec, key):
        filters.set_hidden(key, not row.get_active())
        GLib.idle_add(lambda: self._render(keep_filters=True))   # not inside the handler: it rebuilds pages

    def _group(self, entry):
        notes = (entry.get("sub"), BLURB.get(entry.get("key")),
                 "stale data" if entry.get("stale") else None)
        group = Adw.PreferencesGroup(title=entry["name"],
                                     description=" \u00b7 ".join(n for n in notes if n) or None)
        if entry.get("error"):
            row = Adw.ActionRow(title="Unavailable", subtitle=entry["error"])
            row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
            group.add(row)
            return group
        for row in entry["rows"]:
            group.add(self._row(entry["key"], row))
        return group

    def _row(self, key, row):
        """A quota row gets a bar and a percentage; a balance row just gets the amount."""
        widget = Adw.ActionRow(title=row["label"], subtitle_lines=2)
        badge = Gtk.Image.new_from_icon_name(ICON_NAMES.get(key, "dialog-question-symbolic"))
        badge.set_valign(Gtk.Align.CENTER)
        badge.add_css_class("badge")
        badge.add_css_class(key)
        widget.add_prefix(badge)

        pct = row["pct"]
        if pct is None:
            amount = Gtk.Label(label=row["note"], valign=Gtk.Align.CENTER, xalign=1.0)
            amount.add_css_class("amount")
            widget.add_suffix(amount)
            return widget

        progress = Gtk.ProgressBar(fraction=pct / 100.0, valign=Gtk.Align.CENTER)
        progress.add_css_class("quota")
        level = severity(pct)
        if level:
            progress.add_css_class(level)
        label = Gtk.Label(label=used_label(pct), valign=Gtk.Align.CENTER, xalign=1.0, width_chars=9)
        label.add_css_class("pct")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(progress)
        box.append(label)
        widget.add_suffix(box)
        # a long reset note wraps mid-phrase; break it where the countdown starts instead
        note = row["note"] or ""
        if len(note) > 27:
            note = note.replace(" \u00b7 ", "\n\u00b7 ", 1)
        widget.set_subtitle(note)
        return widget

    def about(self):
        Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            developer_name="Rubens Cividati",
            comments="Claude Code, Codex, OpenRouter and DeepSeek usage and balances.\n"
                     "Reads the logins the CLIs already store locally; nothing leaves this machine.",
            license_type=Gtk.License.MIT_X11,
        ).present(self)


def run():
    flags = Gio.ApplicationFlags.DEFAULT_FLAGS
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        # no session bus (headless/xvfb): a unique GApplication cannot register and would exit
        flags = Gio.ApplicationFlags.NON_UNIQUE
    app = Adw.Application(application_id=APP_ID, flags=flags)

    def on_activate(_app):
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS_DIR))
        Gtk.Window.set_default_icon_name(APP_ID)
        stylesheet = Gtk.CssProvider()
        stylesheet.load_from_string(load_stylesheet())
        Gtk.StyleContext.add_provider_for_display(display, stylesheet,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        UsageWindow(_app).present()

    app.connect("activate", on_activate)
    return app.run(None)


if __name__ == "__main__":
    sys.exit(run())
