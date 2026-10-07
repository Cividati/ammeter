"""A small web dashboard for the same data as the GTK app. Stdlib only.

    python3 -m token_monitor.web --host 127.0.0.1 --port 8080

Endpoints (all same-origin; there is no CORS and no authentication, so keep it on localhost or a
trusted network):

    GET  /api/data      collect() through core.visible(): {"updated", "age", "providers", "problems"}
    POST /api/refresh   drop the cache and fetch again
    GET  /api/settings  budget override state, provider and model switches
    POST /api/settings  {"budget": "400" | ""} or {"hide": {"kind": "providers"|"models", "id", "hidden"}}
    GET  /healthz       "ok"
    GET  /*             static files from token_monitor/web/

Tokens and credentials are never put in a response or in the log: the data layer only keeps numbers
and plan names, and request lines are logged without their query string.
"""
import argparse
import json
import math
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import APP_NAME, __version__, core, filters, settings

STATIC = Path(__file__).with_name("web")
CACHE_SECONDS = 45          # how long one collect() answers every browser tab
MAX_BODY = 4096
BUDGET_KEY = "TOKEN_MONITOR_BUDGET"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".ico": "image/x-icon", ".json": "application/json", ".webmanifest": "application/manifest+json"}
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                               "script-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_lock = threading.Lock()
_cache = {"raw": None, "at": 0.0, "stamp": None}


