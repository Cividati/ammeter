#!/usr/bin/env python3
"""Add the Ammeter uuid to org.gnome.shell enabled-extensions, keeping whatever else is enabled.

GNOME Shell loads extensions at session start, so a log out / log in is needed after this.
"""
import ast
import subprocess

UUID = "ammeter@cividati"
KEY = ["gsettings", "get", "org.gnome.shell", "enabled-extensions"]

current = subprocess.run(KEY, capture_output=True, text=True).stdout.strip()
try:
    enabled = ast.literal_eval(current)
    if not isinstance(enabled, list):
        enabled = []
except (ValueError, SyntaxError):
    enabled = []

if UUID not in enabled:
    enabled.append(UUID)
    value = "[" + ", ".join(f"'{item}'" for item in enabled) + "]"
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", value], check=True)

print("enabled extensions:", subprocess.run(KEY, capture_output=True, text=True).stdout.strip())
