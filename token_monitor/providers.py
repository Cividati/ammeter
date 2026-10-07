"""One fetcher per provider. Each returns the normalised provider dict documented in core.py.

There is a single provider, ``copilot``: GitHub Copilot spend against a monthly budget, as shown on
the budget portal. The portal's real API is not known yet, so the *source* is pluggable and picked
by ``TOKEN_MONITOR_SOURCE``. Settings come from settings.py (environment, then ``.env``, then
``~/.config/token-monitor/config``):

``github`` (default when ``TOKEN_MONITOR_GH_TOKEN`` is set or ``gh`` is logged in to the host)
    The GitHub (Enterprise) Copilot quota: ``GET https://api.<host>/copilot_internal/user`` with the
    token from ``gh auth token -h <host>`` (``TOKEN_MONITOR_GH_HOST``, default ``github.com``;
    ``TOKEN_MONITOR_GH_TOKEN`` skips gh). It reports the *personal seat* quota in credits
    (``premium_interactions``: entitlement, remaining, reset date; spent = entitlement - remaining, as the
    budget portal shows it - ``credits_used`` lags a little and is only the fallback), which can differ from the budget portal
    portal budget. Needs VPN/network. Only numbers and the plan name are kept: the response (login,
    email, ids, avatar urls) is never stored, printed or logged, and neither is the token.
``mock`` (default only when there is no token, no gh login and no history; always labelled "mock data")
    Reads ``~/.config/token-monitor/mock.json`` if present, else ``data/mock-budget.json``.
``curl``
    Runs a command you provide and expects JSON on stdout: ``TOKEN_MONITOR_CURL_CMD`` (a shell
    command, e.g. a DevTools "Copy as cURL"), else ``~/.config/token-monitor/curl.sh``.

The JSON is mapped onto four fields - ``budget``, ``spent``, ``currency``, ``period_end`` - by
``TOKEN_MONITOR_JSON_MAP``, e.g. ``budget=data.limit,spent=data.used,currency=data.cur``. Paths are
dotted keys with list indexes (``items.0.spent``). Unmapped fields default to their own name at the
top level. ``currency`` and ``period_end`` are optional (EUR; end of the current month).

``TOKEN_MONITOR_BUDGET`` (a number, in the displayed unit: USD for github) overrides the budget
whatever the source says; use it when the source only reports spend or to set your own limit.
The app's Settings tab writes it to ~/.config/token-monitor/config.

Every successful ``curl`` fetch is appended to the history (history.py). When the portal cannot be
reached, the latest snapshot is served as *stale* ("offline - last portal data 3h ago") with a local
estimate overlaid: the cost OpenCode recorded since that snapshot (usage.py). The "lasts until" row
(budget lasts until ...) uses the recent burn rate from that local data. Local cost is OpenCode's
estimate, not billed credits; see usage.py and docs/WIRING.md for calibrating it.

Secrets: the command, its stderr and the response body are never logged or put in error messages.
"""
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from . import history, settings, usage
from .formatting import SYMBOLS, age_text, balance_row, money, quota_row
from .settings import setting

DATA_DIR = settings.REPO / "data"
TIMEOUT = 30
HTTP_TIMEOUT = 15
GH_BIN = "gh"           # the gh executable; replaced in the selftest
OPENER = None            # callable(request, timeout=...) like urlopen; replaced in the selftest
DEFAULT_UNIT = "cr"
CREDITS_PER_USD = 100.0      # 30,000 credits = $300
HOURLY_HOURS = 744         # hourly rows kept (31 days): the web hourly charts, 24h/72h ranges and time slices
MIN_CALIBRATION_COST = 1.0   # local USD cost needed in the history before the ratio is auto-applied
FIELDS = ("budget", "spent", "currency", "period_end")
RECENT_DAYS = 7


# ---------------------------------------------------------------------------- sources
def _read_mock():
    for path in (settings.CONFIG_DIR / "mock.json", DATA_DIR / "mock-budget.json"):
        if path.is_file():
            return json.loads(path.read_text())
    raise RuntimeError("no mock data: expected data/mock-budget.json or ~/.config/token-monitor/mock.json")


