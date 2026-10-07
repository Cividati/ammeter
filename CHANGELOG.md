# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Deprecated
- The Linux standalone front-ends (GTK4 app, GNOME top-bar extension, `--float` window) and the `.deb`
  package will be discontinued in a future release. The web dashboard is the supported front-end.

### Changed
- README: a web dashboard screenshot (sample data) and a "Setup with an agent" prompt.

## [0.1.0] - 2026-10-07

First public release. Forked from [Cividati/ammeter](https://github.com/Cividati/ammeter); the
Claude, Codex, OpenRouter and DeepSeek providers were removed and the app is Copilot-only.

### Added
- GitHub Copilot premium-request quota (`github` source, via `gh` or `TOKEN_MONITOR_GH_TOKEN`), shown as
  credits or dollars (`TOKEN_MONITOR_CREDITS_PER_USD`), with percent used, what is left and a
  "lasts until" forecast. Works with GitHub Enterprise hosts (`TOKEN_MONITOR_GH_HOST`).
- Optional `curl` source that replays a captured budget-portal request, and a labelled `mock` source.
- Append-only snapshot history and an offline fallback: the last snapshot is shown as stale with a local
  estimate on top.
- Local usage from OpenCode's SQLite database (read-only): tokens, estimated cost, messages, models and
  skills, per day and per hour; exact daily spend from history deltas; calibration of the cost estimate.
- Web dashboard (stdlib server, Docker via `scripts/web-up.sh`): budget gauge, tokens/cost charts,
  24h to 30-day ranges, hourly charts with a time slice, Bar/Line/Dots/Pie chart types, most used models
  and skills, Settings drawer (budget override, hidden providers/models), light/dark blue theme,
  refresh animations, and in-app "How it's calculated" and "How to set up" pages.
- GTK4/libadwaita app with Budget and Usage pages, GNOME Shell top-bar indicator, tkinter float window,
  and CLI (`--text`, `--json`, `--plot`, `--selftest`).
- `scripts/install.sh` and a Debian package builder (`packaging/build-deb.sh`).
- Documentation: `docs/HOW-IT-WORKS.md`, `docs/WEB.md`, `docs/WIRING.md`, `docs/RELEASING.md`.

### Known limitations
- The quota is your personal seat quota, not an organisation budget, and comes from an undocumented
  GitHub endpoint that may change.
- Cost and per-model figures are estimates (OpenCode's cost data, calibrated); only OpenCode usage is
  broken down.
- The web dashboard has no authentication and is meant for localhost only.
- Linux focus (GNOME, GTK4, Docker host networking). Installed from the `.deb`, history goes to
  `~/.local/share/token-monitor/` and settings to `~/.config/token-monitor/config` (no `.env` next to the code).
- The `curl` source is unavailable in the Docker image.

[0.1.0]: https://github.com/Cividati/token-monitor/releases/tag/v0.1.0
