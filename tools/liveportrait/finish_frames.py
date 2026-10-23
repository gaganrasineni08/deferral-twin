"""
Turn LivePortrait's raw head-turn frames into the landing page's frames: sharp, colour-stable,
background removed, cropped to the part that moves.

    python tools/liveportrait/finish_frames.py SOURCE_CLEAN.png GRID_DIR OUT_DIR [SHARD SHARDS]

Per frame:
  1. upscale to the output size (OUT_H tall), so a Retina display never shows a frame larger
     than its native size at the page's height;
  2. colour-match to the rest frame (mean and spread per Lab channel, over the character);
  3. move the cleaned high-res source's fine detail into the frame's pose with optical flow
     (DIS), and add it where the warp agrees with the frame, so frames stay sharp and don't flicker;
  4. remove the red background (only fully saturated red connected to the top and sides is
     background), trim and refill the edge, pull the screen's red glow out of the hair edges;
  5. crop to the union of what changes across all frames; the rest frame is also saved whole.
Writes OUT_DIR/rest.webp, OUT_DIR/f/<name>.webp and OUT_DIR/frames.json.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

OUT_H = 1600
DETAIL_SIGMA = 1.6          # what counts as "fine detail" in the source
FLOW_SCALE = 0.5            # optical flow at half resolution, then upsampled
QUALITY = 84


def key_background(rgb, s, raw=None):
    """RGBA from a frame on the reel's red screen; the alpha comes from `raw` (the frame before any colour
    work), the colours from `rgb`. s = scale relative to the 970 px tall original key.
    Background is red with little green and blue *relative to its red*: LivePortrait repaints the screen
    around the head in a red that is not quite pure, and the ear's rim (green about 31% of red) still
    fences the dark-red inside of the ear in."""
    f = (rgb if raw is None else raw).astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    cand = ((r > 90) & (g < 0.22 * r) & (b < 0.22 * r)).astype(np.uint8)
    n, lab = cv2.connectedComponents(cand)
    edge = set(np.unique(np.r_[lab[0], lab[:, 0], lab[:, -1]])) - {0}
    fg = (~np.isin(lab, list(edge))).astype(np.uint8)
    n2, lab2, st, _ = cv2.connectedComponentsWithStats(fg)
    fg = (lab2 == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    k = lambda v: np.ones((max(3, int(v * s) | 1),) * 2, np.uint8)
    fg = cv2.morphologyEx(cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k(5)), cv2.MORPH_OPEN, k(5))
    core = cv2.erode(fg, k(19))
    ring = ((cv2.dilate(fg, k(5)) > 0) & (core == 0)).astype(np.uint8)
    ring[-int(6 * s):] = 0
    col = cv2.inpaint(rgb, ring, int(6 * s), cv2.INPAINT_TELEA).astype(np.float32)
    dist = cv2.distanceTransform(fg, cv2.DIST_L2, 5)
    near = np.clip(1 - dist / (26.0 * s), 0, 1)
    excess = np.clip(col[..., 0] - (col[..., 1] * 1.75 + 12), 0, None)
    col[..., 0] -= excess * near
    a = cv2.GaussianBlur(cv2.erode(fg, k(9)).astype(np.float32), (0, 0), 1.3 * s)
    a[-int(4 * s):] = fg[-int(4 * s):]
    return np.dstack([np.clip(col, 0, 255), a * 255]).astype(np.uint8)


def color_match(img, ref, mask):
    """Match img to ref per Lab channel (mean and standard deviation) over mask."""
    a = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).astype(np.float32)
    b = cv2.cvtColor(ref, cv2.COLOR_RGB2LAB).astype(np.float32)
    m = mask > 0
    for c in range(3):
        am, asd = a[..., c][m].mean(), a[..., c][m].std() + 1e-6
        bm, bsd = b[..., c][m].mean(), b[..., c][m].std() + 1e-6
        a[..., c] = (a[..., c] - am) * (bsd / asd) + bm
    return cv2.cvtColor(np.clip(a, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)


class Detail:
    """Fine detail of the high-res source, carried into each frame's pose by optical flow."""

    def __init__(self, src):
        self.src = src.astype(np.float32)
        self.base = cv2.GaussianBlur(self.src, (0, 0), DETAIL_SIGMA)
        self.detail = self.src - self.base
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        h, w = src.shape[:2]
        self.gx, self.gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))

    def apply(self, frame):
        h, w = frame.shape[:2]
        small = lambda im: cv2.resize(cv2.cvtColor(im, cv2.COLOR_RGB2GRAY), None, fx=FLOW_SCALE, fy=FLOW_SCALE, interpolation=cv2.INTER_AREA)
        flow = self.dis.calc(small(frame), small(self.src.astype(np.uint8)), None)       # frame pixel -> where it is in the source
        flow = cv2.resize(flow, (w, h), interpolation=cv2.INTER_LINEAR) / FLOW_SCALE
        mx, my = self.gx + flow[..., 0], self.gy + flow[..., 1]
        det = cv2.remap(self.detail, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        base = cv2.remap(self.base, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        err = np.abs(base - cv2.GaussianBlur(frame.astype(np.float32), (0, 0), DETAIL_SIGMA)).mean(2)
        conf = np.exp(-(err / 10.0) ** 2)[..., None]                                    # only where the warp agrees
        return np.clip(frame.astype(np.float32) + det * conf, 0, 255).astype(np.uint8)


def main(source, grid_dir, out_dir):
    grid_dir, out = Path(grid_dir), Path(out_dir)
    (out / "f").mkdir(parents=True, exist_ok=True)
    grid = json.load(open(grid_dir / "grid.json"))
    raw = sorted((grid_dir / "raw").glob("*.png"))
    W0, H0 = grid["size"]
    H = OUT_H
    W = int(round(W0 * H / H0 / 2) * 2)
    s = H / 970.0
    up = lambda p: cv2.resize(cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB), (W, H), interpolation=cv2.INTER_CUBIC)
    rest_raw = up(grid_dir / "raw" / "y+00_p+00.0_open.png")
    src = cv2.resize(np.asarray(Image.open(source).convert("RGB")), (W, H), interpolation=cv2.INTER_AREA)
    detail = Detail(src)

    # the part of the picture that changes, over all frames (from the raw frames, cheap)
    rest_small = cv2.imread(str(grid_dir / "raw" / "y+00_p+00.0_open.png")).astype(np.int16)
    changed = np.zeros(rest_small.shape[:2], np.uint8)
    for p in raw:
        changed |= (np.abs(cv2.imread(str(p)).astype(np.int16) - rest_small).max(2) > 14).astype(np.uint8)
    changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    ys, xs = np.nonzero(changed)
    pad = 24
    x0, x1 = max(0, xs.min() - pad), min(W0, xs.max() + pad)
    y0, y1 = max(0, ys.min() - pad), min(H0, ys.max() + pad)
    k = H / H0
    box = [int(x0 * k) & ~1, int(y0 * k) & ~1, min(W, int(x1 * k + 1) | 1), min(H, int(y1 * k + 1) | 1)]

    rest = detail.apply(rest_raw)
    rest_rgba = key_background(rest, s, rest_raw)
    if len(sys.argv) <= 5 or sys.argv[4] == "0":
        Image.fromarray(rest_rgba).save(out / "rest.webp", "WEBP", quality=QUALITY, method=6)
    mask = rest_rgba[..., 3] > 128
    names = []
    shard, shards = (int(v) for v in (sys.argv[4:6] if len(sys.argv) > 5 else (0, 1)))
    for i, p in enumerate(raw, 1):
        if (i - 1) % shards != shard:
            continue
        fr_raw = up(p)
        fr = detail.apply(color_match(fr_raw, rest, mask.astype(np.uint8)))
        rgba = key_background(fr, s, fr_raw)[box[1]:box[3], box[0]:box[2]]
        Image.fromarray(rgba).save(out / "f" / (p.stem + ".webp"), "WEBP", quality=QUALITY, method=6)
        names.append(p.stem)
        if i % 50 == 0 or i == len(raw):
            print(f"{i}/{len(raw)}", flush=True)
    names = [p.stem for p in raw]
    json.dump({**{k2: grid[k2] for k2 in ("yaws", "pitches", "blink_yaws", "blink_pitches", "eyes")},
               "size": [W, H], "box": box, "frames": names}, open(out / "frames.json", "w"))
    print("size", W, H, "box", box)


if __name__ == "__main__":
    main(*sys.argv[1:4])
