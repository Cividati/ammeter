"""Local usage: what OpenCode itself recorded about Copilot requests, with no network or VPN.

Reads OpenCode's SQLite database read-only (``~/.local/share/opencode/opencode.db``, override
``TOKEN_MONITOR_OPENCODE_DB``). ``message.data`` is JSON; assistant messages carry ``providerID``,
``modelID``, ``cost`` and ``tokens``. Only providers in ``TOKEN_MONITOR_PROVIDERS`` (default
``github-copilot``) count.

``cost`` is **OpenCode's own estimate in USD, not the billed credits**. ``TOKEN_MONITOR_COST_FACTOR``
(default 1) scales it towards the portal's unit; :func:`calibration` suggests a value by comparing
local cost with how much the portal's spend grew between recorded snapshots.

Per-day rows (local calendar days)::

    {"day": "2026-10-06", "tokens": total, "input": n, "output": n (+reasoning), "cache": read+write,
     "cache_read": n, "cache_write": n, "cost": float, "messages": n,
     "models": {"model-id": {"tokens": n, "cost": float, "messages": n}}}
"""
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .settings import REPO, setting

DEFAULT_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
MOCK_FILE = REPO / "data" / "mock-history.json"
LOOKBACK_DAYS = 62      # enough for the current period plus the previous month's tail


def db_path():
    return Path(setting("TOKEN_MONITOR_OPENCODE_DB") or DEFAULT_DB).expanduser()


def provider_ids():
    return [p.strip() for p in (setting("TOKEN_MONITOR_PROVIDERS") or "github-copilot").split(",")
            if p.strip()]


def cost_factor(default=1.0):
    """TOKEN_MONITOR_COST_FACTOR, else ``default`` (the github source passes 100: 1 credit ~ $0.01)."""
    raw = setting("TOKEN_MONITOR_COST_FACTOR")
    try:
        return float(raw) if raw else default
    except ValueError:
        raise RuntimeError("TOKEN_MONITOR_COST_FACTOR is not a number") from None


def _tokens(data):
    tokens = data.get("tokens") or {}
    cache = tokens.get("cache") or {}
    parts = {"input": tokens.get("input"), "output": tokens.get("output"),
             "reasoning": tokens.get("reasoning"), "cache_read": cache.get("read"),
             "cache_write": cache.get("write")}
    parts = {k: int(v or 0) for k, v in parts.items()}
    parts["total"] = int(tokens.get("total") or sum(parts.values()))
    return parts


def _connect(db):
    """Open the OpenCode database read-only; RuntimeError with a short reason when it cannot be read."""
    # mode=ro is enough on a normal disk. A WAL database on a read-only mount (the Docker image) can
    # fail to open (or report a readonly database) when its -shm file is missing; immutable=1 then reads the main file as it is
    # (recent, not yet checkpointed rows may be missing) instead of giving up.
    for flags in ("mode=ro", "immutable=1"):
        try:
            connection = sqlite3.connect(f"file:{db}?{flags}", uri=True, timeout=2)
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            break
        except sqlite3.Error as failure:
            if flags == "immutable=1":
                raise RuntimeError(f"opencode db unreadable ({type(failure).__name__}: {failure})") from None
    return connection


