# How Token Monitor works

A short tour of where the numbers come from, where things are stored, and what they can and
cannot tell you.

## Data flow

```
GitHub quota endpoint ──► snapshot ──► history.jsonl ──► daily spend (deltas)
                                              │
OpenCode database (read-only) ──► per-day tokens, cost, models ──┤
                                              ▼
                              core.collect() ──► core.visible() ──► app / --text / --json / float window
```

1. **GitHub quota (the budget).** With the default `github` source, the app asks
   `copilot_internal/user` on your GitHub host, using the token from `gh auth token`. Only the
   numbers and the plan name are kept: credits spent (entitlement minus `remaining`, which is how an organisation budget portal showing the same quota would compute it; the API's `credits_used` lags slightly and is kept only as `reported` for diagnosis), the plan's entitlement (the budget), the
   reset date and the plan label. Nothing else from the response is stored or logged. Credits are
   shown as dollars (`TOKEN_MONITOR_CREDITS_PER_USD`, default 100, so 30,000 credits = $300).
2. **History.** Every successful fetch appends one line (time, spent, budget, unit, period end)
   to `history.jsonl`. History keeps the raw credits and the `basis` of the spend figure; snapshots of different bases are not differenced. The portal only gives a running total, so
   *daily spend is the difference between consecutive snapshots*. Spend before the first snapshot
   cannot be split into days.
3. **Local usage (OpenCode).** The OpenCode SQLite database is opened read-only. Both tables are
   read: the legacy `message` table and the newer `session_message` table (assistant messages,
   with provider and model nested under `model`). Only providers listed in
   `TOKEN_MONITOR_PROVIDERS` (default `github-copilot`) are counted. This gives tokens, messages
   and cost per day and per model.
4. **Local cost estimate.** The cost on each OpenCode message is OpenCode's own USD estimate, not
   what GitHub bills. It is multiplied by the cost factor: `TOKEN_MONITOR_COST_FACTOR` if set,
   otherwise a calibration worked out by comparing local cost with how much the portal's spend
   grew between snapshots, otherwise 1. Days that have portal deltas show real spend; other days
   show the estimate.
   **Hourly ranges.** The Usage page's **24h** and **72h** ranges group the same OpenCode messages
   into local clock-hour buckets (the last 72 hours are kept in the entry's `hourly` list, also
   visible in `--json`). They use the local estimate only: the portal's spend is too sparse to be
   split by hour, so the hourly cost chart has no budget, spend or projection lines. Those stay on
   the day ranges. Hidden models are removed from hourly rows too.
5. **Offline and stale data.** If the portal cannot be reached, the latest history snapshot is
   shown, marked stale ("offline — last portal data 3h ago"), plus the local cost since then. A
   last-good copy of each provider is also kept so a fresh process has something to show.
6. **Mock source.** With no `TOKEN_MONITOR_GH_TOKEN`, no usable `gh` login and no history, the app falls back to sample data
   (`data/mock-budget.json`, or `~/.config/token-monitor/mock.json`) so the UI still works. The
   `curl` source replays a command you supply instead (see [WIRING.md](WIRING.md)).

## Where things live

| What | Where |
|---|---|
| User settings | `~/.config/token-monitor/config` (`KEY=VALUE` lines) |
| Hidden providers and models | `~/.config/token-monitor/hidden.json` |
| Optional mock data / curl script | `~/.config/token-monitor/mock.json`, `curl.sh` |
| Snapshot history and the plot | `.token-monitor/history.jsonl`, `usage.svg` in the repo (`TOKEN_MONITOR_HISTORY_DIR` moves it) |
| Last-good cache | `~/.cache/token-monitor/last-good.json` |
| OpenCode data | `~/.local/share/opencode/opencode.db` (`TOKEN_MONITOR_OPENCODE_DB`) |

Settings are looked up in this order, first match wins: real environment variables, then `.env`
in the repo, then the config file.

## The budget limit

By default the limit is the plan's entitlement from GitHub. To use your own number:

- **In the app:** open the **Settings** tab, type a number in "Budget limit" and press the apply
  button. Use the clear button to go back to the GitHub value. The change shows up right away.
- **In the config file:** add `TOKEN_MONITOR_BUDGET=400` to `~/.config/token-monitor/config`.
- **As an environment variable** (or in `.env`): `TOKEN_MONITOR_BUDGET=400`. This wins over the
  config file, and the app then shows the field as read-only and says where it is set.

The number is in the displayed unit: dollars for the `github` source, so `400` means $400.00.
Percent used, "left" and the "lasts until" forecast are all recalculated from it. History still stores
the raw credits and the GitHub value, so removing the override brings back the real limit.
It must be greater than 0.

## Hiding providers and models

The **Settings** tab lists every provider that is set up and every model seen in recent usage.
Switch one off to hide it. Providers that were never set up (for example no `gh` login and no
curl command) are not shown at all. Hiding is saved in `hidden.json` and applies to the app,
`--text`, `--json` and the float window. A hidden model disappears from the charts and the "most
used models" ranking, but daily totals are unchanged.

## Known limits

- This is the personal seat quota, not an organisation budget.
- Hourly views need OpenCode usage; with the mock source or no local database they are empty.
- Per-model numbers exist only for usage OpenCode recorded. Copilot use from other editors or
  tools counts toward the quota but has no model breakdown.
- Cost is an estimate, not billed credits. Calibration improves it but cannot make it exact.
- Daily real spend starts at the first snapshot; earlier days only have the estimate.
- The quota endpoint is not a public, documented API and may change.

The browser dashboard (`python3 -m token_monitor.web`, or Docker) reads the same data layer; see [WEB.md](WEB.md).
