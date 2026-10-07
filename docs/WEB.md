# Web dashboard

The same numbers as the GTK app, in a browser: budget gauge, tokens and cost charts, hourly and
daily ranges, most used models, and a Settings drawer. It is plain Python (standard library) plus
hand-written HTML, CSS and JavaScript. Nothing is loaded from the internet, so it works offline.

The **How it's calculated** tab (`/#how`) explains, in plain words and with small worked examples,
where each number comes from, which ones are exact and which are estimates, plus a glossary. It is
static HTML in `token_monitor/web/index.html` (so it ships in the Docker image with the rest of
`token_monitor/`), and a box at the top fills in your live values (credits rate, burn rate,
calibration factor) from `/api/data`; if those aren't available the box just stays hidden. When you
change how something is calculated, update that page and `HOW-IT-WORKS.md` together.

**Hourly charts and time slice.** The first charts are tokens per hour and cost per hour (dollars, local
estimate), from the `hourly` rows (31 days kept). Drag across one, or use the From/To boxes
(`?from=2026-10-05T08:00&to=2026-10-06T20:00` also works), to filter every card; Reset clears it.

**Refresh motion.** When a refresh brings new data, changed numbers count to the new value with a brief
blue glow, and bars, lines, dots and pie slices glide to their new size (about 0.4 s, ease-out). Values
that did not change stay still. Nothing animates on first load or when you change range, chart type or
settings, and `prefers-reduced-motion` turns it off.

**Settings.** Providers and models are grouped: each OpenCode provider (by default only `github-copilot`; others
appear if listed in `TOKEN_MONITOR_PROVIDERS`) is a collapsible panel with its own switch and its
models underneath. Turning a provider off hides all its models (their own switches are kept and come back
when it is on again). Totals don't change. Hidden ids are stored in `hidden.json` as before; the API
returns the grouping as `groups` in `GET /api/settings` (the flat `providers` and `models` are still there).

**Chart types.** The Bar / Line / Dots / Pie switch above the charts changes how they are drawn (saved
as `tm-chart-type`; `?chart=pie` forces one for a visit). Pie shows the split of the whole selected range,
not time; models and skills lists use bars, dots or a pie (Line falls back to bars).

**Theme.** The page uses a blue palette in a dark and a light theme. The
sun/moon button in the header switches; the choice is saved in the browser (`localStorage`, key
`tm-theme`). Until you choose, it follows the system setting. `theme.js` applies it before the first
paint, so there is no flash. Add `?theme=light` or `?theme=dark` to a URL to force one for that visit.

## Quick start

With Docker (recommended):

```sh
scripts/web-up.sh -d         # uses your `gh` login, then runs docker compose up --build -d
# open http://localhost:8080
```

`web-up.sh` refuses to start without a token (so you never get sample data by accident) and uses
**host networking** by default: the container shares the host's network, so a VPN-only GitHub
Enterprise API (e.g. `api.github.example.com`) is reachable. The page still listens on `127.0.0.1` only. To use
Docker's isolated bridge network instead (no VPN needed, port published on `127.0.0.1`):

```sh
scripts/web-up.sh --bridge
```

If you run `docker compose` yourself, add `-f docker-compose.host-network.yml` for the same setup,
and set `TOKEN_MONITOR_GH_TOKEN` (in `.env` or the environment), or you get mock data.

Without Docker:

```sh
python3 -m token_monitor.web --host 127.0.0.1 --port 8080
```