def _run_curl():
    command = setting("TOKEN_MONITOR_CURL_CMD")
    script = settings.CONFIG_DIR / "curl.sh"
    if command:
        args, shell = command, True
    elif script.is_file():
        args, shell = ["bash", str(script)], False
    else:
        raise RuntimeError("curl source: set TOKEN_MONITOR_CURL_CMD or create "
                           "~/.config/token-monitor/curl.sh (see docs/WIRING.md)")
    try:
        done = subprocess.run(args, shell=shell, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"curl command timed out after {TIMEOUT}s") from None
    if done.returncode != 0:
        # no stderr/command here: they can carry cookies or tokens
        raise RuntimeError(f"curl command failed (exit {done.returncode}); "
                           "VPN down or SSO cookie expired?")
    try:
        return json.loads(done.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise RuntimeError("curl command did not return valid JSON; "
                           "SSO cookie expired (login page)?") from None


def gh_host():
    return setting("TOKEN_MONITOR_GH_HOST", "github.com")


def _gh_token():
    """The GitHub token: TOKEN_MONITOR_GH_TOKEN, else ``gh auth token -h <host>``. Never printed."""
    given = setting("TOKEN_MONITOR_GH_TOKEN")
    if given:
        return given.strip()
    if not shutil.which(GH_BIN):
        raise RuntimeError("gh not found (install GitHub CLI or set TOKEN_MONITOR_GH_TOKEN)")
    try:
        done = subprocess.run([GH_BIN, "auth", "token", "-h", gh_host()], capture_output=True,
                              timeout=10)
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh auth token timed out") from None
    token = done.stdout.decode("utf-8", "replace").strip()
    if done.returncode != 0 or not token:
        raise RuntimeError(f"gh is not logged in to {gh_host()} (gh auth login -h {gh_host()})")
    return token


def _http_json(request):
    """Open ``request`` through the (replaceable) opener; map failures to token-free messages."""
    try:
        with (OPENER or urllib.request.urlopen)(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as failure:
        hint = " - token rejected or expired (gh auth login)" if failure.code in (401, 403) else ""
        raise RuntimeError(f"GitHub API answered HTTP {failure.code}{hint}") from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError("GitHub API unreachable (VPN down?)") from None
    except ValueError:
        raise RuntimeError("GitHub API did not return valid JSON") from None


def _num(value):
    """``value`` as a float, or None when it is missing or not a number (bools do not count)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _spent_of(quota):
    """(credits spent, basis) from a premium_interactions quota.

    ``entitlement - remaining`` is what the budget portal shows; ``credits_used`` lags it by a few dozen
    credits, so it is only the fallback (after ``quota_remaining``). A ``remaining`` that disagrees with
    ``percent_remaining`` by more than a point is not trusted.
    """
    entitlement = _num(quota.get("entitlement"))
    percent = _num(quota.get("percent_remaining"))
    if entitlement and entitlement > 0:
        for key in ("remaining", "quota_remaining"):
            left = _num(quota.get(key))
            if left is None or (percent is not None and abs(left / entitlement * 100 - percent) > 1.0):
                continue
            return max(entitlement - left, 0.0), key
    return quota.get("credits_used"), "credits_used"


def _fetch_github(token):
    """Premium-interactions quota from copilot_internal/user -> (document for snapshot(), plan label).

    Only the numbers and the plan name are copied out; nothing else in the response is retained.
    """
    host = gh_host()
    api = "https://api.github.com" if host == "github.com" else f"https://api.{host}"
    request = urllib.request.Request(f"{api}/copilot_internal/user", headers={
        "Authorization": f"token {token}", "Accept": "application/json", "User-Agent": "token-monitor"})
    data = _http_json(request)
    quota = ((data.get("quota_snapshots") or {}).get("premium_interactions")
             if isinstance(data, dict) else None)
    if not isinstance(quota, dict):
        raise RuntimeError("no premium_interactions quota in the GitHub response")
    if quota.get("unlimited"):
        raise RuntimeError("premium quota is unlimited: nothing to budget")
    used, basis = _spent_of(quota)
    plan = str(data.get("copilot_plan") or "")
    if quota.get("overage_permitted"):
        plan = f"{plan} \u00b7 overage allowed" if plan else "overage allowed"
    return {"budget": quota.get("entitlement"), "spent": used, "basis": basis,
            "reported": _num(quota.get("credits_used")),
            "period_end": data.get("quota_reset_date")}, plan


# ---------------------------------------------------------------------------- normalising
def parse_map(text):
    """'budget=data.limit,spent=data.used' -> {'budget': 'data.limit', 'spent': 'data.used'}."""
    mapping = {field: field for field in FIELDS}
    for part in (text or "").split(","):
        field, _, path = part.partition("=")
        if field.strip() in mapping and path.strip():
            mapping[field.strip()] = path.strip()
    return mapping


def _dig(raw, path):
    """Follow a dotted path through dicts and lists; None when any step is missing."""
    for step in path.split("."):
        try:
            raw = raw[int(step)] if isinstance(raw, list) else raw[step]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return raw


def _number(raw, mapping, field):
    value = _dig(raw, mapping[field])
    try:
        if isinstance(value, bool):
            raise ValueError
        return float(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"field '{field}' (path '{mapping[field]}') missing or not a number") from None


def month_bounds(now):
    """(start, end) of the calendar month containing ``now``, as local-time aware datetimes.

    Built from naive local dates so a DST change inside the month does not skew the offset.
    """
    start = datetime(now.year, now.month, 1)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
    return start.astimezone(), end.astimezone()


def _month_back(moment):
    """The same wall-clock moment one calendar month earlier (day clamped), local aware."""
    local = moment.astimezone().replace(tzinfo=None)
    year, month = (local.year - 1, 12) if local.month == 1 else (local.year, local.month - 1)
    last = (datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return local.replace(year=year, month=month, day=min(local.day, last)).astimezone()


def period_of(end_iso, now):
    """(start, end) for a period ending at ``end_iso`` (start: one month earlier); the calendar month
    of ``now`` when there is no end."""
    if not end_iso:
        return month_bounds(now)
    end = datetime.fromisoformat(str(end_iso))
    end = end if end.tzinfo else end.astimezone()
    return _month_back(end), end


def snapshot(raw, mapping=None, now=None, budget=None, daily=None, unit=None):
    """Decoded JSON -> ``{budget, spent, currency, period_end}`` (``period_end`` always resolved).

    ``budget`` overrides the document's (TOKEN_MONITOR_BUDGET); ``unit`` is the currency/unit when the
    document names none (EUR by default). When the document has no ``spent`` and
    ``daily`` rows are given (mock mode), spend is the local cost so far this period.
    """
    mapping = mapping or parse_map(None)
    now = now or datetime.now().astimezone()
    if not isinstance(raw, dict):
        raise RuntimeError("response is not a JSON object")
    if _dig(raw, mapping["spent"]) is None and daily is not None:
        spent = None
    else:
        spent = _number(raw, mapping, "spent")
    budget = budget if budget is not None else _number(raw, mapping, "budget")
    if budget <= 0:
        raise RuntimeError("budget must be positive")
    given = _dig(raw, mapping["period_end"])
    try:
        start, end = period_of(given, now)
    except ValueError:
        raise RuntimeError("field 'period_end' is not an ISO date") from None
    if spent is None:
        spent = sum(row["cost"] for row in daily if row["day"] >= start.date().isoformat())
    if spent < 0:
        raise RuntimeError("spent must not be negative")
    currency = str(_dig(raw, mapping["currency"]) or unit or "EUR")
    currency = currency.upper() if currency.upper() in SYMBOLS else currency
    return {"budget": budget, "spent": spent, "currency": currency, "period_end": end.isoformat()}


def burn_rate(daily, spent, start, now):
    """(cost per day, basis): the last 7 days' local average, else this period's average so far.

    The local average is only trusted when local usage covers at least half of the period's spend;
    otherwise it would hide usage from other clients, and the period average is used instead.
    """
    recent = (daily or [])[-RECENT_DAYS:]
    total = sum(row["cost"] for row in recent)
    if total > 0 and usage.coverage(daily, spent, start) >= 0.5:
        return total / len(recent), f"{len(recent)}d avg"
    elapsed = max((now - start).total_seconds() / 86400, 1.0)
    return (spent / elapsed if spent > 0 else 0.0), "period avg"


def outlook(budget, spent, rate, now, end):
    """When the budget runs out at ``rate`` per day, compared with the period end.

    Returns ``{"state", "days", "out", "projected"}`` where state is ``exhausted`` (nothing left),
    ``idle`` (no burn), ``short`` (runs out before ``end``) or ``ok``; ``projected`` is the spend
    expected at ``end``.
    """
    left_days = max((end - now).total_seconds() / 86400, 0.0)
    projected = spent + rate * left_days
    if spent >= budget:
        return {"state": "exhausted", "days": 0.0, "out": now, "projected": projected}
    if rate <= 0:
        return {"state": "idle", "days": None, "out": None, "projected": projected}
    days = (budget - spent) / rate
    out = now + timedelta(days=days)
    return {"state": "short" if out < end else "ok", "days": days, "out": out, "projected": projected}


def _dollars(value, currency):
    """'$300' for a whole amount, '$287.21' otherwise (the short form used beside credits)."""
    return f"${value:,.0f}" if abs(value - round(value)) < 0.005 and currency == "USD" else money(value, currency)


def _burn_text(rate, currency, credits):
    """'$2.13/day', or '~213 cr/day ($2.13/day)' when the amounts also have a credits scale."""
    if not credits:
        return f"{money(rate, currency)}/day"
    return f"~{rate * credits['per_usd']:,.0f} {credits['unit']}/day ({money(rate, currency)}/day)"


def _left_text(budget, spent, currency, credits):
    """'$285.63 left of $300.00', or '28,563 / 30,000 cr left ($285.63 / $300)' with credits."""
    left = max(budget - spent, 0.0)
    if not credits:
        return f"{money(left, currency)} left of {money(budget, currency)}"
    scale, unit = credits["per_usd"], credits["unit"]
    return (f"{left * scale:,.0f} / {budget * scale:,.0f} {unit} left "
            f"({_dollars(left, currency)} / {_dollars(budget, currency)})")


LASTS_LABEL = "lasts until"      # the row that says how long the budget lasts (summary key: "outlook")


def _lasts_row(plan, rate, currency, end, credits=None, budget=0.0):
    """The 'lasts until' row: plain sentences, red when the budget runs out before the reset."""
    state = plan["state"]
    if state == "exhausted":
        return dict(balance_row(LASTS_LABEL, "Budget used up"), level="crit")
    if state == "idle":
        return balance_row(LASTS_LABEL, "No recent spending")
    burn = f"spending {_burn_text(rate, currency, credits)}"
    if state == "ok":
        left = money(max(budget - plan["projected"], 0.0), currency)
        return balance_row(LASTS_LABEL, f"Lasts past the reset (about {left} left on {end.day} {end:%b}) \u00b7 {burn}")
    early = max(1, round((end - plan["out"]).total_seconds() / 86400))
    what = "credits" if credits else "the budget"
    return dict(balance_row(LASTS_LABEL, f"At this pace {what} run{'' if credits else 's'} out ~{plan['out']:%a %d %b} "
                                         f"(in {max(1, round(plan['days']))}d), {early}d before the reset \u00b7 {burn}"),
                level="crit")


def build_entry(snap, now=None, label="", daily=None, estimate=None):
    """The provider entry (rows plus ``summary`` and ``daily`` for the plots) from a snapshot.

    ``estimate`` is None for live portal numbers; a number (possibly 0) means the snapshot is old and
    that much local cost has accrued since, so spend is shown as an estimate.
    """
    now = now or datetime.now().astimezone()
    budget, currency = snap["budget"], snap["currency"]
    start, end = period_of(snap["period_end"], now)
    spent = snap["spent"] + (estimate or 0.0)
    rate, basis = burn_rate(daily, spent, start, now)
    plan = outlook(budget, spent, rate, now, end)

    note_prefix = f"~{money(spent, currency)} est." if estimate is not None else \
        f"{money(spent, currency)} spent"
    rows = [quota_row("budget", spent / budget * 100, end.isoformat()),
            balance_row("left", _left_text(budget, spent, currency, snap.get("credits"))),
            _lasts_row(plan, rate, currency, end, snap.get("credits"), budget)]
    rows[0]["note"] = f"{note_prefix} \u00b7 {rows[0]['note']}"
    summary = {"budget": budget, "spent": spent, "portal_spent": snap["spent"], "currency": currency,
               "period_start": start.isoformat(), "period_end": end.isoformat(),
               "burn": rate, "burn_basis": basis, "outlook": plan["state"],
               "out_date": plan["out"].isoformat() if plan["out"] else None,
               "projected": plan["projected"], "estimate": estimate, "calibration": None,
               "credits": snap.get("credits"), "cost_factor": None, "factor_basis": None}
    return {"name": "COPILOT", "sub": label, "rows": rows, "reached": spent >= budget,
            "summary": summary, "daily": daily or []}


def normalise(raw, mapping=None, now=None, source="", budget=None, daily=None):
    """Decoded JSON -> provider entry. Pure: no network, no clock unless asked."""
    now = now or datetime.now().astimezone()
    return build_entry(snapshot(raw, mapping, now, budget, daily), now, source, daily)


# ---------------------------------------------------------------------------- providers
def credits_per_usd():
    """TOKEN_MONITOR_CREDITS_PER_USD (default 100; 0 shows raw credits instead of dollars)."""
    raw = setting("TOKEN_MONITOR_CREDITS_PER_USD")
    try:
        value = float(raw) if raw else CREDITS_PER_USD
    except ValueError:
        raise RuntimeError("TOKEN_MONITOR_CREDITS_PER_USD is not a number") from None
    if value < 0:
        raise RuntimeError("TOKEN_MONITOR_CREDITS_PER_USD must not be negative")
    return value


def _converter(source, unit):
    """show(snapshot): the github source's raw credits as dollars (raw kept under ``credits``).

    History and the JSON keep raw credits; only what is displayed and charted is in USD.
    """
    per_usd = credits_per_usd() if source == "github" else 0

    def show(snap):
        if per_usd > 0 and snap["currency"] == unit:
            return dict(snap, spent=snap["spent"] / per_usd, budget=snap["budget"] / per_usd,
                        currency="USD", credits={"spent": snap["spent"], "budget": snap["budget"],
                                                 "per_usd": per_usd, "unit": unit})
        return snap
    return show


def _offline_entry(now, daily, messages, factor, have_local, snap):
    """``snap`` (latest history snapshot, display units) plus the local estimate since, flagged stale."""
    if snap["period_end"] and period_of(snap["period_end"], now)[1] <= now:
        return None                      # that snapshot belongs to a period that is over
    taken = datetime.fromisoformat(snap["ts"])
    estimate = usage.cost_between(messages, taken, now, factor) if have_local else None
    entry = build_entry(snap, now, "local estimate" if have_local else "", daily, estimate)
    entry["stale"] = True
    entry["why"] = f"offline \u2014 last portal data {age_text((now - taken).total_seconds())} ago"
    return entry


def _choose_source():
    """(source, token): TOKEN_MONITOR_SOURCE if set, else github when gh is usable, else mock.

    With no gh login but some history, github is still chosen so the offline fallback can serve it.
    """
    explicit = setting("TOKEN_MONITOR_SOURCE")
    if explicit:
        return explicit.lower(), None
    given = setting("TOKEN_MONITOR_GH_TOKEN")          # e.g. in a container, which has no gh
    if given and given.strip():
        return "github", given.strip()
    try:
        return "github", _gh_token()
    except RuntimeError:
        return ("github" if history.load() else "mock"), None


def _add_skills(entry, local, now, have_local, messages=None):
    """Skill-load counts per day and hour on the entry, plus ``skill_last`` (name -> last use).

    A missing or unreadable skills table just means no skills; it never fails the entry.
    """
    entry["model_providers"] = usage.model_providers(messages or [], entry.get("daily"))
    if local == "mock":     # mock hourly rows already carry their share of the skills
        entry["skill_last"] = usage.mock_skill_last(entry.get("daily") or [])
        return
    skills = []
    if have_local:
        try:
            skills = usage.load_skills(usage.since_ms(now))
        except RuntimeError:
            pass
    entry["skill_last"] = usage.attach_skills(entry.get("daily"), entry.get("hourly"), skills)


def fetch_copilot():
    """Copilot quota/budget from the chosen source, plus local OpenCode usage and history fallback."""
    now = datetime.now().astimezone()
    source, token = _choose_source()
    if source not in ("mock", "curl", "github"):
        raise RuntimeError(f"unknown TOKEN_MONITOR_SOURCE '{source}' (use github, mock or curl)")
    portal = source in ("curl", "github")
    local = (setting("TOKEN_MONITOR_USAGE") or ("opencode" if portal else "mock")).lower()
    if local not in ("mock", "opencode"):
        raise RuntimeError(f"unknown TOKEN_MONITOR_USAGE '{local}' (use mock or opencode)")
    unit = setting("TOKEN_MONITOR_UNIT") or (DEFAULT_UNIT if source == "github" else None)
    show = _converter(source, unit)
    explicit = bool(setting("TOKEN_MONITOR_COST_FACTOR"))
    factor, basis = usage.cost_factor(), "setting" if explicit else "default"

    messages, have_local = [], False
    if local == "mock":
        daily = usage.mock_daily(now.date())
    else:
        try:
            messages = usage.load_messages(usage.since_ms(now))
            have_local = True
        except RuntimeError:
            pass                     # no local data: the budget rows still work from the portal
        daily = usage.daily(messages, now.date(), factor=factor)

    override = setting("TOKEN_MONITOR_BUDGET")
    if override:
        try:
            override = float(override)
        except ValueError:
            raise RuntimeError("TOKEN_MONITOR_BUDGET is not a number") from None
        if override <= 0:
            raise RuntimeError("TOKEN_MONITOR_BUDGET must be positive")
    mapping = parse_map(setting("TOKEN_MONITOR_JSON_MAP"))

    failure = snap = None
    try:
        if source == "mock":
            raw, label = _read_mock(), "mock data"
        elif source == "curl":
            raw, label = _run_curl(), ""
        else:
            raw, label = _fetch_github(token or _gh_token())
            mapping = parse_map(None)
        snap = snapshot(raw, mapping, now, (override or None) if source != "github" else None,
                        daily if source == "mock" else None, unit)
    except Exception as caught:
        failure = caught

    calibration = None
    if portal:
        if snap:
            snap.update({k: raw[k] for k in ("basis", "reported") if source == "github" and raw.get(k) is not None})
            history.append(snap)                       # raw numbers, in the source's own unit
        saved = history.load()
        unit_now = snap["currency"] if snap else (unit if source == "github" else
                                                  saved[-1]["currency"] if saved else None)
        snaps = [show(s) for s in saved if s["currency"] == unit_now]
        use = messages if have_local else []
        calibration = usage.calibration(snaps, use)
        if calibration and not explicit and calibration["local"] >= MIN_CALIBRATION_COST:
            factor, basis = calibration["ratio"], "calibrated"
        daily = usage.attribute(usage.daily(use, now.date(), factor=factor), snaps, use, factor)

    if failure is not None:
        if portal and snaps:
            last = snaps[-1]
            offline = _offline_entry(now, daily, messages, factor, have_local,
                                     dict(last, budget=override) if override and source == "github" else last)
            if offline:
                offline["hourly"] = usage.hourly(messages, now, HOURLY_HOURS, factor) if have_local else []
                offline["summary"].update(cost_factor=factor, factor_basis=basis,
                                          calibration=calibration, source_budget=last["budget"])
                _add_skills(offline, local, now, have_local, messages)
                return offline
        raise failure

    shown = show(snap)
    source_budget = shown["budget"]              # what the portal says, before any user override
    if override and source == "github":
        shown = dict(shown, budget=override)
    if override and source == "github":
        label = f"{label} \u00b7 budget override" if label else "budget override"
    entry = build_entry(shown, now, label, daily)
    entry["mock"] = source == "mock"
    entry["hourly"] = (usage.mock_hourly(daily, now, HOURLY_HOURS) if local == "mock"
                       else usage.hourly(messages, now, HOURLY_HOURS, factor) if have_local else [])
    entry["summary"].update(source_budget=source_budget, calibration=calibration, cost_factor=factor, factor_basis=basis)
    _add_skills(entry, local, now, have_local, messages)
    return entry


# order matters: it is the order of the app groups, the menu blocks and the CLI output
PROVIDERS = (
    ("copilot", fetch_copilot),
)
