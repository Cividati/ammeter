"""The GTK4 + libadwaita front-end: one Adw.PreferencesGroup per provider.

Needs PyGObject, which on this machine only exists on the system interpreter, so it is launched as

    /usr/bin/python3 -m token_monitor.gtk_app

(the CLI does that for you). The data layer stays stdlib-only, so everything else runs anywhere.
"""
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import APP_ID, APP_NAME, COLOURS, ICON_NAMES, REFRESH_SECONDS, STATUS, __version__
from . import filters, settings
from .core import collect, configured, live_providers, models_seen, problems, visible
from .formatting import money, severity, used_label
from .charts import UsagePage

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

ICONS_DIR = Path(__file__).resolve().parent.parent / "data" / "icons"
STYLESHEET = Path(__file__).with_name("ui.css")

# one line per provider, shown next to the plan name in the group header
BLURB = {
    "copilot": "monthly budget",
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
        self._filters = None
        self.tabs = {}
        self._again = False         # a budget change arrived while a fetch was running
        self._raw = None            # last collect() result, so toggles re-render without a refetch
        self.usage_page = UsagePage()

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
        # three pages: provider rows ("Budget"), charts ("Usage") and "Settings" (budget limit, show/hide)
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
        # keep the Settings page (and anything typed into it) while the user is on it
        self._render(data, keep_filters=self.stack.get_visible_child_name() == "filters")
        if self._again:
            self._again = False
            self.refresh()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ view
    def _render(self, raw=None, keep_filters=False):
        if raw is not None:
            self._raw = raw
        raw = self._raw
        if raw is None:
            return
        data = visible(raw)
        page = Adw.PreferencesPage()
        for entry in data:
            page.add(self._group(entry))
        self.usage_page.set_entry(next((e for e in data if e.get("summary")), None))
        if not (keep_filters and self._filters):     # keep the list (and its scroll spot) while toggling
            self._filters = self._filters_page(raw)
        self._show_pages(page, self.usage_page, self._filters)

        self.title_widget.set_subtitle(f"updated {datetime.now():%H:%M:%S} \u00b7 "
                                       f"{live_providers(data)}/{len(data)} providers")
        found = problems(data)
        if len(found) == 1:
            self.banner.set_title(f"{found[0][0]}: {found[0][1]}")
        elif found:
            self.banner.set_title(f"{len(found)} providers \u00b7 "
                                  + " \u00b7 ".join(sorted({reason for _, reason in found})))
        self.banner.set_revealed(bool(found))

    def _show_pages(self, budget, usage, filters_page):
        """Swap freshly built pages into fixed tabs, so the current tab never changes."""
        if not self.tabs:
            for name, title, icon in (("budget", "Budget", "view-list-symbolic"),
                                      ("usage", "Usage", "utilities-system-monitor-symbolic"),
                                      ("filters", "Settings", "preferences-system-symbolic")):
                self.tabs[name] = Adw.Bin()
                self.stack.add_titled_with_icon(self.tabs[name], name, title, icon)
        for name, page in (("budget", budget), ("usage", usage), ("filters", filters_page)):
            if self.tabs[name].get_child() is not page:
                self.tabs[name].set_child(page)

    def _filters_page(self, raw):
        """The Settings page: budget limit, then switches for providers and models (on means shown)."""
        hidden = filters.load()
        page = Adw.PreferencesPage()
        budget = self._budget_group(raw)
        if budget:
            page.add(budget)
        providers = Adw.PreferencesGroup(
            title="Providers", description="Providers that are set up. Turn one off to hide it.")
        listed = [e for e in raw if configured(e) or e.get("key") in hidden["providers"]]
        for entry in listed:
            providers.add(self._switch("providers", entry["key"], entry["name"], None, hidden))
        if not listed:
            providers.add(Adw.ActionRow(title="No providers are set up yet"))
        page.add(providers)

        seen = models_seen(raw)
        names = sorted(set(seen) | hidden["models"], key=lambda m: (-seen.get(m, {}).get("cost", 0), m))
        if not names:
            status = Adw.StatusPage(title="No models seen yet", icon_name="view-reveal-symbolic",
                                    description="Models show up here once they have been used.")
            status.add_css_class("compact")
            group = Adw.PreferencesGroup(title="Models")
            group.add(status)
        else:
            group = Adw.PreferencesGroup(
                title="Models", description="Models used in the last few weeks. "
                "Hiding one removes it from the charts and rankings; totals stay the same.")
            for name in names:
                total = seen.get(name)
                sub = (f"${total['cost']:,.2f} \u00b7 {total['messages']:,} messages"
                       if total else "not used recently")
                group.add(self._switch("models", name, name, sub, hidden))
        page.add(group)
        return page

    def _budget_group(self, raw):
        """'Budget limit' entry: replaces the limit the portal reports; empty means use the portal's."""
        entry = next((e for e in raw if e.get("summary")), None)
        if not entry:
            return None
        summary = entry["summary"]
        currency = summary["currency"]
        theirs = summary.get("source_budget")
        group = Adw.PreferencesGroup(title="Budget")
        base = f"GitHub quota: {money(theirs, currency)}" if theirs else "No limit reported by the source"
        origin = settings.setting_origin("TOKEN_MONITOR_BUDGET")
        if origin in ("environment", ".env"):
            where = "the environment" if origin == "environment" else "the .env file"
            row = Adw.ActionRow(title=f"Budget limit ({currency})",
                                subtitle=f"{base}. Set in {where} (TOKEN_MONITOR_BUDGET); change it there.")
            row.add_suffix(Gtk.Label(label=money(summary["budget"], currency), valign=Gtk.Align.CENTER))
            group.add(row)
            return group
        group.set_description(base)
        row = Adw.EntryRow(title=f"Budget limit ({currency})", show_apply_button=True,
                           text=settings.config_file().get("TOKEN_MONITOR_BUDGET", ""))
        reset = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER,
                           tooltip_text="Reset to the GitHub value", css_classes=["flat"],
                           sensitive=bool(row.get_text()))
        row.add_suffix(reset)

        def apply(_row):
            text = row.get_text().strip().replace(",", ".")
            row.remove_css_class("error")
            try:
                if text and float(text) <= 0:
                    raise ValueError
            except ValueError:
                row.add_css_class("error")
                group.set_description("Enter a number greater than 0.")
                return
            settings.set_config("TOKEN_MONITOR_BUDGET", text)
            group.set_description(base)
            reset.set_sensitive(bool(text))
            self._budget_changed()

        def clear(_button):
            row.set_text("")
            apply(row)

        row.connect("apply", apply)
        reset.connect("clicked", clear)
        group.add(row)
        return group

    def _budget_changed(self):
        """Fetch again now instead of waiting for the timer."""
        if self._busy:
            self._again = True
        else:
            self.refresh()

    def _switch(self, kind, ident, title, subtitle, hidden):
        row = Adw.SwitchRow(title=GLib.markup_escape_text(title), subtitle=subtitle or "",
                            active=ident not in hidden[kind])

        def toggled(widget, _pspec):
            filters.set_hidden(kind, ident, not widget.get_active())
            GLib.idle_add(lambda: self._render(keep_filters=True))         # rebuilding inside the handler would destroy the row

        row.connect("notify::active", toggled)
        return row

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
            amount = Gtk.Label(label=row["note"], valign=Gtk.Align.CENTER, xalign=1.0,
                               wrap=True, hexpand=True, max_width_chars=40,
                               justify=Gtk.Justification.RIGHT)
            amount.add_css_class("amount")
            if row.get("level"):
                amount.add_css_class(row["level"])
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
            comments="GitHub Copilot spend vs. monthly budget.\n"
                     "Mock data or a user-supplied curl command; see docs/WIRING.md.",
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
