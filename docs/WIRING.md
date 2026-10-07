# Wiring the real source

Two real sources exist. Start with the first: it needs no browser capture.

## Option 1: GitHub Copilot quota (`TOKEN_MONITOR_SOURCE=github`, the default)

If `gh` is logged in to `github.com` (`gh auth status -h github.com`), nothing needs configuring:
Token Monitor runs `gh auth token -h github.com` and calls
`GET https://api.github.com/copilot_internal/user` (`Authorization: token ...`, `Accept:
application/json`, 15 s timeout). Override with `TOKEN_MONITOR_GH_HOST` (a GitHub Enterprise host such as `github.example.com`) or
`TOKEN_MONITOR_GH_TOKEN` (skip gh). It needs VPN/network; when that is down the last history snapshot
is served as *offline* (see below).

Mapping: `quota_snapshots.premium_interactions.entitlement` -> budget, `entitlement - remaining` -> spent (what the budget portal shows; `credits_used` lags it by a few dozen credits, so it is only the fallback after `quota_remaining`; `percent_remaining` is a sanity check),
top-level `quota_reset_date` -> period end (the period starts one month earlier), `copilot_plan` ->
the label. The raw unit is **credits** (`cr`), shown as **USD** at `TOKEN_MONITOR_CREDITS_PER_USD` (default 100: 30,000 credits = $300, 1,279 credits = $12.79); `0` shows raw credits. History and the JSON (`summary.credits`) keep the raw credits. Chat and completions are unlimited and
ignored. Nothing else from the response (login, email, analytics id, avatars) is kept, and the token
is never printed, logged or stored.

**This is your personal seat quota, not an organisation budget.** An organisation may run a budget
portal that shows the same quota, or a different budget with its own amounts, unit and reset date;
such portals usually need an SSO session that `gh` cannot provide. Token Monitor has no dedicated
source for them; Option 2 can replay one if you can capture a request.

## Option 2: replay a budget portal request (`TOKEN_MONITOR_SOURCE=curl`)

If your organisation has a budget portal (for example `https://portal.example.com/copilot/budget`),
its API is not documented here, so you capture the request your browser already makes and let Token
Monitor replay it.

### 1. Capture the request

1. Connect to the VPN if needed and open the portal's budget page, logged in via SSO.
2. Open DevTools (F12) -> **Network** tab, filter **Fetch/XHR**, and reload the page.
3. Find the request that returns the budget/spend numbers (look at the *Response* tab for the
   monthly budget and the amount spent).
4. Right-click it -> **Copy** -> **Copy as cURL (bash)**.

### 2. Save it

```sh
mkdir -p ~/.config/token-monitor
$EDITOR ~/.config/token-monitor/curl.sh      # paste the command; add -sS --fail to the curl flags
chmod 600 ~/.config/token-monitor/curl.sh    # it contains your session cookie: keep it private
```

`curl.sh` must print the JSON response to stdout and nothing else. A typical file:

```sh
curl -sS --fail 'https://portal.example.com/...' -H 'cookie: ...' -H 'accept: application/json'
```

Alternatively put the one-line command in `TOKEN_MONITOR_CURL_CMD`.

### 3. Map the fields

Token Monitor needs four values: `budget`, `spent`, and optionally `currency` (default `EUR`) and
`period_end` (ISO date; default end of the current month). If the JSON does not already use those
names at its top level, tell it where they are with `TOKEN_MONITOR_JSON_MAP`:

```
TOKEN_MONITOR_JSON_MAP="budget=data.monthlyBudget,spent=data.items.0.spent,currency=data.currency,period_end=data.resetAt"
```

Paths are dotted keys; a number steps into a list. Fields you omit keep their default name.

### 4. Switch it on

Settings are read from the real environment first, then from a `.env` file in the repo root (loaded
at startup; it never overrides variables already set), then from `~/.config/token-monitor/config`.
The simplest route:

