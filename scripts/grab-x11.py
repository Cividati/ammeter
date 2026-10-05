#!/usr/bin/env python3
"""Grab the whole X screen to PNG (stdlib zlib/struct + python-xlib). For verifying the widget headless."""
import struct, sys, zlib
from Xlib import display, X

out = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
d = display.Display()
root = d.screen().root
g = root.get_geometry()
w, h = g.width, g.height
src = root.get_image(0, 0, w, h, X.ZPixmap, 0xFFFFFFFF).data
# Xvfb/TrueColor 24-bit -> 4 bytes per pixel, BGRX (4th byte unused/zero)
rgba = b"".join(bytes((src[i + 2], src[i + 1], src[i], 255)) for i in range(0, len(src), 4))
rows = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))


def chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


png = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(rows, 9))
       + chunk(b"IEND", b""))
open(out, "wb").write(png)
print(f"{out}: {w}x{h}")
