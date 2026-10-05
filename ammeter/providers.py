"""One fetcher per provider. Each returns the normalised provider dict documented in core.py.

Two credential styles are supported because the providers differ:

* Claude Code and Codex keep an OAuth login on disk and expose the same usage endpoints their own
  CLIs read (``~/.claude/.credentials.json``, ``~/.codex/auth.json``).
* OpenRouter and DeepSeek are pay-as-you-go: they hand back a credit balance for an API key, which
  Hermes already stores in ``~/.hermes/.env``.
"""
import json
import re
import urllib.request
from pathlib import Path

from .formatting import balance_row, quota_row, to_iso

HOME = Path.home()
TIMEOUT = 20


def _get(url, headers, timeout=TIMEOUT):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _env_key(name):
    """Read KEY=... from ~/.hermes/.env, the file Hermes itself keeps provider keys in."""
    env = HOME / ".hermes" / ".env"
    if not env.exists():
        return None
    found = re.search(r'(?im)^\s*(?:export\s+)?' + name + r'\s*=\s*["\']?([^\s"\']+)',
                      env.read_text(errors="ignore"))
    return found.group(1) if found else None


def claude():
    """5-hour and weekly quota from the OAuth login Claude Code already has."""
    credentials = json.loads((HOME / ".claude" / ".credentials.json").read_text())
    oauth = credentials.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        raise RuntimeError("no oauth token in ~/.claude/.credentials.json")
    usage = _get("https://api.anthropic.com/api/oauth/usage",
                 {"Authorization": f"Bearer {token}",
                  "anthropic-beta": "oauth-2025-04-20",
                  "User-Agent": "claude-code/2.0",
                  "Accept": "application/json"})
    rows = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d"),
                       ("seven_day_opus", "opus 7d"), ("seven_day_sonnet", "sonnet 7d")):
        window = usage.get(key) or {}
        if window.get("utilization") is not None:
            rows.append(quota_row(label, window["utilization"], window.get("resets_at")))
    return {"name": "CLAUDE", "sub": oauth.get("subscriptionType") or "", "rows": rows}


def codex():
    """Session and weekly rate limits from the Codex CLI login. Not a public OpenAI API."""
    auth = json.loads((HOME / ".codex" / "auth.json").read_text())
    tokens = auth.get("tokens") or {}
    token, account = tokens.get("access_token"), tokens.get("account_id")
    if not (token and account):
        raise RuntimeError("no tokens in ~/.codex/auth.json")
    usage = _get("https://chatgpt.com/backend-api/wham/usage",
                 {"Authorization": f"Bearer {token}", "chatgpt-account-id": account,
                  "User-Agent": "codex-cli/0.1", "Accept": "application/json"})
    limits = usage.get("rate_limit") or {}
    rows = []
    for window, label in (("primary_window", "session"), ("secondary_window", "weekly")):
        entry = limits.get(window) or {}
        if entry.get("used_percent") is not None:
            # the reset date and countdown already say how far out the window is
            rows.append(quota_row(label, entry["used_percent"], to_iso(entry.get("reset_at"))))
    balance = (usage.get("credits") or {}).get("balance")
    if balance is not None:
        rows.append(balance_row("balance", f"${balance}"))
    return {"name": "CODEX", "sub": usage.get("plan_type") or "", "rows": rows,
            "reached": bool(limits.get("limit_reached"))}


def openrouter():
    """Prepaid credit: a balance to spend, not a quota to fill."""
    key = _env_key("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("no OPENROUTER_API_KEY in ~/.hermes/.env")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    credits = _get("https://openrouter.ai/api/v1/credits", headers)["data"]
    detail = _get("https://openrouter.ai/api/v1/key", headers)["data"]
    purchased = float(credits.get("total_credits") or 0)
    used = float(credits.get("total_usage") or 0)
    left = max(purchased - used, 0.0)
    rows = [balance_row("balance", f"${left:.2f} left of ${purchased:.2f}" if purchased
                        else "no credit purchased")]
    if detail.get("usage_daily") is not None:
        rows.append(balance_row("spend", f"${float(detail.get('usage_daily') or 0):.2f} today   "
                                        f"${float(detail.get('usage_monthly') or 0):.2f} month"))
    return {"name": "OPENROUTER", "sub": "", "rows": rows}


def deepseek():
    """Prepaid credit, same shape as OpenRouter."""
    key = _env_key("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("no DEEPSEEK_API_KEY in ~/.hermes/.env")
    data = _get("https://api.deepseek.com/user/balance",
                {"Authorization": f"Bearer {key}", "Accept": "application/json"})
    rows = []
    for entry in data.get("balance_infos") or []:
        note = f"{entry.get('currency') or ''} {entry.get('total_balance')}".strip()
        if float(entry.get("granted_balance") or 0):
            note += f"  (granted {entry['granted_balance']})"
        rows.append(balance_row("balance", note))
    if not rows:
        rows.append(balance_row("balance", "no balance reported"))
    return {"name": "DEEPSEEK", "sub": "available" if data.get("is_available") else "unavailable",
            "rows": rows}


# order matters: it is the order of the app groups, the menu blocks and the CLI output
PROVIDERS = (
    ("claude", claude),
    ("codex", codex),
    ("openrouter", openrouter),
    ("deepseek", deepseek),
)