```sh
cp .env.example .env
chmod 600 .env          # gitignored, but it may hold your SSO cookie: keep it private
$EDITOR .env
```

```
TOKEN_MONITOR_SOURCE=curl
TOKEN_MONITOR_JSON_MAP=budget=data.monthlyBudget,spent=data.items.0.spent
# TOKEN_MONITOR_CURL_CMD=curl -sS --fail 'https://...' -H 'cookie: ...'   # instead of curl.sh
# TOKEN_MONITOR_BUDGET=300    # fixed budget, overrides the JSON (if the portal only reports spend)
```

Values may be quoted; `#` starts a comment on its own line (or after a space on an unquoted value).
Prefer `curl.sh` over `TOKEN_MONITOR_CURL_CMD` for long commands with cookies - quoting is easier.

Check it: `token-monitor --text`. Errors are shown in the output instead of numbers; the command, its
stderr and the response body are never printed, so cookies do not end up in logs or the UI. The last
good numbers are cached in `~/.cache/token-monitor/` and shown as *stale* when a refresh fails (see
*Offline fallback* below).

## SSO cookie expiry

The cookie in `curl.sh` expires with your SSO session (typically hours to days). When it does, the
portal answers with a login page or a redirect: Token Monitor reports *curl command did not return
valid JSON; SSO cookie expired* (or *curl command failed*), and shows the cached numbers as stale.
Repeat steps 1-2 to refresh `curl.sh`. If the VPN is down you will see the same failure; the
monitor then falls back to the last portal snapshot (below).

Treat `curl.sh` like a password: never commit it, never paste it into chat or tickets.

## Offline fallback

With the `github` or `curl` source every successful fetch is appended to `.token-monitor/history.jsonl` (repo
root, gitignored; `TOKEN_MONITOR_HISTORY_DIR` relocates it). It holds only `ts`, `spent`, `budget`,
`currency`, `period_end` - no cookies, no response bodies. If a later fetch fails for any reason (VPN
down, non-zero curl exit, HTTP 401 with `--fail`, an SSO login page instead of JSON), the monitor
shows the newest snapshot of the current period labelled **offline - last portal data 3h ago**, plus
a *local estimate*: the cost OpenCode recorded since that snapshot. Delete the file to forget history.

## Local usage and calibration

The local source needs no VPN: OpenCode's database is opened read-only
(`TOKEN_MONITOR_OPENCODE_DB`, default `~/.local/share/opencode/opencode.db`) and the assistant messages
of the providers in `TOKEN_MONITOR_PROVIDERS` (default `github-copilot`) are aggregated per day.

Its `cost` is **OpenCode's own USD estimate, not what was billed**, so anything derived from it is a
*local estimate*. For the `github` source USD is also the display scale, so no conversion is needed
(`TOKEN_MONITOR_COST_FACTOR` defaults to 1, or to the calibrated ratio below).

### True daily spend

Charts do not rely on that estimate where the portal can say better. Each fetch records the cumulative
spend in the history; the growth between two consecutive snapshots of one period is exact and is spread
over the local days of that interval (by the local cost of the messages in it; by time if there were
none). OpenCode's estimate only fills the gaps: before the first snapshot, between periods and since
the latest one. Daily rows carry `cost_true`, `cost_est` and `basis`. Leave the app (or
`token-monitor --text` in a loop/cron) running to collect more intervals.

### Calibration

1. With snapshots of the same period and OpenCode activity in between, `token-monitor --text` prints
   `portal spend ≈ 1.80× local cost (3 intervals, $4.20 local)`.
2. Once at least $1 of local cost lies in those intervals and `TOKEN_MONITOR_COST_FACTOR` is unset, the
   ratio is **applied automatically** to the estimates (`summary.factor_basis` = `calibrated`).
   Set `TOKEN_MONITOR_COST_FACTOR` to override it.

The "lasts until" row's burn rate (daily spending) is the average daily cost of the last 7 days (true where known), used only
when those rows explain at least half of the period's spend; otherwise it is the period average.
