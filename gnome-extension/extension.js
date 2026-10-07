// Token Monitor - top-bar indicator for GitHub Copilot spend vs. monthly budget.
// Every number comes from `token-monitor --json`; this file only renders it.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';
import { renderLines } from './format.js';

// Launcher location: installed by scripts/install.sh (symlink) or the .deb (/usr/bin).
const SCRIPT = GLib.file_test('/usr/bin/token-monitor', GLib.FileTest.EXISTS)
    ? '/usr/bin/token-monitor' : GLib.get_home_dir() + '/.local/bin/token-monitor';
const INTERVAL_S = 120;
const ICON_OK = 'speedometer-symbolic';
const ICON_ALERT = 'dialog-warning-symbolic';

export default class TokenMonitorExtension extends Extension {
    enable() {
        this._theme = St.ThemeContext.get_for_stage(global.stage).get_theme();
        this._css = this.dir.get_child('stylesheet.css');
        this._theme.load_stylesheet(this._css);

        this._icon = new St.Icon({ icon_name: ICON_OK, style_class: 'system-status-icon token-monitor-ok' });
        this._button = new PanelMenu.Button(0.5, 'Token Monitor', false);
        this._button.add_child(this._icon);
        Main.panel.addToStatusArea('token-monitor', this._button, 0, 'right');

        this._menu = this._button.menu;
        this._menu.removeAll();
        this._section = new PopupMenu.PopupMenuSection();
        this._menu.addMenuItem(this._section);
        this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const refresh = new PopupMenu.PopupMenuItem('Refresh now');
        refresh.connect('activate', () => this._fetch());
        this._menu.addMenuItem(refresh);
        this._status = new PopupMenu.PopupMenuItem('loading...', { reactive: false });
        this._menu.addMenuItem(this._status);

        this._busy = false;
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, INTERVAL_S, () => {
            this._fetch();
            return GLib.SOURCE_CONTINUE;
        });
        this._fetch();
    }

    disable() {
        if (this._timer) {
            GLib.Source.remove(this._timer);
            this._timer = null;
        }
        if (this._theme && this._css)
            this._theme.unload_stylesheet(this._css);
        this._theme = null;
        this._css = null;
        this._section = null;
        this._status = null;
        this._menu = null;
        this._icon = null;
        if (this._button) {
            this._button.destroy();
            this._button = null;
        }
    }

    async _fetch() {
        if (this._busy)
            return;
        this._busy = true;
        try {
            const proc = Gio.Subprocess.new([SCRIPT, '--json'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
            const [stdout] = await proc.communicate_utf8_async(null, null);
            if (!proc.get_successful()) {
                this._status.label.text = 'token-monitor failed to run';
                return;
            }
            this._render(JSON.parse(stdout));
        } catch (e) {
            logError(e, 'token-monitor');
            if (this._status)
                this._status.label.text = 'token-monitor error: ' + e.message;
        } finally {
            this._busy = false;
        }
    }

    _render(data) {
        this._section.removeAll();
        const { blocks, worst, alert } = renderLines(data);

        for (const block of blocks) {
            const item = new PopupMenu.PopupMenuItem('', { reactive: false });
            item.label.add_style_class_name('token-monitor-row');
            item.label.clutter_text.set_markup(block.markup);
            this._section.addMenuItem(item);
        }

        this._icon.icon_name = alert ? ICON_ALERT : ICON_OK;
        this._icon.style_class = 'system-status-icon ' +
            (alert ? 'token-monitor-crit' : worst >= 70 ? 'token-monitor-warn' : 'token-monitor-ok');
        this._status.label.text = `updated ${new Date().toLocaleTimeString()} \u00b7 every ${INTERVAL_S}s`;
    }
}
