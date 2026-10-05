# agents-usage

Claude Code, Codex, OpenRouter and DeepSeek usage, credits and quota limits, in two surfaces on
Linux:

- `ai-usage` — a small always-on-top desktop window (drag to move, right-click for the menu).
- `gnome-extension/` — a GNOME top-bar indicator that shows the same numbers in a click menu.

Both read their numbers from the same place: the logins the CLIs already store on this machine.
Nothing is uploaded anywhere, and no extra account or API key beyond the providers' own is needed.

## Layout

```
ai-usage                    the data + window app (stdlib Python, no dependencies)
gnome-extension/
  metadata.json             uuid: ai-usage@cividati
  extension.js              PanelMenu button, 120s timer, Gio.Subprocess -> ai-usage --json
  format.js                 pure formatting (bars, Pango markup, severity) — no shell imports
  stylesheet.css            panel icon colours + monospace menu rows
scripts/grab-x11.py         screenshot an X screen to PNG (headless verification helper)
tests/
  test-format.js            runs format.js in gjs against real `ai-usage --json`
  glyph-check.py            renders icon candidates in tk so tofu glyphs are obvious
```

## Usage

```sh
ai-usage                     # floating window
ai-usage --framed            # same window with normal WM decorations (debug/screenshot aid)
ai-usage --text              # print to stdout and exit
ai-usage --json              # machine-readable, this is what the GNOME extension consumes
ai-usage --selftest          # bar widths, reset formatting, stale fallback
```

## How it is wired in

Sources live here; the installed paths are symlinks to this checkout, so edits take effect
immediately and there is nothing to re-copy.

```sh
ln -sfn ~/Development/agents-usage/ai-usage ~/.local/bin/ai-usage
ln -sfn ~/Development/agents-usage/gnome-extension \
        ~/.local/share/gnome-shell/extensions/ai-usage@cividati
gsettings get org.gnome.shell enabled-extensions   # must contain ai-usage@cividati
```

The extension is loaded by GNOME Shell at session start. On Wayland the shell cannot be reloaded
in place, so after enabling it once, log out and back in. Verify with:

```sh
gnome-extensions info ai-usage@cividati            # State: ACTIVE
journalctl --user -b -o cat /usr/bin/gnome-shell | grep -i ai-usage
```

If the shell refuses a symlinked extension directory, replace it with a copy and re-run the
install from the checkout when you change something:

```sh
rm ~/.local/share/gnome-shell/extensions/ai-usage@cividati
cp -r ~/Development/agents-usage/gnome-extension \
      ~/.local/share/gnome-shell/extensions/ai-usage@cividati
```

## Data sources

| Provider | Credential | Endpoint | Fields used |
|---|---|---|---|
| Claude | `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`) | `GET api.anthropic.com/api/oauth/usage`, headers `anthropic-beta: oauth-2025-04-20` | `five_hour`, `seven_day`, `seven_day_opus/sonnet` → `utilization`, `resets_at` |
| Codex | `~/.codex/auth.json` (`tokens.access_token` + `tokens.account_id`) | `GET chatgpt.com/backend-api/wham/usage`, header `chatgpt-account-id` | `rate_limit.primary_window/secondary_window.used_percent`, `reset_at`, `plan_type`, `credits` |
| OpenRouter | `OPENROUTER_API_KEY` in `~/.hermes/.env` | `GET openrouter.ai/api/v1/credits` + `/api/v1/key` | `total_credits`, `total_usage`, `usage_daily`, `usage_monthly` |
| DeepSeek | `DEEPSEEK_API_KEY` in `~/.hermes/.env` | `GET api.deepseek.com/user/balance` | `is_available`, `balance_infos[].total_balance`, `currency` |

Adding a provider = one `fetch_<name>()` returning
`{"name", "rows": [{"label", "pct"|None, "note"}], "sub"}`, appended to `FETCHERS`, plus a colour
and icon entry (`ACCENT`/`ICONS` in `ai-usage`, `COLOR`/`ICON` in `format.js`).

## Pitfalls found the hard way

- **Claude's usage endpoint rate-limits hard.** Rapid polling returns `HTTP 429 Too Many Requests`
  with no `Retry-After`; it cleared in roughly 2–3 minutes here. Hence a 120s interval and a
  last-good cache: a failed poll keeps the previous numbers and marks the block `stale` instead of
  blanking it.
- **The Codex usage endpoint is not a public API** (`chatgpt.com/backend-api/wham/usage`), and
  `~/.codex/auth.json` and `~/.claude/.credentials.json` are secrets — never log or commit them.
- **GNOME Shell 50 API facts**, checked against the installed shell (`libshell-18.so` gresource):
  `PopupMenuItem` has no `setLabel()` (use `item.label.text`), `addToStatusArea(role, indicator,
  position, box)` throws on a duplicate role, and the role is freed when the indicator is destroyed
  (so `disable()` must destroy the button).
- **`Gio.Subprocess.communicate_utf8_async` is not promisified by default** in an extension: call
  `Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish')`
  first. Promisified, it resolves to `[stdout, stderr]` (no leading boolean).
- **Icon glyphs:** `U+2B22 ⬢`, `U+2B21 ⬡`, `U+2B24 ⬤` and `U+29BF` are missing from the DejaVu
  fonts here and render as tofu. The icons in use (`U+2733 ✳`, `U+25C6 ◆`, `U+21C4 ⇄`, `U+25C9 ◉`)
  are present in both DejaVu Sans and DejaVu Sans Mono. Re-check with `tests/glyph-check.py`.
- **Progress bars need a font with block elements.** DejaVu has `U+2593`/`U+2591`; Ubuntu Mono does
  not. The GNOME menu rows therefore pin `font-family: "DejaVu Sans Mono", monospace`.

## Testing

```sh
ai-usage --selftest                      # python side
gjs -m tests/test-format.js              # extension formatting, real data, real shell JS engine
```

Headless visual check (no desktop screenshots needed, and gnome-shell's Screenshot D-Bus call is
denied for unprivileged callers here):

```sh
python3 -m venv .venv && .venv/bin/pip install python-xlib    # only for the grab helper
xvfb-run -a -s "-screen 0 700x460x24" bash -c \
  './ai-usage --framed & sleep 12; .venv/bin/python scripts/grab-x11.py /tmp/shot.png; kill %1'
```

A plain `ffmpeg -f x11grab` of `:0.0` returns an all-black frame (XWayland root is not painted by
the compositor), which is why the helper goes through `Xvfb` instead.