def load_messages(since_ms, db=None, providers=None):
    """Assistant messages from the allowed providers created at/after ``since_ms``, oldest first.

    Raises RuntimeError with a short reason when the database is missing, locked or unreadable.
    """
    db = Path(db) if db else db_path()
    wanted = set(providers or provider_ids())
    if not db.is_file():
        raise RuntimeError(f"opencode db not found ({db})")
    connection = _connect(db)
    try:
        try:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # newer OpenCode writes session_message (provider/model nested under "model");
            # older versions wrote message. Read both so history spans the upgrade.
            if not tables & {"message", "session_message"}:
                raise sqlite3.OperationalError("no message table")
            rows = []
            if "message" in tables:
                rows += connection.execute("SELECT time_created, data FROM message "
                                           "WHERE time_created >= ?", (int(since_ms),)).fetchall()
            if "session_message" in tables:
                rows += connection.execute("SELECT time_created, data FROM session_message "
                                           "WHERE type = 'assistant' AND time_created >= ?",
                                           (int(since_ms),)).fetchall()
            rows.sort(key=lambda r: r[0])
        finally:
            connection.close()
    except sqlite3.Error as failure:
        raise RuntimeError(f"opencode db unreadable ({type(failure).__name__}: {failure})") from None
    found = []
    for created, raw in rows:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        model = data.get("model") if isinstance(data.get("model"), dict) else {}
        provider = data.get("providerID") or model.get("providerID")
        if data.get("role", "assistant") != "assistant" or provider not in wanted:
            continue
        if "cost" not in data and "tokens" not in data:     # in-flight message, nothing recorded yet
            continue
        found.append(dict(_tokens(data), ts=int(created), provider=provider,
                          model=data.get("modelID") or model.get("id") or "unknown",
                          cost=float(data.get("cost") or 0)))
    return found


