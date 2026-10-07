"""Checks for the copilot provider, history, local usage and the SVG report. No network, no real data.

Run through ``token-monitor --selftest``. Everything touches throwaway directories and a temporary
SQLite database; the real history, OpenCode database and config are never read or written.
"""
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import chart_math as cm
from . import cache, core, filters, history, plot, settings, usage
from . import providers as pv
from .formatting import age_text, human_tokens, money, severity, sparkline

KEYS = ("TOKEN_MONITOR_CREDITS_PER_USD", "TOKEN_MONITOR_GH_HOST", "TOKEN_MONITOR_GH_TOKEN", "TOKEN_MONITOR_UNIT", "TOKEN_MONITOR_SOURCE", "TOKEN_MONITOR_CURL_CMD", "TOKEN_MONITOR_JSON_MAP",
        "TOKEN_MONITOR_BUDGET", "TOKEN_MONITOR_USAGE", "TOKEN_MONITOR_HISTORY_DIR",
        "TOKEN_MONITOR_OPENCODE_DB", "TOKEN_MONITOR_PROVIDERS", "TOKEN_MONITOR_COST_FACTOR")


@contextlib.contextmanager
def environment(**given):
    """Cleared settings, a throwaway config dir and history dir; yields (config_dir, history_dir)."""
    # every setting starts cleared so a developer's own .env cannot leak into the checks
    scratch = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-"))
    values = {**dict.fromkeys(KEYS), "TOKEN_MONITOR_HISTORY_DIR": str(scratch / "history"),
              "TOKEN_MONITOR_OPENCODE_DB": str(scratch / "missing.db"), **given}
    saved = {key: os.environ.get(key) for key in values}
    saved_dir, saved_gh, saved_opener = settings.CONFIG_DIR, pv.GH_BIN, pv.OPENER
    pv.GH_BIN = str(scratch / "no-such-gh")                       # gh "not installed" by default
    pv.OPENER = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used in selftest"))
    settings.CONFIG_DIR = scratch / "config"
    for key, value in values.items():
        os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
    try:
        yield settings.CONFIG_DIR, scratch / "history"
    finally:
        settings.CONFIG_DIR, pv.GH_BIN, pv.OPENER = saved_dir, saved_gh, saved_opener
        for key, value in saved.items():
            os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)


def fails(call, needle, message):
    """Assert that ``call()`` raises RuntimeError mentioning ``needle``."""
    try:
        call()
    except RuntimeError as failure:
        assert needle in str(failure), f"{message}: {failure}"
        return
    raise AssertionError(f"{message}: no error raised")


