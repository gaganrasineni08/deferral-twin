"""Draw the app icon (two overlapping rings, the second dashed) as an .iconset, with the
standard library only, because the bundled Python has no imaging library.
  python3 packaging/make_icon.py OUT.iconset  &&  iconutil -c icns OUT.iconset"""
import math
import struct
import sys
import zlib
from pathlib import Path


def png(w, h, px):
    raw = b"".join(b"\x00" + bytes(px[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def draw(n):
    px = bytearray(n * n * 4)
    bg, blue, purple = (244, 241, 234), (44, 70, 196), (110, 55, 200)
    r_bg, rr, sw = n * 0.225, n * 0.215, n * 0.062
    c1, c2, cy = n * 0.4, n * 0.6, n * 0.5
    for y in range(n):
        for x in range(n):
            fx, fy = x + 0.5, y + 0.5
            # rounded square background
            dx, dy = max(abs(fx - n / 2) - (n / 2 - r_bg) + n * 0.04, 0), max(abs(fy - n / 2) - (n / 2 - r_bg) + n * 0.04, 0)
            if math.hypot(dx, dy) > r_bg:
                continue
            col = bg
            d2 = math.hypot(fx - c2, fy - cy)
            ang = math.atan2(fy - cy, fx - c2)
            if abs(d2 - rr) < sw / 2 and (ang * 9 / math.pi) % 2 < 1.25:
                col = purple
            if abs(math.hypot(fx - c1, fy - cy) - rr) < sw / 2:
                col = blue
            i = (y * n + x) * 4
            px[i:i + 4] = bytes((*col, 255))
    return px


out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for base in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        n = base * scale
        name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
        (out / name).write_bytes(png(n, n, draw(n)))
print("icon written to", out)