def load_skills(since_ms, db=None, providers=None):
    """Skill loads OpenCode recorded: ``[{"ts": ms, "name": skill}]``, oldest first.

    A skill load is a ``part`` row whose JSON is ``{"type": "tool", "tool": "skill", "state":
    {"status": "completed", "input": {"name": ...}}}``. Only the skill name and the timestamp leave
    SQLite (``json_extract`` runs inside it): ``state.output`` holds the skill's full text and is never
    read. The part's message is looked up to apply the provider filter, but a load whose message
    cannot be found, or has no provider, is kept. Raises RuntimeError like :func:`load_messages`; a
    database without a ``part`` table gives an empty list.
    """
    db = Path(db) if db else db_path()
    wanted = set(providers or provider_ids())
    if not db.is_file():
        raise RuntimeError(f"opencode db not found ({db})")
    connection = _connect(db)
    try:
        try:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "part" not in tables:
                return []
            joined = "message" in tables
            rows = connection.execute(
                "SELECT p.time_created, "
                "CASE WHEN json_valid(p.data) THEN json_extract(p.data, '$.tool') END, "
                "CASE WHEN json_valid(p.data) THEN json_extract(p.data, '$.state.status') END, "
                "CASE WHEN json_valid(p.data) THEN json_extract(p.data, '$.state.input.name') END, "
                + ("CASE WHEN json_valid(m.data) THEN json_extract(m.data, '$.providerID') END "
                   "FROM part p LEFT JOIN message m ON m.id = p.message_id "
                   if joined else "NULL FROM part p ")
                + "WHERE p.time_created >= ? AND instr(p.data, '\"skill\"') > 0 "
                "ORDER BY p.time_created", (int(since_ms),)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as failure:
        raise RuntimeError(f"opencode db unreadable ({type(failure).__name__}: {failure})") from None
    return [{"ts": int(created), "name": name.strip()} for created, tool, status, name, provider in rows
            if tool == "skill" and status == "completed" and isinstance(name, str) and name.strip()
            and (provider is None or provider in wanted)]


def model_providers(messages, daily_rows=None, mock_provider="github-copilot"):
    """{model: OpenCode providerID} from the messages (the latest provider wins when a model id is
    seen under several). With no messages (mock rows) every model in ``daily_rows`` maps to ``mock_provider``."""
    found = {m["model"]: m["provider"] for m in messages if m.get("provider")}
    if not messages:
        for row in daily_rows or []:
            for name in row.get("models") or {}:
                found.setdefault(name, mock_provider)
    return found


def attach_skills(daily_rows, hourly_rows, skills):
    """Put ``skills`` ({name: loads}) on every day and hour row; returns ``{name: last use, local ISO}``."""
    for row in list(daily_rows or []) + list(hourly_rows or []):
        row["skills"] = {}
    days = {row["day"]: row for row in daily_rows or []}
    first = {row["hour"]: row for row in hourly_rows or []}
    last = {}
    for item in skills:
        moment = datetime.fromtimestamp(item["ts"] / 1000)
        for row in (days.get(moment.date().isoformat()), first.get(moment.replace(minute=0, second=0, microsecond=0).isoformat())):
            if row is not None:
                row["skills"][item["name"]] = row["skills"].get(item["name"], 0) + 1
        if item["name"] not in last or moment.isoformat() > last[item["name"]]:
            last[item["name"]] = moment.isoformat(timespec="seconds")
    return last


def mock_skill_last(daily_rows):
    """Last-use times for mock rows (noon of the latest day each skill appears on)."""
    last = {}
    for row in daily_rows:
        for name in row.get("skills") or {}:
            last[name] = f"{row['day']}T12:00:00"
    return last


def since_ms(now, days=LOOKBACK_DAYS):
    return int((now - timedelta(days=days)).timestamp() * 1000)


def _day_of(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000).date()


def _blank(day):
    return {"day": day.isoformat(), "tokens": 0, "input": 0, "output": 0, "cache": 0,
            "cache_read": 0, "cache_write": 0, "cost": 0.0, "cost_true": 0.0, "cost_est": 0.0,
            "basis": "none", "messages": 0, "models": {}, "skills": {}}


def _add(row, message, factor):
    """Fold one message into a day or hour row (local estimate only)."""
    cost = message["cost"] * factor
    row["tokens"] += message["total"]
    row["input"] += message["input"]
    row["output"] += message["output"] + message["reasoning"]
    row["cache_read"] += message["cache_read"]
    row["cache_write"] += message["cache_write"]
    row["cache"] += message["cache_read"] + message["cache_write"]
    row["cost"] += cost
    row["cost_est"] += cost
    row["basis"] = "estimate"
    row["messages"] += 1
    model = row["models"].setdefault(message["model"], {"tokens": 0, "cost": 0.0, "messages": 0})
    model["tokens"] += message["total"]
    model["cost"] += cost
    model["messages"] += 1


def daily(messages, today=None, days=35, factor=1.0):
    """One row per local calendar day for the last ``days`` days (oldest first), zeros included."""
    today = today or date.today()
    rows = {today - timedelta(days=i): _blank(today - timedelta(days=i)) for i in range(days)}
    for message in messages:
        row = rows.get(_day_of(message["ts"]))
        if row is None:
            continue
        _add(row, message, factor)
    return [rows[day] for day in sorted(rows)]


def hourly(messages, now=None, hours=24, factor=1.0):
    """One row per local clock hour for the last ``hours`` hours, ending with the current hour.

    Same shape as :func:`daily` rows plus ``"hour"`` (naive local ISO time of the hour's start).
    Buckets are counted in absolute hours from the first one, so a clock change cannot merge or
    drop a bucket. Cost is the local estimate only (``basis`` "estimate"); the portal's spend is
    far too sparse to be spread over hours.
    """
    now = now or datetime.now().astimezone()
    last = int(now.timestamp()) - now.minute * 60 - now.second
    first = last - (hours - 1) * 3600
    rows = []
    for i in range(hours):
        start = datetime.fromtimestamp(first + i * 3600)
        row = _blank(start.date())
        row["hour"] = start.replace(minute=0, second=0, microsecond=0).isoformat()
        rows.append(row)
    for message in messages:
        slot = (message["ts"] // 1000 - first) // 3600
        if 0 <= slot < hours:
            _add(rows[slot], message, factor)
    return rows


def cost_between(messages, start, end, factor=1.0):
    """Estimated cost of messages with ``start < ts <= end`` (aware datetimes), scaled by factor."""
    lo, hi = start.timestamp() * 1000, end.timestamp() * 1000
    return sum(m["cost"] for m in messages if lo < m["ts"] <= hi) * factor


def comparable(before, after):
    """Whether two snapshots measure spend the same way: one period, one unit, one basis.

    A change of basis (old history from ``credits_used``, new from ``remaining``) would show up as a
    spurious jump in spend, so such a pair is skipped.
    """
    return (before["period_end"] == after["period_end"] and before["currency"] == after["currency"]
            and before.get("basis") == after.get("basis"))


def calibration(snapshots, messages):
    """How the portal's spend relates to local cost, from consecutive snapshots of one period.

    Snapshots must share a unit with the local cost (USD; the github source converts credits first).
    Returns ``{"ratio": portal growth / raw local cost, "pairs": n, "portal": ..., "local": ...}`` or
    None when there is not yet an interval where both grew. The ratio is what
    ``TOKEN_MONITOR_COST_FACTOR`` should be, and is applied automatically only once ``local`` is large
    enough to be meaningful (see providers.py).
    """
    portal = local = 0.0
    pairs = 0
    for before, after in zip(snapshots, snapshots[1:]):
        if not comparable(before, after):
            continue
        grew = after["spent"] - before["spent"]
        cost = cost_between(messages, datetime.fromisoformat(before["ts"]),
                            datetime.fromisoformat(after["ts"]))
        if grew > 0 and cost > 0:
            portal, local, pairs = portal + grew, local + cost, pairs + 1
    return {"ratio": portal / local, "pairs": pairs, "portal": portal, "local": local} if pairs else None


def _day_pieces(start, end):
    """[(day, seconds)] for the local calendar days covered by the naive-local span start..end."""
    pieces, cursor = [], start
    while cursor < end:
        midnight = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time())
        stop = min(end, midnight)
        pieces.append((cursor.date().isoformat(), (stop - cursor).total_seconds()))
        cursor = stop
    return pieces


def attribute(rows, snapshots, messages, factor=1.0):
    """Put real spend on the daily rows: portal deltas where history exists, estimates elsewhere.

    ``snapshots`` (oldest first, one unit, same unit as the local cost) are portal readings of a
    cumulative spend. Between two consecutive readings of one period the spend grew by an exact
    amount; it is spread over the local days of that interval in proportion to the local cost of the
    messages in it (by time when there were none). Messages outside every such interval - before the
    first reading, between periods, since the last reading - are priced at their local estimate times
    ``factor``. Each row gets ``cost_true``, ``cost_est``, their sum as ``cost``, and a ``basis`` of
    ``portal``, ``mixed``, ``estimate`` or ``none``. Rows are modified in place and returned.
    """
    by_day = {row["day"]: row for row in rows}
    for row in rows:
        row["cost_true"] = row["cost_est"] = 0.0
    spans = []
    for before, after in zip(snapshots, snapshots[1:]):
        if not comparable(before, after):
            continue
        t0, t1 = datetime.fromisoformat(before["ts"]), datetime.fromisoformat(after["ts"])
        grew = after["spent"] - before["spent"]
        if t1 <= t0 or grew < 0:
            continue
        spans.append((t0.timestamp() * 1000, t1.timestamp() * 1000))
        weights = {}
        for message in messages:
            if spans[-1][0] < message["ts"] <= spans[-1][1]:
                day = _day_of(message["ts"]).isoformat()
                weights[day] = weights.get(day, 0.0) + message["cost"]
        if sum(weights.values()) <= 0:
            weights = dict(_day_pieces(t0.astimezone().replace(tzinfo=None),
                                       t1.astimezone().replace(tzinfo=None)))
        total = sum(weights.values())
        for day, weight in weights.items():
            if day in by_day:
                by_day[day]["cost_true"] += grew * weight / total
    for message in messages:
        if any(lo < message["ts"] <= hi for lo, hi in spans):
            continue
        row = by_day.get(_day_of(message["ts"]).isoformat())
        if row is not None:
            row["cost_est"] += message["cost"] * factor
    for row in rows:
        local = sum(m["cost"] for m in row["models"].values())
        row["cost"] = row["cost_true"] + row["cost_est"]
        if local > 0 and row["cost_true"] > 0:       # keep the per-model split in step with the day
            for model in row["models"].values():
                model["cost"] *= row["cost"] / local
        row["basis"] = ("mixed" if row["cost_true"] > 0 and row["cost_est"] > 0 else
                        "portal" if row["cost_true"] > 0 else
                        "estimate" if row["cost_est"] > 0 else "none")
    return rows


def coverage(daily, spent, start):
    """Share of this period's spend that the local usage rows account for (1.0 when spend is 0).

    The seat quota is shared by every Copilot client (editors, CLI, ...) while the local source only
    sees OpenCode, so a low share means local numbers under-report.
    """
    local = sum(row["cost"] for row in daily or [] if row.get("day", "") >= start.date().isoformat())
    return local / spent if spent > 0 else 1.0


def mock_daily(today=None, days=35):
    """Sample daily rows for mock mode: data/mock-history.json's pattern repeated back from today."""
    today = today or date.today()
    pattern = json.loads(MOCK_FILE.read_text())["pattern"]
    rows = []
    for ago in range(days - 1, -1, -1):
        sample = pattern[ago % len(pattern)]
        row = _blank(today - timedelta(days=ago))
        for key in ("input", "output", "cache_read", "cache_write"):
            row[key] = int(sample[key]) + (int(sample.get("reasoning", 0)) if key == "output" else 0)
        row["cache"] = row["cache_read"] + row["cache_write"]
        row["tokens"] = row["input"] + row["output"] + row["cache"]
        row["cost"], row["messages"] = float(sample["cost"]), int(sample["messages"])
        row["cost_est"], row["basis"] = row["cost"], "estimate" if row["cost"] else "none"
        for model, share in sample["models"].items():
            row["models"][model] = {"tokens": int(row["tokens"] * share),
                                    "cost": row["cost"] * share,
                                    "messages": round(row["messages"] * share)}
        row["skills"] = {k: int(v) for k, v in (sample.get("skills") or {}).items()}
        rows.append(row)
    return rows


def mock_hourly(daily_rows, now=None, hours=24):
    """Sample hourly rows for mock mode: each mock day spread over a made-up working-day curve.

    Same shape as :func:`hourly`. Every day's totals (tokens, cost, messages, per-model and per-skill)
    are shared out over its hours, so the hours of a whole day add up to the daily row; today stops
    at the current hour. Deterministic, and not real data.
    """
    now = now or datetime.now().astimezone()
    last = int(now.timestamp()) - now.minute * 60 - now.second
    by_day = {row["day"]: row for row in daily_rows}
    curve = [0, 0, 0, 0, 0, 0, 0, 1, 3, 6, 8, 7, 4, 6, 9, 8, 7, 5, 3, 2, 1, 1, 0, 0]
    rows = []
    for i in range(hours):
        start = datetime.fromtimestamp(last - (hours - 1 - i) * 3600)
        day = by_day.get(start.date().isoformat())
        row = _blank(start.date())
        row["hour"] = start.replace(minute=0, second=0, microsecond=0).isoformat()
        rows.append(row)
        if day is None:
            continue
        # open hours of that day (today only up to now); weight wiggles a little per day so days differ
        is_today = start.date() == now.date()
        open_hours = [h for h in range(24) if curve[h] and (not is_today or h <= now.hour)] or [now.hour if is_today else 12]
        weight = lambda h: curve[h] + (h * 7 + start.toordinal()) % 3 or 1
        total = sum(weight(h) for h in open_hours)
        if start.hour not in open_hours:
            continue
        share = weight(start.hour) / total
        peak = max(open_hours, key=weight)
        for key in ("input", "output", "cache_read", "cache_write", "messages"):
            row[key] = int(day[key] * share)
            if start.hour == peak:                    # the rounding left-overs go to the busiest hour
                row[key] = day[key] - sum(int(day[key] * weight(h) / total) for h in open_hours if h != peak)
        row["cache"] = row["cache_read"] + row["cache_write"]
        row["tokens"] = row["input"] + row["output"] + row["cache"]
        row["cost"] = row["cost_est"] = day["cost"] * share
        row["basis"] = "estimate" if row["cost"] else "none"
        for model, v in day["models"].items():
            row["models"][model] = {"tokens": int(v["tokens"] * share), "cost": v["cost"] * share,
                                    "messages": round(v["messages"] * share)}
        for name, count in (day.get("skills") or {}).items():
            n = count // len(open_hours) + (1 if open_hours.index(start.hour) < count % len(open_hours) else 0)
            if n:
                row["skills"][name] = n
    return rows