def make_db(path, rows):
    """A minimal OpenCode-shaped database: message(id, session_id, time_created, time_updated, data)."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE message (id text PRIMARY KEY, session_id text NOT NULL, "
                       "time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL)")
    for number, (created, data) in enumerate(rows):
        connection.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                           (f"m{number}", "s", created, created, data if isinstance(data, str)
                            else json.dumps(data)))
    connection.commit()
    connection.close()


def message(ts, model="claude-sonnet-5.5", cost=1.0, provider="github-copilot", role="assistant",
            input=100, output=50, reasoning=0, read=1000, write=200):
    return (int(ts.timestamp() * 1000), {
        "role": role, "providerID": provider, "modelID": model, "cost": cost,
        "tokens": {"total": input + output + reasoning + read + write, "input": input,
                   "output": output, "reasoning": reasoning, "cache": {"read": read, "write": write}}})


def check_basics(now):
    start, end = pv.month_bounds(now)
    assert (start.month, end.month, (end - start).days) == (10, 11, 31), "calendar month bounds"
    assert pv.month_bounds(datetime(2026, 12, 20).astimezone())[1].year == 2027, "December rollover"
    assert pv.parse_map("budget=a.b, spent=c.0.d ,junk=x")["spent"] == "c.0.d"
    assert pv.parse_map(None) == {f: f for f in pv.FIELDS}
    assert sparkline([0, 0]) == "\u2581\u2581" and sparkline([1, 8])[-1] == "\u2588"
    assert age_text(30) == "1m" and age_text(7200) == "2h" and age_text(3 * 86400) == "3d"
    assert human_tokens(25_700_000) == "25.7M" and human_tokens(950) == "950"
    assert money(5, "USD") == "$5.00" and money(187.4, "EUR") == "\u20ac187.40"
    assert money(1279, "cr") == "1,279 cr" and money(41.3, "cr") == "41.3 cr" and money(5.0, "cr") == "5 cr"
    assert money(28721.3, "cr") == "28,721 cr" and money(0.2, "cr") == "0.2 cr"
    # a period ends at its reset date and starts one calendar month earlier (day clamped)
    assert pv.period_of("2026-11-01", now)[0].date() == date(2026, 10, 1)
    assert pv.period_of("2026-03-31", now)[0].date() == date(2026, 2, 28)
    assert pv.period_of("2027-01-15", now)[0].date() == date(2026, 12, 15)
    assert pv.period_of("2026-11-01", now)[1].date() == date(2026, 11, 1)


def check_outlook(now):
    _, end = pv.month_bounds(now)                       # 15.5 days left
    ok = pv.outlook(300, 60, 10, now, end)               # 24 days of money > 15.5 left
    assert ok["state"] == "ok" and abs(ok["days"] - 24) < 1e-9
    assert abs(ok["projected"] - (60 + 10 * 15.5)) < 1.0                # DST may shift an hour
    short = pv.outlook(300, 60, 20, now, end)            # 12 days < 15.5 left
    assert short["state"] == "short" and abs((short["out"] - now).days - 11) <= 1
    assert pv.outlook(300, 300, 5, now, end)["state"] == "exhausted"
    assert pv.outlook(300, 60, 0, now, end)["state"] == "idle"
    assert pv.outlook(300, 60, 0, now, end)["out"] is None

    rate, basis = pv.burn_rate(None, 62, end - timedelta(days=31), now)
    assert basis == "period avg" and abs(rate - 62 / 15.5) < 0.1, "fallback is the period average"
    day = now.date().isoformat()
    start = pv.month_bounds(now)[0]
    rows = [{"cost": 7.0, "day": day}] * 7 + [{"cost": 100.0, "day": day}]    # only the last 7 count
    assert pv.burn_rate(rows, 1, start, now) == (mean_cost(rows[-7:]), "7d avg")
    assert pv.burn_rate([{"cost": 0.0, "day": day}] * 7, 0, start, now)[0] == 0.0
    # local usage that explains under half of the spend is not trusted: other clients use the quota
    low = pv.burn_rate([{"cost": 1.0, "day": day}] * 7, 1000.0, start, now)
    assert low[1] == "period avg" and abs(low[0] - 1000 / 15.5) < 1, low
    assert usage.coverage([{"cost": 3.0, "day": day}], 0, start) == 1.0
    assert abs(usage.coverage([{"cost": 3.0, "day": day}], 12.0, start) - 0.25) < 1e-9


def mean_cost(rows):
    return sum(r["cost"] for r in rows) / len(rows)


def check_entry(now):
    def rows_for(spent, budget=300, **extra):
        entry = pv.normalise(dict({"budget": budget, "spent": spent, "currency": "EUR"}, **extra), now=now)
        return entry, {r["label"]: r for r in entry["rows"]}

    entry, rows = rows_for(60)                              # 20% used
    assert entry["name"] == "COPILOT" and entry["reached"] is False
    assert rows["budget"]["pct"] == 20 and severity(rows["budget"]["pct"]) == ""
    assert rows["left"]["pct"] is None and rows["left"]["note"] == "\u20ac240.00 left of \u20ac300.00"
    assert "level" not in rows["lasts until"] and "Lasts past the reset" in rows["lasts until"]["note"]
    assert entry["summary"]["outlook"] == "ok" and entry["summary"]["estimate"] is None
    json.dumps(entry)                                       # the --json output must serialise

    _, rows = rows_for(140)                                 # ~9/day: lasts to the end
    assert "level" not in rows["lasts until"]
    _, rows = rows_for(160)                                 # ~10.3/day: out before the reset
    assert rows["lasts until"]["level"] == "crit" and " out ~" in rows["lasts until"]["note"]
    assert "before the reset" in rows["lasts until"]["note"] and "(in " in rows["lasts until"]["note"]
    assert severity(rows_for(210)[1]["budget"]["pct"]) == "warn"     # 70%
    assert severity(rows_for(270)[1]["budget"]["pct"]) == "crit"     # 90%
    entry, rows = rows_for(310)
    assert entry["reached"] is True and rows["left"]["note"].startswith("\u20ac0.00 left")
    assert rows["lasts until"]["note"] == "Budget used up" and rows["lasts until"]["level"] == "crit"

    mapped = pv.normalise({"d": {"cap": "300", "items": [{"used": 30}]}, "cur": "usd"},
                          pv.parse_map("budget=d.cap,spent=d.items.0.used,currency=cur"), now=now)
    assert mapped["rows"][1]["note"] == "$270.00 left of $300.00"
    explicit = pv.normalise({"budget": 10, "spent": 1, "period_end": "2030-01-01T00:00:00+00:00"}, now=now)
    assert "01 Jan" in explicit["rows"][0]["note"] or "2030" in explicit["rows"][0]["note"]
    assert pv.normalise({"spent": 30}, now=now, budget=100.0)["rows"][0]["pct"] == 30

    # no spend in the document: it is derived from local usage this period (mock mode)
    daily = [{"day": (now.date() - timedelta(days=i)).isoformat(), "cost": 2.0}
             for i in range(5, -1, -1)]
    derived = pv.normalise({"budget": 100}, now=now, daily=daily)
    assert derived["summary"]["spent"] == 12.0 and derived["summary"]["burn_basis"] == "6d avg"

    for bad in ({}, {"budget": 300}, {"budget": "x", "spent": 1}, {"budget": 0, "spent": 1},
                {"budget": 300, "spent": -1}, {"budget": 300, "spent": True}, [1, 2], None,
                {"budget": 300, "spent": 1, "period_end": "soon"}):
        fails(lambda bad=bad: pv.normalise(bad, now=now), "", f"bad payload accepted: {bad!r}")


def check_history():
    with environment() as (_, store):
        now = datetime.now().astimezone()
        snap = {"spent": 10.0, "budget": 300.0, "currency": "EUR", "period_end": "2030-01-01T00:00:00+00:00"}
        assert history.load() == [] and history.latest() is None
        assert history.append(snap, now - timedelta(hours=3)) is True
        assert history.append(dict(snap, spent=10.004), now - timedelta(hours=2)) is False, "near duplicate"
        assert history.append(dict(snap, spent=12.5), now - timedelta(hours=1)) is True
        assert history.append(dict(snap, spent=12.5, period_end="2030-02-01T00:00:00+00:00"), now) is True
        assert len(history.load()) == 3 and history.latest()["period_end"].startswith("2030-02")
        lines = (store / "history.jsonl").read_text().splitlines()
        assert set(json.loads(lines[0])) == {"ts", "spent", "budget", "currency", "period_end"}
        with (store / "history.jsonl").open("a") as handle:
            handle.write("not json\n{\"ts\": \"x\"}\n")
        assert len(history.load()) == 3, "corrupt lines are skipped"


def check_usage():
    today = date.today()
    noon = lambda days_ago: datetime.combine(today - timedelta(days=days_ago), time(12)).astimezone()
    rows = [message(noon(0), cost=1.0, input=10, output=5, read=100, write=20),
            message(noon(0), "gpt-5", cost=2.0, input=20, output=10, reasoning=5, read=0, write=0),
            message(noon(1), cost=4.0),
            message(noon(1), cost=9.0, provider="openrouter"),         # other provider: ignored
            message(noon(1), cost=9.0, role="user"),                   # not an assistant message
            message(noon(40), cost=3.0)]                               # outside the daily window
    rows.append((int(noon(0).timestamp() * 1000), "{not json"))        # corrupt rows are skipped
    scratch = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-"))
    db = scratch / "opencode.db"
    make_db(db, rows)
    since = usage.since_ms(datetime.now().astimezone())
    found = usage.load_messages(since, db=db, providers=["github-copilot"])
    assert sorted(m["cost"] for m in found) == [1.0, 2.0, 3.0, 4.0], "provider and role filters"
    assert len(usage.load_messages(since, db=db, providers=["github-copilot", "openrouter"])) == 5

    days = usage.daily(found, today, days=5, factor=2.0)
    assert len(days) == 5 and days[-1]["day"] == today.isoformat() and days[0]["cost"] == 0
    latest, before = days[-1], days[-2]
    assert latest["messages"] == 2 and latest["cost"] == 6.0, "factor scales cost"
    assert (latest["input"], latest["output"], latest["cache"]) == (30, 20, 120)   # output has reasoning
    assert latest["cache_read"] == 100 and latest["cache_write"] == 20
    assert latest["tokens"] == 10 + 5 + 100 + 20 + 20 + 10 + 5
    assert set(latest["models"]) == {"claude-sonnet-5.5", "gpt-5"}
    assert latest["models"]["gpt-5"] == {"tokens": 35, "cost": 4.0, "messages": 1}
    assert before["messages"] == 1 and before["cost"] == 8.0

    start = noon(0) - timedelta(hours=1)
    assert usage.cost_between(found, start, noon(0)) == 3.0 and usage.cost_between(found, noon(0), noon(0)) == 0
    assert usage.cost_between(found, start, noon(0), factor=0.5) == 1.5

    # calibration: portal spend grew 6 while the local cost between the snapshots was 3 -> ratio 2
    snaps = [{"ts": start.isoformat(), "spent": 10.0, "budget": 100.0, "currency": "EUR", "period_end": "p"},
             {"ts": noon(0).isoformat(), "spent": 16.0, "budget": 100.0, "currency": "EUR", "period_end": "p"}]
    assert usage.calibration(snaps, found) == {"ratio": 2.0, "pairs": 1, "portal": 6.0, "local": 3.0}
    assert usage.calibration(snaps[:1], found) is None
    assert usage.calibration([snaps[0], dict(snaps[1], period_end="q")], found) is None, "other period"

    # degradation: missing, corrupt and schema-less databases are reported, not raised through
    fails(lambda: usage.load_messages(0, db=scratch / "nope.db"), "not found", "missing db")
    broken = scratch / "broken.db"
    broken.write_text("this is not sqlite")
    fails(lambda: usage.load_messages(0, db=broken), "unreadable", "corrupt db")
    empty = scratch / "empty.db"
    sqlite3.connect(empty).close()
    fails(lambda: usage.load_messages(0, db=empty), "unreadable", "db without a message table")

    with environment(TOKEN_MONITOR_COST_FACTOR="abc"):
        fails(usage.cost_factor, "COST_FACTOR", "bad factor")
    with environment(TOKEN_MONITOR_PROVIDERS="a, b"):
        assert usage.provider_ids() == ["a", "b"]

    sample = usage.mock_daily(today, 35)
    assert len(sample) == 35 and sample[-1]["day"] == today.isoformat() and sample[0]["cost"] >= 0
    assert all(d["tokens"] == d["input"] + d["output"] + d["cache"] for d in sample)
    return db, noon


def check_sources(now, db, noon):
    # .env parsing and precedence: the real environment wins, nothing else is touched
    parsed = settings.parse_env("# c\nA=1\nexport B = 'two words' \nC=\"x # y\"\nD=3 # note\nbroken\n=z\n")
    assert parsed == {"A": "1", "B": "two words", "C": "x # y", "D": "3"}, parsed
    envfile = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-")) / ".env"
    envfile.write_text("TM_SELFTEST_KEEP=from-file\nTM_SELFTEST_NEW=from-file\n")
    os.environ["TM_SELFTEST_KEEP"] = "from-env"
    try:
        settings.load_dotenv(envfile)
        assert os.environ["TM_SELFTEST_KEEP"] == "from-env", ".env must not override the environment"
        assert os.environ["TM_SELFTEST_NEW"] == "from-file"
        settings.load_dotenv(envfile.with_name("missing"))       # a missing file is fine
    finally:
        os.environ.pop("TM_SELFTEST_KEEP", None)
        os.environ.pop("TM_SELFTEST_NEW", None)

    # mock source: the shipped sample is self-consistent and the budget sits comfortably
    with environment():
        shipped = pv.fetch_copilot()
        assert shipped["sub"] == "mock data" and shipped["daily"] and shipped["summary"]["outlook"] == "ok"
        assert not any(r.get("level") for r in shipped["rows"]), "the sample must not look alarming"
        assert not (settings.CONFIG_DIR.parent / "history").exists(), "mock mode writes no history"
        assert history.load() == []
    with environment(TOKEN_MONITOR_SOURCE="mock") as (config, _):
        config.mkdir(parents=True)
        (config / "mock.json").write_text('{"budget": 100, "spent": 10}')
        assert pv.fetch_copilot()["rows"][0]["pct"] == 10, "the user's mock.json wins"
    with environment(TOKEN_MONITOR_BUDGET="1000"):
        assert pv.fetch_copilot()["summary"]["budget"] == 1000.0, "TOKEN_MONITOR_BUDGET overrides"
    with environment(TOKEN_MONITOR_BUDGET="lots"):
        fails(pv.fetch_copilot, "BUDGET", "non-numeric budget")
    with environment(TOKEN_MONITOR_SOURCE="bogus"):
        fails(pv.fetch_copilot, "bogus", "unknown source")
    with environment(TOKEN_MONITOR_USAGE="bogus"):
        fails(pv.fetch_copilot, "bogus", "unknown usage source")

    # curl source: success is recorded once (deduped), failures fall back to history
    good = "echo '{\"cap\": 50, \"used\": 25}'"
    with environment(TOKEN_MONITOR_SOURCE="curl", TOKEN_MONITOR_JSON_MAP="budget=cap,spent=used",
                     TOKEN_MONITOR_CURL_CMD=good, TOKEN_MONITOR_OPENCODE_DB=str(db)):
        first = pv.fetch_copilot()
        assert first["rows"][0]["pct"] == 50 and not first.get("stale") and first["sub"] == ""
        pv.fetch_copilot()
        assert len(history.load()) == 1, "an identical consecutive fetch is not recorded twice"
        assert first["daily"][-1]["messages"] == 2, "curl mode reads the local OpenCode db"
        assert first["summary"]["calibration"] is None

    with environment(TOKEN_MONITOR_SOURCE="curl", TOKEN_MONITOR_CURL_CMD="echo '<html>login</html>'"):
        def html():
            try:
                pv.fetch_copilot()
            except RuntimeError as failure:
                assert "valid JSON" in str(failure) and "html" not in str(failure)
                raise
        fails(html, "valid JSON", "html login page is not JSON")
    with environment(TOKEN_MONITOR_SOURCE="curl",
                     TOKEN_MONITOR_CURL_CMD="echo SECRET-COOKIE >&2; exit 22"):
        def leaks():
            try:
                pv.fetch_copilot()
            except RuntimeError as failure:
                assert "SECRET" not in str(failure), "no stderr leaks"
                raise
        fails(leaks, "exit 22", "failing command with no history raises")
    with environment(TOKEN_MONITOR_SOURCE="curl"):
        fails(pv.fetch_copilot, "docs/WIRING.md", "curl source without a command")

    # offline: the latest snapshot, labelled, with the local estimate since it overlaid
    snap = pv.snapshot({"budget": 300, "spent": 100}, now=now)
    with environment(TOKEN_MONITOR_SOURCE="curl", TOKEN_MONITOR_CURL_CMD="exit 22",
                     TOKEN_MONITOR_OPENCODE_DB=str(db)):
        history.append(snap, now - timedelta(hours=2))
        gone = now - timedelta(minutes=1)
        recent = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-")) / "recent.db"
        make_db(recent, [message(now - timedelta(hours=3), cost=50.0),     # before the snapshot
                         message(gone, cost=2.5), message(gone, cost=1.5),
                         message(gone, cost=9.0, provider="openrouter")])
        os.environ["TOKEN_MONITOR_OPENCODE_DB"] = str(recent)
        offline = pv.fetch_copilot()
        assert offline["stale"] is True and offline["sub"] == "local estimate"
        assert offline["why"] == "offline \u2014 last portal data 2h ago", offline["why"]
        assert abs(offline["summary"]["estimate"] - 4.0) < 1e-9 and offline["summary"]["spent"] == 104.0
        assert offline["summary"]["portal_spent"] == 100.0
        assert offline["rows"][0]["note"].startswith("~\u20ac104.00 est."), offline["rows"][0]["note"]
        json.dumps(offline)

        os.environ["TOKEN_MONITOR_OPENCODE_DB"] = str(db.with_name("gone.db"))   # no local data either
        bare = pv.fetch_copilot()
        assert bare["stale"] is True and bare["sub"] == "" and bare["summary"]["estimate"] is None
        assert bare["rows"][0]["note"].startswith("\u20ac100.00 spent")
    with environment(TOKEN_MONITOR_SOURCE="curl", TOKEN_MONITOR_CURL_CMD="exit 22"):
        old = dict(snap, period_end=(now - timedelta(days=1)).isoformat())
        history.append(old, now - timedelta(days=3))
        fails(pv.fetch_copilot, "exit 22", "a snapshot of a finished period is not served")


SECRET = "gho_SECRET-TOKEN-123"
PII = ("octocat", "analytics-xyz", "octo@example.com", "avatar")


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def fake_opener(outcome, calls):
    """A stand-in for urlopen: records (url, headers, timeout), then returns/raises ``outcome``."""
    def opener(request, timeout=None):
        calls.append((request.full_url, dict(request.header_items()), timeout))
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome if isinstance(outcome, bytes) else json.dumps(outcome).encode())
    return opener


def github_document(reset, **quota):
    """A response shaped like the real one, PII and all, so leaks would show."""
    premium = {"entitlement": 30000, "credits_used": 1279, "remaining": 28721, "quota_remaining": 28721.3,
               "percent_remaining": 95.7, "overage_permitted": True, "overage_count": 0,
               "unlimited": False, "timestamp_utc": "2026-10-06T15:14:00Z"}
    premium.update(quota)
    return {"login": "octocat", "analytics_tracking_id": "analytics-xyz", "email": "octo@example.com",
            "avatar_url": "https://avatars.example/octocat.png", "copilot_plan": "business",
            "quota_reset_date": reset,
            "quota_snapshots": {"premium_interactions": premium,
                                "chat": {"unlimited": True}, "completions": {"unlimited": True}}}


def no_leaks(*things):
    text = "\n".join(str(t) for t in things)
    for needle in (SECRET,) + PII:
        assert needle not in text, f"{needle!r} leaked into output"


def check_github(now, db):
    from .cli import render_text

    reset = pv.month_bounds(now)[1].date().isoformat()
    document = github_document(reset)
    seen = io.StringIO()
    with contextlib.redirect_stdout(seen), contextlib.redirect_stderr(seen):
        # success: right URL and headers, credits as the unit, numbers mapped, history written once
        with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                         TOKEN_MONITOR_OPENCODE_DB=str(db)) as (_, store):
            calls = []
            pv.OPENER = fake_opener(document, calls)
            entry = pv.fetch_copilot()
            url, headers, timeout = calls[0]
            assert url == "https://api.github.com/copilot_internal/user" and timeout <= 15
            assert headers["Authorization"] == f"token {SECRET}" and headers["Accept"] == "application/json"
            summary = entry["summary"]
            assert (summary["budget"], round(summary["spent"], 2), summary["currency"]) == (300.0, 12.79, "USD")
            assert summary["credits"] == {"spent": 1279.0, "budget": 30000.0, "per_usd": 100.0, "unit": "cr"}
            assert summary["period_end"].startswith(reset) and summary["period_start"][:7] != reset[:7]
            assert entry["sub"] == "business \u00b7 overage allowed" and not entry.get("stale")
            assert entry["rows"][0]["pct"] == 4 and entry["rows"][0]["note"].startswith("$12.79 spent")
            assert entry["rows"][1]["note"] == "28,721 / 30,000 cr left ($287.21 / $300)"
            assert entry["daily"] and entry["daily"][-1]["day"] == now.date().isoformat()
            pv.fetch_copilot()
            lines = (store / "history.jsonl").read_text().splitlines()
            kept = json.loads(lines[0])
            assert len(lines) == 1 and (kept["currency"], kept["spent"], kept["budget"]) == ("cr", 1279.0, 30000.0), \
                "history keeps raw credits, recorded once"
            no_leaks(json.dumps(entry), render_text([dict(entry, key="copilot")]), lines)
        with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                         TOKEN_MONITOR_UNIT="credits", TOKEN_MONITOR_OPENCODE_DB=str(db)):
            pv.OPENER = fake_opener(document, [])
            assert pv.fetch_copilot()["rows"][1]["note"] == "28,721 / 30,000 credits left ($287.21 / $300)"
        with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                         TOKEN_MONITOR_CREDITS_PER_USD="50", TOKEN_MONITOR_BUDGET="900"):
            pv.OPENER = fake_opener(document, [])
            entry = pv.fetch_copilot()
            assert entry["summary"]["budget"] == 900.0, "TOKEN_MONITOR_BUDGET is in displayed (USD) units"
            assert entry["summary"]["credits"]["budget"] == 30000.0 and abs(entry["summary"]["spent"] - 25.58) < 1e-9
        # a budget saved in the config file overrides the API entitlement, in USD
        with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET) as (config, _h):
            pv.OPENER = fake_opener(document, [])
            assert pv.fetch_copilot()["summary"]["budget"] == 300.0
            config.mkdir(parents=True, exist_ok=True)
            (config / "config").write_text("# mine\nOTHER=1\n")
            settings.set_config("TOKEN_MONITOR_BUDGET", "400")
            assert (config / "config").read_text() == "# mine\nOTHER=1\nTOKEN_MONITOR_BUDGET=400\n"
            assert settings.setting_origin("TOKEN_MONITOR_BUDGET") == "config"
            entry = pv.fetch_copilot()
            assert entry["summary"]["budget"] == 400.0 and entry["summary"]["source_budget"] == 300.0
            assert "$400.00" in entry["rows"][1]["note"] or "/ $400)" in entry["rows"][1]["note"]
            assert entry["rows"][0]["pct"] == 3, "percent follows the override (12.79 of 400)"
            assert [l for l in (pv.history.load())][-1]["budget"] == 30000.0, "history keeps raw credits"
            os.environ["TOKEN_MONITOR_BUDGET"] = "500"        # the real environment wins
            try:
                assert pv.fetch_copilot()["summary"]["budget"] == 500.0
                assert settings.setting_origin("TOKEN_MONITOR_BUDGET") == "environment"
            finally:
                del os.environ["TOKEN_MONITOR_BUDGET"]
            settings.set_config("TOKEN_MONITOR_BUDGET", "")
            assert (config / "config").read_text() == "# mine\nOTHER=1\n", "empty value removes the key"
            assert pv.fetch_copilot()["summary"]["budget"] == 300.0
        with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                         TOKEN_MONITOR_BUDGET="0"):
            pv.OPENER = fake_opener(document, [])
            fails(pv.fetch_copilot, "positive", "a zero budget")
        with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                         TOKEN_MONITOR_CREDITS_PER_USD="0"):
            pv.OPENER = fake_opener(document, [])
            entry = pv.fetch_copilot()                       # 0 switches the conversion off: raw credits
            assert entry["rows"][1]["note"] == "28,721 cr left of 30,000 cr" and entry["summary"]["credits"] is None
        for bad in ("lots", "-5"):
            with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                             TOKEN_MONITOR_CREDITS_PER_USD=bad):
                pv.OPENER = fake_opener(document, [])
                fails(pv.fetch_copilot, "CREDITS_PER_USD", f"bad rate {bad}")

        # failures without history raise short, token-free errors
        for outcome, needle in ((urllib.error.HTTPError("u", 401, "no", {}, None), "HTTP 401"),
                                (urllib.error.HTTPError("u", 403, "no", {}, None), "token rejected"),
                                (urllib.error.URLError("dns failure"), "unreachable"),
                                (TimeoutError("slow"), "unreachable"),
                                (b"<html>sso</html>", "valid JSON"),
                                (github_document(reset, unlimited=True), "unlimited"),
                                ({"copilot_plan": "business"}, "premium_interactions"),
                                ([1, 2], "premium_interactions"),
                                (github_document(reset, entitlement=None), "budget")):
            with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET):
                pv.OPENER = fake_opener(outcome, [])
                try:
                    pv.fetch_copilot()
                except RuntimeError as failure:
                    assert needle in str(failure), (needle, str(failure))
                    no_leaks(failure)
                else:
                    raise AssertionError(f"{needle}: no error raised")

        # failures with history: the last snapshot, stale, with the local estimate on top
        taken = now - timedelta(hours=2)
        recent = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-")) / "recent.db"
        make_db(recent, [message(now - timedelta(hours=3), cost=5.0),    # before the snapshot
                         message(now - timedelta(minutes=1), cost=0.04),
                         message(now - timedelta(minutes=1), cost=0.01, provider="openrouter")])
        for outcome in (urllib.error.HTTPError("u", 401, "no", {}, None), urllib.error.URLError("vpn"),
                        document):
            with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                             TOKEN_MONITOR_OPENCODE_DB=str(recent)):
                snap = pv.snapshot({"budget": 30000, "spent": 1000, "period_end": reset}, now=now,
                                   unit="cr")
                history.append(snap, taken)
                pv.OPENER = fake_opener(outcome, [])
                entry = pv.fetch_copilot()
                if outcome is document:                      # control: reachable again, live numbers
                    assert not entry.get("stale") and round(entry["summary"]["spent"], 2) == 12.79
                    assert len(history.load()) == 2
                    continue
                assert entry["stale"] is True and entry["sub"] == "local estimate"
                assert entry["why"] == "offline \u2014 last portal data 2h ago", entry["why"]
                assert abs(entry["summary"]["estimate"] - 0.04) < 1e-9, "local USD cost since the snapshot"
                assert abs(entry["summary"]["spent"] - 10.04) < 1e-9 and entry["summary"]["credits"]["spent"] == 1000.0
                assert entry["rows"][0]["note"].startswith("~$10.04 est.")
                no_leaks(json.dumps(entry), render_text([dict(entry, key="copilot")]),
                         (history.history_file()).read_text())

        # source selection: gh decides when TOKEN_MONITOR_SOURCE is unset
        gh = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-")) / "gh"
        gh.write_text('#!/bin/sh\n[ "$1 $2 $3" = "auth token -h" ] && echo "tok-for-$4" && exit 0\nexit 1\n')
        gh.chmod(0o755)
        with environment(TOKEN_MONITOR_GH_HOST="ghe.example.com", TOKEN_MONITOR_OPENCODE_DB=str(db)):
            pv.GH_BIN, calls = str(gh), []
            pv.OPENER = fake_opener(document, calls)
            entry = pv.fetch_copilot()
            assert calls[0][0] == "https://api.ghe.example.com/copilot_internal/user"
            assert calls[0][1]["Authorization"] == "token tok-for-ghe.example.com", "token comes from gh"
            assert entry["summary"]["currency"] == "USD" and history.load()
        with environment():
            pv.GH_BIN = str(gh)
            gh.write_text("#!/bin/sh\nexit 1\n")                # gh present but not logged in
            assert pv.fetch_copilot()["sub"] == "mock data", "no gh login and no history: mock"
            history.append(pv.snapshot({"budget": 30000, "spent": 10000}, now=now, unit="cr"), now - timedelta(hours=1))
            offline = pv.fetch_copilot()                     # history exists: serve it, flagged
            assert offline["stale"] is True and offline["why"].startswith("offline")
        # spent = entitlement - remaining (what the budget portal shows); credits_used lags and is the fallback
        with environment(TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                         TOKEN_MONITOR_OPENCODE_DB=str(db)) as (_, store):
            lag = github_document(reset, credits_used=2566, remaining=27374, quota_remaining=27374.2,
                                  percent_remaining=91.2)
            pv.OPENER = fake_opener(lag, [])
            summary = pv.fetch_copilot()["summary"]
            assert summary["credits"]["spent"] == 2626.0 and summary["credits"]["budget"] == 30000.0
            assert round(summary["spent"], 2) == 26.26 and summary["budget"] == 300.0
            rows = pv.fetch_copilot()["rows"]
            assert rows[1]["note"] == "27,374 / 30,000 cr left ($273.74 / $300)", rows[1]["note"]
            kept = json.loads((store / "history.jsonl").read_text().splitlines()[-1])
            assert (kept["spent"], kept["basis"], kept["reported"]) == (2626.0, "remaining", 2566.0)
            for quota, want in ((dict(remaining=None), (2625.8, "quota_remaining")),
                                (dict(remaining="x", quota_remaining=None), (2566.0, "credits_used")),
                                (dict(percent_remaining=50.0), (2566.0, "credits_used"))):
                got = pv._spent_of(dict(lag["quota_snapshots"]["premium_interactions"], **quota))
                assert (round(got[0], 1), got[1]) == want, (quota, got)
            # history recorded on the old credits_used basis: no spurious jump across the basis change
            old = {"ts": (now - timedelta(hours=3)).astimezone(timezone.utc).isoformat(), "spent": 2566.0,
                   "budget": 30000.0, "currency": "cr", "period_end": kept["period_end"]}
            new = dict(old, ts=now.astimezone(timezone.utc).isoformat(), spent=2626.0, basis="remaining")
            assert usage.comparable(old, new) is False and usage.comparable(new, dict(new, spent=2700.0)) is True
            assert history.same(dict(kept, basis=None), kept) is False
        with environment(TOKEN_MONITOR_SOURCE="mock", TOKEN_MONITOR_GH_TOKEN=SECRET):
            assert pv.fetch_copilot()["sub"] == "mock data", "explicit mock beats an available gh"
            assert pv.fetch_copilot()["mock"] is True, "mock entries are flagged for the UIs"
        with environment(TOKEN_MONITOR_GH_TOKEN=SECRET, TOKEN_MONITOR_OPENCODE_DB=str(db)):
            calls = []                                       # no SOURCE, no gh binary: the token alone picks github
            pv.OPENER = fake_opener(document, calls)
            auto = pv.fetch_copilot()
            assert calls and auto["sub"] != "mock data" and auto["mock"] is False, "token selects github"
            assert pv.fetch_copilot()["mock"] is False
        with environment(TOKEN_MONITOR_GH_TOKEN=SECRET, TOKEN_MONITOR_OPENCODE_DB=str(db)):
            pv.OPENER = lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
            fails(pv.fetch_copilot, "unreachable", "token set + API down + no history: error, never mock")
    no_leaks(seen.getvalue())


def check_attribution(now):
    """Real daily spend from portal snapshots, local estimates only where there are none."""
    today = now.date()
    at = lambda days_ago, hour: datetime.combine(today - timedelta(days=days_ago),
                                                 time(hour)).astimezone()
    snap = lambda when, spent, **extra: dict({"ts": when.isoformat(), "spent": spent, "budget": 300.0,
                                              "currency": "USD", "period_end": "p"}, **extra)

    def raw(ts, model="m", cost=1.0):
        return {"ts": int(ts.timestamp() * 1000), "total": 10, "input": 5, "output": 5, "reasoning": 0,
                "cache_read": 0, "cache_write": 0, "model": model, "cost": cost}

    messages = [raw(at(2, 12), cost=4.0),                      # before the first reading: estimate
                raw(at(1, 23), cost=1.0), raw(at(0, 9), "n", cost=3.0),   # inside the interval
                raw(at(0, 11), cost=2.0)]                      # after the last reading: estimate
    readings = [snap(at(1, 22), 10.0), snap(at(0, 10), 16.0)]
    rows = usage.attribute(usage.daily(messages, today, days=4, factor=2.0), readings, messages, 2.0)
    by_day = {r["day"]: r for r in rows}
    old, yesterday, now_row = (by_day[(today - timedelta(days=n)).isoformat()] for n in (2, 1, 0))
    assert (old["basis"], old["cost"], old["cost_true"]) == ("estimate", 8.0, 0.0), old
    assert yesterday["basis"] == "portal" and abs(yesterday["cost_true"] - 1.5) < 1e-9, "6 spread 1:3"
    assert now_row["basis"] == "mixed" and abs(now_row["cost_true"] - 4.5) < 1e-9
    assert abs(now_row["cost_est"] - 4.0) < 1e-9 and abs(now_row["cost"] - 8.5) < 1e-9
    assert abs(sum(m["cost"] for m in now_row["models"].values()) - now_row["cost"]) < 1e-9, "models follow"
    assert abs(sum(r["cost_true"] for r in rows) - 6.0) < 1e-9, "the portal delta is kept exactly"
    assert by_day[(today - timedelta(days=3)).isoformat()]["basis"] == "none"

    # no local messages in the interval: the delta is spread over the days by time
    spread = usage.attribute(usage.daily([], today, days=3), [snap(at(1, 12), 10.0), snap(at(0, 12), 14.0)],
                             [], 1.0)
    assert abs(spread[-1]["cost_true"] - 2.0) < 0.2 and abs(spread[-2]["cost_true"] - 2.0) < 0.2, spread

    # intervals across a period change, going backwards, or from another unit are not used
    for bad in (snap(at(0, 10), 16.0, period_end="q"), snap(at(0, 10), 5.0), snap(at(0, 10), 16.0, currency="EUR")):
        rows = usage.attribute(usage.daily(messages, today, days=4, factor=2.0), [readings[0], bad],
                               messages, 2.0)
        assert sum(r["cost_true"] for r in rows) == 0.0 and abs(sum(r["cost"] for r in rows) - 20.0) < 1e-9
    assert usage.attribute(usage.daily(messages, today, days=4, factor=1.0), [], messages, 1.0)[-1]["cost"] == 5.0


def check_calibrated_history(now, db):
    """Provider level: history deltas become true daily cost, and the ratio calibrates the estimates."""
    reset = pv.month_bounds(now)[1].date().isoformat()
    document = github_document(reset)                       # 1,279 credits = $12.79
    rows = [message(now - timedelta(days=2), cost=1.0),     # pre-history
            message(now - timedelta(minutes=5), cost=1.5)]  # between the two readings
    scratch = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-")) / "cal.db"
    make_db(scratch, rows)
    seen = io.StringIO()
    for extra, basis, factor in (({}, "calibrated", None), ({"TOKEN_MONITOR_COST_FACTOR": "3"}, "setting", 3.0)):
        with contextlib.redirect_stdout(seen), environment(
                TOKEN_MONITOR_SOURCE="github", TOKEN_MONITOR_GH_TOKEN=SECRET,
                TOKEN_MONITOR_OPENCODE_DB=str(scratch), **extra):
            first = pv.snapshot({"budget": 30000, "spent": 1000, "period_end": reset}, now=now, unit="cr")
            history.append(dict(first, basis="remaining"), now - timedelta(minutes=10))  # $10.00 ten minutes ago
            pv.OPENER = fake_opener(document, [])
            entry = pv.fetch_copilot()                       # now $12.79: +$2.79 against $1.50 local
            summary = entry["summary"]
            assert summary["factor_basis"] == basis
            expected = 2.79 / 1.5 if factor is None else factor
            assert abs(summary["cost_factor"] - expected) < 1e-9, summary["cost_factor"]
            assert summary["calibration"] and abs(summary["calibration"]["ratio"] - 2.79 / 1.5) < 1e-9
            days = entry["daily"]
            assert abs(sum(d["cost_true"] for d in days) - 2.79) < 1e-9, "true usage = portal delta"
            assert abs(sum(d["cost_est"] for d in days) - 1.0 * expected) < 1e-9, "pre-history, calibrated"
            assert all(d["basis"] in ("none", "estimate", "portal", "mixed") for d in days)
            assert history.load()[-1]["spent"] == 1279.0, "raw credits in history"
    no_leaks(seen.getvalue())


def check_credit_rows(now):
    """Left and lasts-until rows speak credits and dollars together when the source has a credits scale."""
    credits = {"spent": 0, "budget": 30000.0, "per_usd": 100.0, "unit": "cr"}
    snap = lambda spent: {"budget": 300.0, "spent": spent, "currency": "USD", "ts": now.isoformat(),
                          "period_end": pv.month_bounds(now)[1].isoformat(), "credits": credits}
    ok = pv.build_entry(snap(60.0), now)                    # $60 by mid-month: ~3.9 $/day, lasts
    left, lasts = ok["rows"][1]["note"], ok["rows"][2]["note"]
    assert left == "24,000 / 30,000 cr left ($240 / $300)", left
    assert lasts.startswith("Lasts past the reset (about $") and " left on " in lasts and lasts.endswith("\u00b7 spending ~387 cr/day ($3.87/day)"), lasts
    short = pv.build_entry(snap(160.0), now)                # ~10.3 $/day: out before the reset
    note = short["rows"][2]["note"]
    assert short["rows"][2]["level"] == "crit" and note.startswith("At this pace credits run out ~") and "d before the reset" in note
    assert note.endswith("spending ~1,032 cr/day ($10.32/day)"), note
    assert pv.build_entry(snap(310.0), now)["rows"][1]["note"] == "0 / 30,000 cr left ($0 / $300)"
    est = pv.build_entry(snap(100.0), now, estimate=2.5)   # an offline estimate moves the credits too
    assert est["rows"][1]["note"] == "19,750 / 30,000 cr left ($197.50 / $300)", est["rows"][1]["note"]
    plain = pv.normalise({"budget": 300, "spent": 60, "currency": "EUR"}, now=now)   # no credits scale
    assert plain["rows"][1]["note"] == "\u20ac240.00 left of \u20ac300.00" and "cr/day" not in plain["rows"][2]["note"]
    assert plain["rows"][2]["note"].endswith("/day") and "\u20ac" in plain["rows"][2]["note"]


def check_plot(now):
    with environment():
        entry = pv.fetch_copilot()
    svg = plot.render_svg(entry)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and svg.count("<rect") > 40 and "budget" in svg
    assert "local estimate" in svg.lower() and "Lasts until:" in svg

    short = pv.normalise({"budget": 100, "spent": 80}, now=now, daily=entry["daily"])
    short["rows"][2].update(level="crit")
    ET.fromstring(plot.render_svg(short, now.date()))
    nodata = dict(entry, daily=[])
    assert "no local usage data" in plot.render_svg(nodata)
    ET.fromstring(plot.render_svg({"name": "COPILOT", "rows": [], "error": "offline <&>"}))
    series = plot.cumulative([1.0, 2.0, 3.0], 5.0)
    assert series == [0.0, 2.0, 5.0], series
    assert plot.cumulative([1.0, 1.0], 10.0)[-1] == 10.0


def check_chart_math(now):
    assert cm.bar_index(100, 100, 300, 4) == 0 and cm.bar_index(149.9, 100, 300, 4) == 0
    assert cm.bar_index(150, 100, 300, 4) == 1 and cm.bar_index(299.9, 100, 300, 4) == 3
    assert cm.bar_index(99, 100, 300, 4) is None and cm.bar_index(300, 100, 300, 4) is None
    assert cm.bar_index(5, 0, 10, 0) is None
    step, ceiling = cm.nice_scale(23_400_000)
    assert (step, ceiling) == (10_000_000, 40_000_000), (step, ceiling)
    assert cm.nice_scale(14.5)[1] >= 14.5 and cm.nice_scale(0)[1] > 0
    assert cm.axis_tokens(2_500_000) == "2.5M" and cm.axis_tokens(10_000_000) == "10M"
    assert cm.axis_tokens(500) == "500" and cm.axis_tokens(0) == "0"
    today, start = now.date(), now.date() - timedelta(days=15)
    assert (today - cm.history_window("7d", today, start)).days == 6
    assert cm.history_window("period", today, start) == start
    assert (today - cm.history_window("period", today, today)).days == 6    # fresh period: a week
    rows = cm.fill_days([{"day": today.isoformat(), "tokens": 5, "cost": 1.0, "models": {}}],
                        today - timedelta(days=2), today)
    assert [r["tokens"] for r in rows] == [0, 0, 5]
    assert cm.moving_average([2, 4, 6], 2) == [2, 3, 5]
    assert cm.future_days(14, 3) == 3 and cm.future_days(14, 20, 9) == 10
    days = [{"models": {"github-copilot/a": {"tokens": 10, "cost": 1.0}, "b": {"tokens": 90, "cost": 0.5}}},
            {"models": {"a": {"tokens": 5, "cost": 2.0}, "c": {}, "d": {"tokens": 1, "cost": 0.1}}},
            {}, {"models": None}]
    by_cost = cm.aggregate_models(days, "cost", top=2)
    assert [m["name"] for m in by_cost] == ["a", "b", "Other (1)"], by_cost
    assert by_cost[0]["tokens"] == 15 and abs(by_cost[0]["cost"] - 3.0) < 1e-9 and by_cost[2]["other"]
    assert abs(sum(m["share"] for m in by_cost) - 1.0) < 1e-9
    by_tokens = cm.aggregate_models(days, "tokens", top=6)
    assert [m["name"] for m in by_tokens] == ["b", "a", "d"], by_tokens     # "c" has no numbers
    assert cm.aggregate_models([], "cost") == [] and cm.aggregate_models([{"day": "x"}]) == []
    assert cm.short_model("github-copilot/claude-sonnet-5.5") == "claude-sonnet-5.5"
    models = {"models": {"a": {"cost": 1.0, "tokens": 1}, "b": {"cost": 3.0, "tokens": 1}}}
    assert cm.top_models(models, 1)[0][0] == "b"


def check_filters():
    with environment() as (config, _history):
        assert filters.load() == {"providers": set(), "models": set()}, "no file means nothing hidden"
        filters.set_hidden("models", "m/b", True)
        filters.set_hidden("providers", "copilot", True)
        assert filters.is_hidden("models", "m/b") and not filters.is_hidden("models", "m/a")
        assert json.loads((config / "hidden.json").read_text())["models"] == ["m/b"]
        assert not list(config.glob("*.tmp")), "atomic write leaves no temp file"
        filters.set_hidden("models", "m/b", False)
        assert not filters.is_hidden("models", "m/b")
        (config / "hidden.json").write_text("{not json")
        assert filters.load() == {"providers": set(), "models": set()}, "a corrupt file is ignored"

        day = {"day": "2026-10-01", "tokens": 10, "cost": 4.0, "messages": 2,
               "models": {"m/a": {"tokens": 5, "cost": 1.0, "messages": 1},
                          "m/b": {"tokens": 5, "cost": 3.0, "messages": 1}}}
        data = [{"key": "copilot", "name": "C", "rows": [1], "daily": [day]},
                {"key": "x", "name": "X", "rows": [], "error": "gh not found (install)"},
                {"key": "y", "name": "Y", "rows": [], "error": "GitHub API unreachable"}]
        assert [e["key"] for e in core.visible(data)] == ["copilot", "y"], \
            "unconfigured providers vanish, real failures stay"
        filters.set_hidden("models", "m/b", True)
        shown = core.visible(data)[0]["daily"][0]
        assert list(shown["models"]) == ["m/a"] and shown["cost"] == 4.0, "totals stay"
        assert "m/b" in day["models"], "the source data is not modified"
        assert set(core.models_seen(data)) == {"m/a", "m/b"}
        filters.set_hidden("providers", "copilot", True)
        assert [e["key"] for e in core.visible(data)] == ["y"]
        filters.set_hidden("providers", "copilot", False)
        filters.set_hidden("models", "m/b", False)
        data[0]["model_providers"] = {"m/a": "p1", "m/b": "p2"}
        filters.set_hidden("providers", "p2", True)
        assert list(core.visible(data)[0]["daily"][0]["models"]) == ["m/a"], "a hidden OpenCode provider hides its models"
        assert usage.model_providers([{"model": "m", "provider": "p"}]) == {"m": "p"}
        assert usage.model_providers([], [{"models": {"x": {}}}]) == {"x": "github-copilot"}
        from . import web
        seen = {"m/a": {"cost": 3.0, "messages": 2}, "m/b": {"cost": 1.0, "messages": 1}, "m/c": {"cost": 0.5, "messages": 1}}
        hid = {"providers": {"p2"}, "models": {"m/b", "gone"}}
        grp = web.build_groups(data, hid, seen, ["m/a", "m/b", "m/c", "gone"])
        by = {g["id"]: g for g in grp}
        assert [m["name"] for m in by["p1"]["models"]] == ["m/a"] and not by["p1"]["hidden"]
        assert by["p2"]["hidden"] and by["p2"]["models"][0]["hidden"], "provider hidden, model keeps its own state"
        assert grp[-1]["id"] is None and grp[-1]["kind"] is None and [m["name"] for m in grp[-1]["models"]] == ["m/c", "gone"], "unmapped models go last"


def check_hourly():
    now = datetime(2026, 10, 7, 14, 30).astimezone()                  # 14:30 local
    at = lambda days, hour, minute=0: int(datetime.combine(now.date() - timedelta(days=days),
                                                           time(hour, minute)).astimezone().timestamp() * 1000)
    parsed = lambda ts, cost, model="m/a": dict(ts=ts, cost=cost, model=model, total=1350, input=100,
                                                output=50, reasoning=0, cache_read=1000, cache_write=200)
    msgs = [parsed(at(0, 14, 5), 1.0), parsed(at(0, 14, 59), 2.0, "m/b"),
            parsed(at(1, 23, 40), 0.5),                                # before midnight
            parsed(at(0, 0, 10), 0.25),                                # just after midnight
            parsed(at(2, 10), 9.0), parsed(at(0, 15, 0), 5.0)]         # outside the window / next hour
    rows = usage.hourly(msgs, now, 24, factor=2.0)
    assert len(rows) == 24 and rows[-1]["hour"].endswith("T14:00:00") and rows[0]["hour"].endswith("T15:00:00")
    assert rows[-1]["messages"] == 2 and abs(rows[-1]["cost"] - 6.0) < 1e-9, "factor applied, one hour bucket"
    assert set(rows[-1]["models"]) == {"m/a", "m/b"} and rows[-1]["basis"] == "estimate"
    by_hour = {r["hour"][11:13]: r for r in rows}
    assert by_hour["23"]["messages"] == 1 and by_hour["00"]["messages"] == 1, "buckets across midnight"
    assert rows[-1]["cost_true"] == 0.0 and sum(r["messages"] for r in rows) == 4, "no portal deltas, zeros filled"
    assert sum(1 for r in rows if not r["messages"]) == 21
    # a clock change cannot lose or merge buckets: the count is in absolute hours
    assert len(usage.hourly([], datetime(2026, 11, 1, 1, 30).astimezone(), 72)) == 72

    assert cm.hour_rows(rows, "24h") == rows and len(cm.hour_rows(rows, "72h")) == 24
    assert cm.hour_rows(None, "24h") == [] and cm.hour_label(rows[-1]) == "14:00"
    assert cm.hour_label(rows[-1], True) == "14:00 \u00b7 07 Oct"
    ticks = cm.hour_ticks(rows, 3)
    assert ticks[-1] == (21, "12:00") and ticks[-2][1] == "09:00", "labels count back from now, 3 -> every 3h"
    assert any(label == "07 Oct" for _i, label in cm.hour_ticks(rows, 1)), "midnight shows the date"
    assert cm.history_window("7d", date(2026, 10, 7), date(2026, 10, 1)) == date(2026, 10, 1)
    top = cm.aggregate_models(rows, "cost")
    assert top[0]["name"] == "b" and abs(top[0]["cost"] - 4.0) < 1e-9 and abs(top[1]["cost"] - 3.5) < 1e-9

    with environment():
        filters.set_hidden("models", "m/b", True)
        entry = core.visible([{"key": "copilot", "name": "C", "rows": [1], "daily": [], "hourly": rows}])[0]
        assert all("m/b" not in r["models"] for r in entry["hourly"]) and entry["hourly"][-1]["cost"] == 6.0
        assert "m/b" in rows[-1]["models"], "source rows untouched"


def check_mock_hourly():
    """Mock hours add up to their mock days (so a time slice and the daily view agree)."""
    now = datetime(2026, 10, 7, 14, 30).astimezone()
    days = usage.mock_daily(now.date(), 5)
    rows = usage.mock_hourly(days, now, 72)
    assert len(rows) == 72 and rows[-1]["hour"].endswith("T14:00:00")
    for day in days[3:4]:                                   # yesterday: a whole day inside the 72 hours
        mine = [r for r in rows if r["day"] == day["day"]]
        for key in ("tokens", "input", "output", "cache_read", "messages"):
            assert sum(r[key] for r in mine) == day[key], key
        assert abs(sum(r["cost"] for r in mine) - day["cost"]) < 1e-6
        for name, count in day["skills"].items():
            assert sum(r["skills"].get(name, 0) for r in mine) == count
    assert all(r["tokens"] == r["input"] + r["output"] + r["cache"] for r in rows)
    assert not any(r["tokens"] for r in rows if r["day"] == now.date().isoformat() and int(r["hour"][11:13]) > 14)


def check_web():
    import http.client
    import threading

    from . import web
    secret = "ghp_SELFTEST_SECRET_TOKEN"
    now = datetime.now().astimezone()
    parsed = dict(ts=int(now.timestamp() * 1000), cost=2.0, model="m/a", total=10, input=4, output=3,
                  reasoning=0, cache_read=2, cache_write=1)
    entry = {"name": "COPILOT", "sub": "business", "rows": [{"label": "budget", "pct": 5, "note": "x"}],
             "summary": {"budget": 300.0, "source_budget": 300.0, "spent": 15.0, "currency": "USD"},
             "daily": usage.daily([parsed], now.date()), "hourly": usage.hourly([parsed], now, 72),
             "model_providers": {"m/a": "github-copilot"}}
    saved = core.PROVIDERS, cache.CACHE_FILE
    with environment(TOKEN_MONITOR_GH_TOKEN=secret) as (config, _history):
        cache.CACHE_FILE = config.parent / "last-good.json"
        core.PROVIDERS = (("copilot", lambda: dict(entry)),
                          ("other", lambda: (_ for _ in ()).throw(RuntimeError("gh not found (install)"))))
        web._cache.update(raw=None, at=0.0)
        server = web.make_server("127.0.0.1", 0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log = io.StringIO()

        def call(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            payload = body if isinstance(body, (str, bytes)) else (json.dumps(body) if body is not None else None)
            sent = {"Content-Type": "application/json"} if method == "POST" else {}
            connection.request(method, path, payload, {**sent, **(headers or {})})
            reply = connection.getresponse()
            text = reply.read().decode()
            connection.close()
            return reply.status, text, reply

        try:
            with contextlib.redirect_stdout(log):
                assert call("GET", "/healthz")[:2] == (200, "ok")
                status, text, reply = call("GET", "/api/data")
                data = json.loads(text)
                assert status == 200 and [e["key"] for e in data["providers"]] == ["copilot"], \
                    "unconfigured providers are left out"
                shown = data["providers"][0]
                assert len(shown["daily"]) == 35 and len(shown["hourly"]) == 72 and shown["summary"]["budget"] == 300.0
                assert reply.getheader("Content-Security-Policy") and reply.getheader("Access-Control-Allow-Origin") is None
                index = call("GET", "/")
                assert index[0] == 200 and "<title>Token Monitor</title>" in index[1]
                assert call("GET", "/app.js")[0] == 200 and call("GET", "/style.css")[0] == 200

                # static serving never leaves token_monitor/web/
                for bad in ("/../web.py", "/%2e%2e/web.py", "/..%2fweb.py", "/web/../../settings.py", "//etc/passwd",
                            "/missing.html", "/api/nope"):
                    assert call("GET", bad)[0] == 404, f"{bad} must not be served"

                # settings: validation, persistence, precedence
                assert call("POST", "/api/settings", "{}")[0] == 400
                assert call("POST", "/api/settings", "not json")[0] == 400
                for bad in ("abc", "0", "-5", "nan", "inf", "1e99"):
                    status, text, _r = call("POST", "/api/settings", {"budget": bad})
                    assert status == 400 and "greater than 0" in text, f"budget {bad!r} must be refused"
                assert not (config / "config").exists()
                status, text, _r = call("POST", "/api/settings", {"budget": " 400,5 "})
                budget = json.loads(text)["budget"]
                assert status == 200 and budget["override"] == "400.5" and budget["source"] == 300.0
                assert (config / "config").read_text() == "TOKEN_MONITOR_BUDGET=400.5\n"
                assert json.loads(call("POST", "/api/settings", {"budget": ""})[1])["budget"]["override"] == ""
                assert not (config / "config").read_text().strip(), "an empty value removes the key"
                os.environ["TOKEN_MONITOR_BUDGET"] = "123"
                try:
                    status, text, _r = call("POST", "/api/settings", {"budget": "5"})
                    assert status == 409 and "environment" in text
                    assert json.loads(call("GET", "/api/settings")[1])["budget"]["readonly"] is True
                finally:
                    del os.environ["TOKEN_MONITOR_BUDGET"]

                # hiding providers and models
                for bad in ({"hide": {"kind": "x", "id": "a", "hidden": True}}, {"hide": {"kind": "models", "id": "", "hidden": True}},
                            {"hide": {"kind": "models", "id": "a", "hidden": "yes"}}, {"hide": "models"}):
                    assert call("POST", "/api/settings", bad)[0] == 400
                call("POST", "/api/settings", {"hide": {"kind": "models", "id": "m/a", "hidden": True}})
                hidden = json.loads(call("GET", "/api/data")[1])["providers"][0]
                assert all("m/a" not in r["models"] for r in hidden["hourly"] + hidden["daily"]) and hidden["hourly"][-1]["cost"] == 2.0
                listing = json.loads(call("GET", "/api/settings")[1])
                assert [p["key"] for p in listing["providers"]] == ["copilot"] and listing["models"][0] == \
                    {"name": "m/a", "hidden": True, "cost": 2.0, "messages": 1}
                groups = listing["groups"]
                assert [(g["id"], g["hidden"], [m["name"] for m in g["models"]]) for g in groups] == \
                    [("github-copilot", False, ["m/a"])], groups
                call("POST", "/api/settings", {"hide": {"kind": "models", "id": "m/a", "hidden": False}})
                call("POST", "/api/settings", {"hide": {"kind": "providers", "id": "github-copilot", "hidden": True}})
                shown = json.loads(call("GET", "/api/data")[1])["providers"][0]
                assert all("m/a" not in r["models"] for r in shown["daily"] + shown["hourly"]), "hidden provider hides its models"
                assert shown["daily"][-1]["cost"] == 2.0, "totals stay"
                assert json.loads(call("GET", "/api/settings")[1])["groups"][0]["hidden"] is True
                call("POST", "/api/settings", {"hide": {"kind": "providers", "id": "github-copilot", "hidden": False}})
                call("POST", "/api/settings", {"hide": {"kind": "providers", "id": "copilot", "hidden": True}})
                assert json.loads(call("GET", "/api/data")[1])["providers"] == []
                assert any(g["id"] == "copilot" and g["hidden"] for g in json.loads(call("GET", "/api/settings")[1])["groups"]), \
                    "a hidden dashboard provider can still be switched back on"

                # POST rules: json only, same origin only, small bodies
                assert call("POST", "/api/refresh", "{}", {"Content-Type": "text/plain"})[0] == 415
                assert call("POST", "/api/refresh", "{}", {"Origin": "http://evil.example"})[0] == 403
                assert call("POST", "/api/refresh", "x" * 5000)[0] == 413
                assert call("POST", "/api/refresh", "{}", {"Origin": f"http://127.0.0.1:{port}"})[0] == 200
                assert call("POST", "/api/nope", "{}")[0] == 404
                everything = "".join(call("GET", path)[1] for path in ("/api/data", "/api/settings"))
            assert secret not in everything and secret not in log.getvalue(), "no token in responses or the log"
            assert "?" not in log.getvalue() and "GET /api/data" in log.getvalue()
        finally:
            server.shutdown()
            server.server_close()
            core.PROVIDERS, cache.CACHE_FILE = saved
            web._cache.update(raw=None, at=0.0)


def check_skills():
    """Skill loads: parsing, malformed rows, provider filter, aggregation per day/hour, mock data."""
    now = datetime.now().astimezone()
    stamp = lambda hours_ago: int((now - timedelta(hours=hours_ago)).timestamp() * 1000)
    scratch = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-"))
    db = scratch / "opencode.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE message (id text PRIMARY KEY, session_id text, time_created integer, "
                       "time_updated integer, data text)")
    connection.execute("CREATE TABLE part (id text PRIMARY KEY, message_id text, session_id text, "
                       "time_created integer, time_updated integer, data text)")
    for mid, provider in (("m1", "github-copilot"), ("m2", "openrouter"), ("m3", None)):
        connection.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                           (mid, "s", 0, 0, json.dumps({"role": "assistant", "providerID": provider} if provider
                                                       else {"role": "assistant"})))
    secret = "SECRET-SKILL-BODY"

    def skill(name, status="completed", tool="skill", output=secret):
        return json.dumps({"type": "tool", "tool": tool, "state": {"status": status, "input": {"name": name},
                                                                    "output": output}})
    parts = [("m1", stamp(1), skill("alpha")), ("m1", stamp(2), skill("alpha")), ("m1", stamp(30), skill("beta")),
             ("m1", stamp(3), skill("gamma", status="error")),         # did not complete
             ("m1", stamp(3), skill("bash", tool="bash")),             # another tool
             ("m1", stamp(3), json.dumps({"type": "tool", "state": {"status": "completed"}})),   # no tool field
             ("m1", stamp(3), json.dumps({"type": "tool", "tool": "skill", "state": {"status": "completed"}})),  # no name
             ("m1", stamp(3), "{not json \"skill\""),                  # malformed
             ("m2", stamp(1), skill("other-provider")),                # filtered out by provider
             ("m3", stamp(1), skill("alpha")),                         # message without provider: kept
             ("gone", stamp(1), skill("beta"))]                        # message missing: kept
    for number, (mid, created, data) in enumerate(parts):
        connection.execute("INSERT INTO part VALUES (?,?,?,?,?,?)", (f"p{number}", mid, "s", created, created, data))
    connection.commit()
    connection.close()
    found = usage.load_skills(usage.since_ms(now), db=db, providers=["github-copilot"])
    names = sorted(item["name"] for item in found)
    assert names == ["alpha", "alpha", "alpha", "beta", "beta"], names
    assert all(set(item) == {"ts", "name"} for item in found), "only the name and time leave the database"
    assert secret not in json.dumps(found)
    assert [i["ts"] for i in found] == sorted(i["ts"] for i in found), "oldest first"
    assert len(usage.load_skills(usage.since_ms(now), db=db, providers=["github-copilot", "openrouter"])) == 6
    assert sorted(i["name"] for i in usage.load_skills(stamp(5), db=db, providers=["github-copilot"])) == ["alpha"] * 3 + ["beta"]
    # a database without a part table, or without a message table, still works; a missing one is an error
    bare = scratch / "bare.db"
    sqlite3.connect(bare).close()
    assert usage.load_skills(0, db=bare) == []
    nomsg = scratch / "nomsg.db"
    link = sqlite3.connect(nomsg)
    link.execute("CREATE TABLE part (id text, message_id text, session_id text, time_created integer, time_updated integer, data text)")
    link.execute("INSERT INTO part VALUES ('p','m','s',?,?,?)", (stamp(1), stamp(1), skill("alpha")))
    link.commit()
    link.close()
    assert [i["name"] for i in usage.load_skills(0, db=nomsg)] == ["alpha"]
    fails(lambda: usage.load_skills(0, db=scratch / "nope.db"), "not found", "missing db")

    today = now.date()
    days = usage.daily([], today, days=3)
    hours = usage.hourly([], now, 24)
    last = usage.attach_skills(days, hours, found)
    assert sum(sum(r["skills"].values()) for r in days) == 5, "every load lands on one day row"
    assert days[-1]["skills"].get("alpha", 0) + days[-2]["skills"].get("alpha", 0) == 3
    assert sum(r["skills"].get("alpha", 0) for r in hours) == 3, "hourly rows count the same loads"
    assert sum(r["skills"].get("beta", 0) for r in hours) == 1, "the 30-hour-old load is outside the 24 hourly rows"
    assert set(last) == {"alpha", "beta"}, "last use per skill"
    assert usage.attach_skills(days[:1], None, []) == {} and days[0]["skills"] == {}
    sample = usage.mock_daily(today, 35)
    assert any(r["skills"] for r in sample) and set(usage.mock_skill_last(sample)) >= {"caveman"}


def check_readonly_db():
    """A WAL database on a read-only folder without its -shm file (the Docker mount) still reads."""
    if os.geteuid() == 0:
        return                                    # root ignores directory permissions
    scratch = Path(tempfile.mkdtemp(prefix="token-monitor-selftest-"))
    db = scratch / "ro" / "opencode.db"
    db.parent.mkdir()
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA journal_mode=wal")
    connection.execute("CREATE TABLE message (time_created INTEGER, data TEXT)")
    stamp, data = message(datetime.now().astimezone())
    connection.execute("INSERT INTO message VALUES (?, ?)", (stamp, json.dumps(data)))
    connection.commit()
    connection.close()                            # closing checkpoints and removes -wal/-shm
    for extra in db.parent.glob("opencode.db-*"):
        extra.unlink()
    db.parent.chmod(0o555)
    try:
        assert len(usage.load_messages(0, db=db)) == 1
    finally:
        db.parent.chmod(0o755)


def run_copilot_checks():
    check_filters()
    check_web()
    check_readonly_db()
    check_skills()
    check_hourly()
    check_mock_hourly()
    now = datetime(2026, 10, 16, 12, 0).astimezone()     # mid-month, local time
    check_basics(now)
    check_outlook(now)
    check_entry(now)
    check_history()
    db, noon = check_usage()
    check_sources(datetime.now().astimezone(), db, noon)
    check_github(datetime.now().astimezone(), db)
    check_credit_rows(now)
    check_attribution(datetime.now().astimezone())
    check_calibrated_history(datetime.now().astimezone(), db)
    check_plot(now)
    check_chart_math(now)
