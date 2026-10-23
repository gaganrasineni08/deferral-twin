"""
Small copies of the landing character's open-eye frames, kept decoded on the page all the time, so a
pose that isn't decoded at full size yet shows the right pose (slightly soft) instead of a nearby one.

    python tools/liveportrait/small_frames.py app/static/character/lp [SCALE]

Writes LP/s/*.webp (open-eye frames only) and adds "small": {"dir": "s/", "scale": SCALE} to frames.json.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image


def one(job):
    lp, scale, name = job
    im = Image.open(lp / "f" / (name + ".webp"))
    im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS).save(lp / "s" / (name + ".webp"), "WEBP", quality=82, method=4)


def main(lp, scale="0.3"):
    lp, scale = Path(lp), float(scale)
    m = json.load(open(lp / "frames.json"))
    (lp / "s").mkdir(exist_ok=True)
    names = [n for n in m["frames"] if n.endswith("_open")]
    with ProcessPoolExecutor() as ex:
        list(ex.map(one, [(lp, scale, n) for n in names]))
    n = len(names)
    m["small"] = {"dir": "s/", "scale": scale}
    json.dump(m, open(lp / "frames.json", "w"))
    print(n, "small frames")


if __name__ == "__main__":
    main(*sys.argv[1:3])
