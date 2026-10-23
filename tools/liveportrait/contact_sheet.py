"""
Contact sheet of the finished landing-page frames, composited exactly as the page's canvas does
(rest frame, then the moving region), on the landing page's red.

    python tools/liveportrait/contact_sheet.py FRAMES_DIR OUT.png
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def name(yaw, pitch, eyes="open"):
    return f"y{yaw:+03d}_p{pitch:+05.1f}_{eyes}"


def main(frames_dir, out):
    d = Path(frames_dir)
    m = json.load(open(d / "frames.json"))
    rest = Image.open(d / "rest.webp").convert("RGBA")
    x0, y0, x1, y1 = m["box"]
    tw = 300
    th = int(rest.height * tw / rest.width)

    def tile(n, label):
        bg = Image.new("RGBA", rest.size, (230, 59, 46, 255))
        bg.alpha_composite(rest)
        f = Image.open(d / "f" / (n + ".webp")).convert("RGBA")
        hole = Image.new("RGBA", (x1 - x0, y1 - y0), (230, 59, 46, 255))
        hole.alpha_composite(f)
        bg.paste(hole, (x0, y0))
        t = bg.convert("RGB").resize((tw, th), Image.LANCZOS)
        ImageDraw.Draw(t).rectangle((0, 0, tw, 22), fill=(0, 0, 0))
        ImageDraw.Draw(t).text((6, 5), label, fill=(255, 255, 255))
        return t

    rows = [[tile(name(y, p), f"yaw {y:+d}  pitch {p:+.1f}") for y in (-20, -10, 0, 10, 20)] for p in (-10.0, -5.0, 0.0, 5.0, 10.0)]
    rows.append([tile(name(0, 0.0, "half"), "blink: half"), tile(name(0, 0.0, "closed"), "blink: closed"),
                 tile(name(-20, 0.0, "closed"), "yaw -20 closed"), tile(name(20, 0.0, "closed"), "yaw +20 closed"),
                 tile(name(0, 0.0), "rest (LivePortrait)")])
    sheet = Image.new("RGB", (tw * 5, th * len(rows)))
    for r, row in enumerate(rows):
        for c, t in enumerate(row):
            sheet.paste(t, (c * tw, r * th))
    sheet.save(out)
    print("wrote", out, sheet.size)


if __name__ == "__main__":
    main(*sys.argv[1:3])
