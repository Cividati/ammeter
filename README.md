# Token Monitor

See how much of your **GitHub Copilot monthly budget** you have used, how fast you are spending it, and
when it will run out.

<img src="docs/screenshot-dashboard-dark.png" alt="Token Monitor web dashboard: budget gauge, spent / budget / left, range and chart-type controls, hourly tokens and cost charts">

*The dashboard front page, with sample data.*

## What you get

- **Your budget at a glance:** spent, left and percent used, in credits and dollars, and the date the
  budget lasts until at your current pace.
- **Usage charts:** tokens and estimated cost per hour and per day, your most used models and skills,
  and a time slice that filters every chart.
- **Works offline:** every refresh is saved, so without a connection you still see the last numbers,
  clearly marked as old.
- **Private:** it runs on your machine. Your token is only sent to GitHub and is never stored.

## Quick start

You need Docker, and the [GitHub CLI](https://cli.github.com) logged in (`gh auth login`).

```sh
git clone https://github.com/Cividati/token-monitor.git
cd token-monitor
scripts/web-up.sh -d
```

Open <http://localhost:8080>. A red **MOCK DATA** banner means no GitHub token reached the app: log in
with `gh` and run the script again.

Using GitHub Enterprise? Copy `.env.example` to `.env` and set `TOKEN_MONITOR_GH_HOST` to your host
(`gh auth login -h <host>` first).

No Docker? Run `python3 -m token_monitor.web --host 127.0.0.1 --port 8080`. For a quick look in the
terminal: `python3 bin/token-monitor --text`.


## Setup with an agent

Paste this into a coding agent (OpenCode, Claude Code, Codex, ...) opened in the folder where you want the
project. Replace `<GITHUB_HOST>` with your GitHub Enterprise host, or write `github.com`.

````text
Set up https://github.com/Cividati/token-monitor on this machine and get the web dashboard running.

1. Check the prerequisites and tell me what is missing before installing anything:
   Linux, git, Docker with the compose plugin (or Python 3.10+ if Docker is not available), and the
   GitHub CLI (`gh`). If I need the VPN for <GITHUB_HOST>, tell me to connect it.
2. Clone the repo into ./token-monitor and cd into it.
3. Check `gh auth status -h <GITHUB_HOST>`. If I am not logged in, stop and ask me to run
   `gh auth login -h <GITHUB_HOST>` myself. Never ask me to paste a token into the chat.
4. If <GITHUB_HOST> is not github.com, copy `.env.example` to `.env`, run `chmod 600 .env`, and set
   `TOKEN_MONITOR_GH_HOST=<GITHUB_HOST>`. Do not set `TOKEN_MONITOR_BUDGET`: it overrides the real budget.
5. Run `python3 bin/token-monitor --selftest` (expect "selftest ok"), then `python3 bin/token-monitor --text`.
   The first line should name the plan, not "mock data".
6. Start the dashboard with `scripts/web-up.sh -d` (or `python3 -m token_monitor.web --host 127.0.0.1
   --port 8080` without Docker). Then check `curl -s http://localhost:8080/api/data`: the provider
   must have `"mock": false`.
7. Report: the dashboard URL, the spent / budget / left figures, and anything that looked wrong.

Rules: never print, log or commit a token. Do not commit `.env` or `.token-monitor/`. Keep the dashboard on
127.0.0.1: it has no authentication. Ask before changing anything outside the project folder.

If something fails: a red MOCK DATA banner means no token reached the app (log in with `gh`, then rerun
`scripts/web-up.sh -d`); a 401 means an expired login (`gh auth refresh -h <GITHUB_HOST>`); an offline
banner means <GITHUB_HOST> is unreachable (VPN); empty charts mean the OpenCode database was not found
(`TOKEN_MONITOR_OPENCODE_DB`). More in docs/WEB.md and the in-app "How to set up" page.
````

## Settings

Everything is optional. Copy `.env.example` to `.env` (`chmod 600`) to change a setting. The ones you
are most likely to need:

| Setting | What it does |
|---|---|
| `TOKEN_MONITOR_GH_HOST` | Your GitHub host. Default `github.com`. |
| `TOKEN_MONITOR_GH_TOKEN` | A token to use instead of your `gh` login. |
| `TOKEN_MONITOR_CREDITS_PER_USD` | Credits per dollar. Default 100. |
| `TOKEN_MONITOR_OPENCODE_DB` | Where OpenCode's database is, for the model and skill charts. |
| `TOKEN_MONITOR_HISTORY_DIR` | Where your history is saved. Default `.token-monitor/` in the project. |

The full list, and what each one does, is in [`.env.example`](.env.example) and
[docs/WEB.md](docs/WEB.md).

## How the numbers are calculated

- **Budget, spent and left** come straight from GitHub, so they are exact. They are your personal
  Copilot quota, which every Copilot client shares (editor, CLI, OpenCode and so on).
- **Daily spend** comes from comparing saved snapshots, so it only starts when you first run the app.
- **Tokens, models, skills and cost estimates** come from [OpenCode](https://opencode.ai)'s local
  database. They are estimates and only cover what you did through OpenCode.

The dashboard has a **How it's calculated** page, and [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) goes
into detail.

## Good to know

- The dashboard has **no login**. Keep it on `127.0.0.1`, which is the default, and don't expose it to a
  network.
- It reads an undocumented GitHub endpoint, so it may stop working if GitHub changes it.
- The GTK app, GNOME top-bar indicator, `--float` window and `.deb` package are deprecated and will be
  removed in a future release. The web dashboard is the supported way to use Token Monitor.

More in [docs/](docs/): [WEB.md](docs/WEB.md) (dashboard and Docker),
[WIRING.md](docs/WIRING.md) (other budget sources) and [RELEASING.md](docs/RELEASING.md).

## Licence

MIT, see [LICENSE](LICENSE). GitHub Copilot is a trademark of its owner, used only to say what is measured.
