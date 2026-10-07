"""Settings lookup: real environment, then ``.env`` in the repo root, then the user config file.

Stdlib only and import-time side effect on purpose: ``load_dotenv()`` runs once when the package's
data layer is first imported, so the CLI, the GTK app and the tests all see the same settings.
``.env`` never overrides a variable that is already set. Secrets are never logged.
"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path.home() / ".config" / "token-monitor"


def parse_env(text):
    """KEY=VALUE lines -> dict. Supports blanks, # comments, 'export ', quotes and trailing ' # c'."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if key.strip():
            values[key.strip()] = value
    return values


def load_dotenv(path=None):
    """Load ``.env`` from the repo root into os.environ without overriding anything already set."""
    try:
        text = (path or REPO / ".env").read_text()
    except OSError:
        return
    for key, value in parse_env(text).items():
        os.environ.setdefault(key, value)


def config_file():
    """~/.config/token-monitor/config; absent file means no settings."""
    try:
        return parse_env((CONFIG_DIR / "config").read_text())
    except OSError:
        return {}


def set_config(name, value):
    """Write NAME=value into the user config file (an empty value removes the key).

    Other lines and comments are kept; the file is replaced atomically.
    """
    path = CONFIG_DIR / "config"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    kept, done = [], False
    for line in lines:
        text = line.strip()
        text = text[7:].lstrip() if text.startswith("export ") else text
        if not text.startswith("#") and text.partition("=")[0].strip() == name:
            if value and not done:
                kept.append(f"{name}={value}")
            done = True
            continue
        kept.append(line)
    if value and not done:
        kept.append(f"{name}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("\n".join(kept) + "\n" if kept else "")
    os.replace(temp, path)


def setting_origin(name):
    """Where a setting comes from: 'environment', '.env', 'config' or None."""
    if os.environ.get(name):
        try:
            dotenv = parse_env((REPO / ".env").read_text())
        except OSError:
            dotenv = {}
        return ".env" if dotenv.get(name) == os.environ[name] else "environment"
    return "config" if config_file().get(name) else None


def setting(name, default=None):
    return os.environ.get(name) or config_file().get(name) or default


load_dotenv()
