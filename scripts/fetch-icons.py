#!/usr/bin/python3
"""Regenerate the provider marks used by the app from simple-icons (CC0-1.0).

The upstream files are 24x24 monochrome marks; this insets and scales them so they sit
comfortably inside a 26px badge circle, and forces a white fill because GTK recolours symbolic
icons itself (the CSS colour of the image wins).

    /usr/bin/python3 scripts/fetch-icons.py
"""
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "icons"
SLUG = {"claude": "claude", "codex": "openai", "openrouter": "openrouter", "deepseek": "deepseek"}
INNER = '<g transform="translate(2.2 2.2) scale(0.82)">%s</g>'
TEMPLATE = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24">\n'
            '  <!-- %s mark, from simple-icons (CC0-1.0): %s -->\n'
            '  %s\n'
            '</svg>\n')

for key, slug in SLUG.items():
    url = f"https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/{slug}.svg"
    src = urllib.request.urlopen(url, timeout=30).read().decode()
    paths = re.findall(r'<path[^>]*\sd="([^"]+)"', src)
    if not paths:
        raise SystemExit(f"no path data in {url}")
    inner = "".join(f'<path fill="#ffffff" d="{p}"/>' for p in paths)
    out = OUT / f"ai-usage-{key}-symbolic.svg"
    out.write_text(TEMPLATE % (slug, url, INNER % inner))
    print(f"{out.name:36} {len(inner):5} bytes of path data  <- {url}")