def snapshot(force=False):
    """(raw collect() result, fetched-at epoch, ISO time). One fetch at a time, shared by all callers."""
    with _lock:
        if force or _cache["raw"] is None or time.time() - _cache["at"] > CACHE_SECONDS:
            _cache["raw"] = core.collect()
            _cache["at"] = time.time()
            _cache["stamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return _cache["raw"], _cache["at"], _cache["stamp"]


def data_payload(force=False):
    raw, at, stamp = snapshot(force)
    shown = core.visible(raw)
    return {"version": __version__, "app": APP_NAME, "updated": stamp, "age": round(time.time() - at),
            "providers": shown, "problems": [{"name": n, "reason": r} for n, r in core.problems(shown)]}


def build_groups(raw, hidden, seen, models):
    """Settings groups: one per provider (OpenCode providerID, or a dashboard provider with no model data)
    with its models; models of unknown provider go in a last, switch-less "Other models" group."""
    owner = {}
    for e in raw:
        owner.update(e.get("model_providers") or {})
    cost_of = lambda m: seen.get(m, {}).get("cost", 0)
    items = {}
    for m in models:
        items.setdefault(owner.get(m), []).append({"name": m, "hidden": m in hidden["models"], "cost": seen.get(m, {}).get("cost", 0.0),
                                                   "messages": seen.get(m, {}).get("messages", 0)})
    groups = [{"id": pid, "name": pid, "kind": "providers", "hidden": pid in hidden["providers"], "models": ms}
              for pid, ms in items.items() if pid]
    groups.sort(key=lambda g: (-sum(cost_of(m["name"]) for m in g["models"]), g["name"]))
    for e in raw:      # a dashboard provider that is hidden (or has no model data) stays listed so it can be switched back on
        if (core.configured(e) or e["key"] in hidden["providers"]) and (e["key"] in hidden["providers"] or not groups):
            groups.append({"id": e["key"], "name": e["name"], "kind": "providers", "hidden": e["key"] in hidden["providers"], "models": []})
    if items.get(None):
        groups.append({"id": None, "name": "Other models", "kind": None, "hidden": False, "models": items[None]})
    return groups


def settings_payload():
    raw, _at, _stamp = snapshot()
    hidden = filters.load()
    entry = next((e for e in raw if e.get("summary")), None)
    summary = (entry or {}).get("summary") or {}
    origin = settings.setting_origin(BUDGET_KEY)
    seen = core.models_seen(raw)
    models = sorted(set(seen) | hidden["models"], key=lambda m: (-seen.get(m, {}).get("cost", 0), m))
    groups = build_groups(raw, hidden, seen, models)
    return {
        "groups": groups,
        "budget": {"override": settings.config_file().get(BUDGET_KEY, ""), "origin": origin,
                   "readonly": origin in ("environment", ".env"),
                   "source": summary.get("source_budget"), "effective": summary.get("budget"),
                   "currency": summary.get("currency"), "available": bool(entry)},
        "providers": [{"key": e["key"], "name": e["name"], "hidden": e["key"] in hidden["providers"]}
                      for e in raw if core.configured(e) or e["key"] in hidden["providers"]],
        "models": [{"name": m, "hidden": m in hidden["models"], "cost": seen.get(m, {}).get("cost", 0.0),
                    "messages": seen.get(m, {}).get("messages", 0)} for m in models],
    }


class Problem(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def apply_settings(body):
    """Validate and apply one settings change; raises Problem for anything invalid."""
    if not isinstance(body, dict) or not body:
        raise Problem(400, "Send a JSON object.")
    if "budget" in body:
        origin = settings.setting_origin(BUDGET_KEY)
        if origin in ("environment", ".env"):
            raise Problem(409, f"The budget is set in {'the environment' if origin == 'environment' else 'the .env file'}"
                               f" ({BUDGET_KEY}); change it there.")
        text = str(body["budget"] if body["budget"] is not None else "").strip().replace(",", ".")
        if text:
            try:
                number = float(text)
            except ValueError:
                raise Problem(400, "Enter a number greater than 0.") from None
            if not math.isfinite(number) or number <= 0 or number > 1e9:
                raise Problem(400, "Enter a number greater than 0.")
            text = f"{number:g}"
        settings.set_config(BUDGET_KEY, text)
        with _lock:
            _cache["raw"] = None                      # the next read fetches with the new limit
    elif "hide" in body:
        change = body["hide"]
        if not isinstance(change, dict) or change.get("kind") not in filters.KINDS \
                or not isinstance(change.get("id"), str) or not 0 < len(change["id"]) <= 200 \
                or not isinstance(change.get("hidden"), bool):
            raise Problem(400, "Expected {kind: providers|models, id: text, hidden: true|false}.")
        filters.set_hidden(change["kind"], change["id"], change["hidden"])
    else:
        raise Problem(400, "Nothing to change: send 'budget' or 'hide'.")
    return settings_payload()


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenMonitor"
    sys_version = ""

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):          # no query strings, no headers, nothing secret
        print(f"{self.log_date_time_string()} {self.command} {urlsplit(self.path).path} -> {args[1] if len(args) > 1 else ''}",
              flush=True)

    def send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, payload, status=200):
        self.send_bytes(status, json.dumps(payload).encode(), "application/json")

    def fail(self, status, message):
        self.send_json({"error": message}, status)

    def guarded(self, work):
        try:
            work()
        except Problem as problem:
            self.fail(problem.status, problem.message)
        except Exception:                        # never leak a traceback or an inner message
            self.fail(500, "Something went wrong reading the data.")

    # ------------------------------------------------------------ routes
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self.send_bytes(200, b"ok", "text/plain; charset=utf-8")
        if path == "/api/data":
            return self.guarded(lambda: self.send_json(data_payload()))
        if path == "/api/settings":
            return self.guarded(lambda: self.send_json(settings_payload()))
        if path.startswith("/api/"):
            return self.fail(404, "No such endpoint.")
        self.serve_static(path)

    do_HEAD = do_GET

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/api/refresh", "/api/settings"):
            return self.fail(404, "No such endpoint.")
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.fail(403, "Cross-origin requests are not allowed.")
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self.fail(415, "Content-Type must be application/json.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.fail(400, "Bad Content-Length.")
        if length > MAX_BODY:
            return self.fail(413, "Request body too large.")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.fail(400, "Body is not valid JSON.")
        if path == "/api/refresh":
            return self.guarded(lambda: self.send_json(data_payload(force=True)))
        self.guarded(lambda: self.send_json(apply_settings(body)))

    def serve_static(self, path):
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        try:
            target = (STATIC / name).resolve()
            target.relative_to(STATIC.resolve())          # raises if it escapes the folder
        except (ValueError, OSError):
            return self.fail(404, "Not found.")
        if not target.is_file() or target.suffix not in TYPES:
            return self.fail(404, "Not found.")
        self.send_bytes(200, target.read_bytes(), TYPES[target.suffix])


def make_server(host="127.0.0.1", port=8080):
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m token_monitor.web", description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    server = make_server(args.host, args.port)
    print(f"{APP_NAME} web dashboard on http://{args.host}:{server.server_address[1]} (no authentication)",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