Stop the container with `docker compose down`. Settings live in the `tm-data`
volume and survive that (history is the repo's `.token-monitor/`, see below); `docker compose down -v` deletes them.

## Security: there is no authentication

Anyone who can open the page can read your usage and change its settings (budget limit, hidden
items). So:

- `docker-compose.yml` publishes the port on `127.0.0.1` only. Leave it that way unless you must.
- To open it from your LAN, change the mapping to `"0.0.0.0:8080:8080"`, and only on a network you
  trust. Never put it on the public internet.
- The server answers same-origin only (no CORS). POST requests must be `application/json`, and
  a request whose `Origin` differs from its `Host` is refused.
- No token is ever returned by the API or written to the log. Request lines are logged without
  query strings.
- The OpenCode folder is mounted read-only, but the whole folder is visible to the container,
  including OpenCode's own `auth.json`. The app never reads it. If that bothers you, point
  `OPENCODE_DATA_DIR` at a folder that holds only a copy of `opencode.db`.

## How the data and the token get in

| What | How |
|---|---|
| GitHub quota | The container has no `gh`, so the token arrives as `TOKEN_MONITOR_GH_TOKEN`. `scripts/web-up.sh` fills it from `gh auth token -h <host>` and never prints it. It is passed as an environment variable at run time, not stored in the image. |
| Local usage | Your OpenCode folder (`OPENCODE_DATA_DIR`, default `~/.local/share/opencode`) is mounted read-only at `/opencode`. The app reads `opencode.db` with SQLite's read-only mode. |
| Settings | The `tm-data` volume at `/data`: `/data/home/.config/token-monitor/` (config file and `hidden.json`), `/data/home/.cache/`. This is separate from the host's `~/.config/token-monitor`, so a budget override set on the host does not apply in the container. |
| History | The repo's `.token-monitor/` is bind-mounted at `/data/history`, so the host CLI/GTK app and the container share `history.jsonl`. Every successful fetch is appended; when the API is unreachable the dashboard serves the last snapshot as *offline* with an amber banner. |

Source selection: `TOKEN_MONITOR_SOURCE` if set, else `github` whenever `TOKEN_MONITOR_GH_TOKEN` is set (no `gh` binary needed), else `github` if there is history, else sample data.
Sample data is never silent: the plan line reads `MOCK DATA` and a red banner says these are not your numbers.
With a token that stops working or an unreachable API, it shows the last saved numbers from the history with an amber banner, or an error if there is no history yet.

### Why the OpenCode folder is mounted, not just the file

OpenCode's database uses SQLite's write-ahead log: recent rows sit in `opencode.db-wal`, next to
`-shm`. Both must be in the same folder, so the folder is mounted. On a read-only mount SQLite can
normally read it (the app opens it with `mode=ro`, and this was checked against a real database).
If SQLite cannot open it that way, for instance when OpenCode is closed and left no `-shm` file,
the app retries with `immutable=1`, which reads the main file as it is. Rows OpenCode has not yet
moved out of the log may then be missing until it runs again.

The files are often mode 600, so the container runs as your uid. `scripts/web-up.sh` sets
`HOST_UID` and `HOST_GID`. If you run `docker compose` yourself, put them in `.env`:

```
HOST_UID=1000
HOST_GID=1000
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `TOKEN_MONITOR_GH_TOKEN` | empty | GitHub token (set by `web-up.sh`, or put it in `.env`) |
| `TOKEN_MONITOR_GH_HOST` | `github.com`, or the value in `.env` | GitHub host (for GitHub Enterprise) |
| `TOKEN_MONITOR_SOURCE` | empty (auto) | `github`, `mock` or `curl`; set `mock` to run on purpose with sample data |
| `TOKEN_MONITOR_PORT` | `8080` | host port |
| `OPENCODE_DATA_DIR` | `~/.local/share/opencode` | folder mounted at `/opencode` |
| `HOST_UID`, `HOST_GID` | `1000` | user the container runs as |
| `TZ` | `UTC` (`web-up.sh` uses the host's) | time zone for the daily and hourly buckets |

`.env` is read too (optional), so every setting from the README's configuration table works
(`TOKEN_MONITOR_BUDGET`, `TOKEN_MONITOR_PROVIDERS`, ...). One exception: the `curl` source needs
`curl` inside the container, which the slim image does not have. A budget set in `.env` or the
environment shows in Settings as read-only.

## API

| Request | What it does |
|---|---|
| `GET /api/data` | providers (same entries as `--json`, with `daily` and `hourly`), `updated`, `problems`. Cached for 45 seconds. |
| `POST /api/refresh` | fetch again now |
| `GET /api/settings` | budget override state, provider and model switches |
| `POST /api/settings` | `{"budget": "400"}` (empty string clears it) or `{"hide": {"kind": "providers"\|"models", "id": "...", "hidden": true}}` |
| `GET /healthz` | `ok` |

## Troubleshooting

- **"GitHub API unreachable"**: the container cannot reach your GitHub host. Use the default
  `scripts/web-up.sh` (host networking, Linux), not `--bridge`, and check that the VPN is up.
- **MOCK DATA banner**: no token reached the container (typically `docker compose up` run by hand). Run
  `gh auth login -h <host>` and start with `scripts/web-up.sh`, or set `TOKEN_MONITOR_GH_TOKEN` in `.env`.
- **No charts for models / empty hourly view**: the OpenCode database was not found or not
  readable. Check `OPENCODE_DATA_DIR`, and that `HOST_UID` matches the owner of the files.
- **Hours look shifted**: set `TZ` to your time zone.
- **Port already in use**: set `TOKEN_MONITOR_PORT=8090`.
- **Logs**: `docker compose logs -f token-monitor`.

The dashboard also has a **How to set up** tab (`/#setup`): prerequisites, quick start, the settings
table, data locations, host vs bridge network, offline behaviour, security and troubleshooting. Keep it
in step with this file, `.env.example` and `scripts/web-up.sh`.
