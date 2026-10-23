"""
The landing character's eye layer. For every finished frame: find both eye openings and iris
centres, store them in JSON, and make an "empty eyes" version (the white of the eye painted over
the iris). Also cut one iris sprite (iris + pupil + highlight) from the rest frame. The page then
draws the iris itself, clipped to each opening and offset toward the cursor.

    python tools/liveportrait/eye_layer.py FRAMES_DIR OUT_DIR PUPILS_JSON   (pupils.json from pupils.py)

Reads FRAMES_DIR/frames.json, rest.webp and f/*.webp (as written by finish_frames.py).
Writes OUT_DIR/f/*.webp (empty eyes), OUT_DIR/rest.webp, OUT_DIR/eye-iris.png and OUT_DIR/frames.json
(the original manifest plus "eyes": {frame: [eye, eye] or []} and "iris": sprite info).
An eye is {"cx", "cy", "r": [32 radii, starting at angle 0 (right), counter-clockwise in screen y],
"ix", "iy", "ir"}; coordinates are in the full canvas.
"""
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

N = 32                                    # outline radii per eye
QUALITY = 84


def find_eyes(rgba, pupils):
    """[(opening mask, iris (x, y, r), white mask)] per pupil (LivePortrait landmarks 197/198, in this
    image's pixels). The iris edge is where rays cast from the pupil first meet the white of the eye; the
    opening is the white connected to the iris rim, within a few iris radii of the pupil, plus the iris.
    The white is bluish-neutral (blue at least red - 8), unlike the pink highlight on the cheek."""
    rgb = rgba[..., :3]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    r, b = rgb[..., 0].astype(int), rgb[..., 2].astype(int)
    white_all = ((hsv[..., 2] > 150) & (hsv[..., 1] < 70) & (b >= r - 8) & (rgba[..., 3] > 250)).astype(np.uint8)
    white_all = cv2.morphologyEx(white_all, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    H, W = white_all.shape
    eyes = []
    for px, py in pupils:
        # the landmark is near the pupil, not always on it: use the darkest blob within 60 px instead
        x0, y0 = int(max(0, px - 60)), int(max(0, py - 60))
        win = hsv[y0:int(py + 60), x0:int(px + 60), 2]
        dark = (win < max(60, np.percentile(win, 8))).astype(np.uint8)
        n, lab, st, cen = cv2.connectedComponentsWithStats(dark)
        if n > 1:
            j = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
            if st[j, cv2.CC_STAT_AREA] > 40:
                px, py = x0 + cen[j][0], y0 + cen[j][1]
        hits = []
        for k in range(24):
            a = 2 * np.pi * k / 24
            run = 0
            for d in range(6, 130):                           # the first run of 10+ white pixels: the eye
                x, y = int(round(px + d * np.cos(a))), int(round(py + d * np.sin(a)))   # white, not the catchlight
                if not (0 <= x < W and 0 <= y < H):
                    break
                run = run + 1 if white_all[y, x] else 0
                if run >= 10:
                    hits.append(d - 9)
                    break
        if len(hits) < 5:
            eyes.append((None, None, None))                    # no white around the pupil: eye closed here
            continue
        ir = float(np.clip(np.median(hits), 12, 90))
        R = 2.7 * ir
        disc_R = np.zeros((H, W), np.uint8); cv2.circle(disc_R, (int(px), int(py)), int(R), 1, -1)
        n, lab = cv2.connectedComponents(white_all & disc_R)
        rim = np.zeros((H, W), np.uint8); cv2.circle(rim, (int(px), int(py)), int(ir + 6), 1, 5)
        keep = set(np.unique(lab[(rim > 0) & (lab > 0)])) - {0}
        white = np.isin(lab, list(keep)).astype(np.uint8)
        iris_disc = np.zeros((H, W), np.uint8); cv2.circle(iris_disc, (int(px), int(py)), int(ir * 1.15), 1, -1)
        union = ((white > 0) | (iris_disc > 0)).astype(np.uint8)
        cnts, _ = cv2.findContours(union, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        hull = cv2.convexHull(np.vstack(cnts))
        opening = np.zeros((H, W), np.uint8); cv2.fillConvexPoly(opening, hull, 1)
        opening &= disc_R
        eyes.append((opening, (float(px), float(py), ir), white))
    return eyes


def radii(opening):
    """Centre and N radii of a (convex) opening, for blending outlines between frames."""
    m = cv2.moments(opening, binaryImage=True)
    if m["m00"] < 1:
        return None
    cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
    cnts, _ = cv2.findContours(opening, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    pts = max(cnts, key=cv2.contourArea)[:, 0, :].astype(np.float32)
    ang = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
    dist = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    out = []
    for k in range(N):
        a = 2 * np.pi * k / N - np.pi
        d = np.abs((ang - a + np.pi) % (2 * np.pi) - np.pi)
        out.append(float(dist[np.argsort(d)[:3]].mean()))
    out = out[N // 2:] + out[:N // 2]                       # start at angle 0 (pointing right)
    return cx, cy, out


def empty_eyes(rgba, eyes):
    """Paint the white of the eye over the whole iris disc (highlights included): a smooth, shaded white
    made only from the eye's own white pixels (normalised Gaussian), the eye's median white where they
    are too few, so no streaks or skin colour."""
    out = rgba.copy()
    for opening, iris, white in eyes:
        if iris is None or opening is None:
            continue
        disc = np.zeros(opening.shape, np.uint8)
        cv2.circle(disc, (int(round(iris[0])), int(round(iris[1]))), int(round(iris[2] * 1.3)) + 4, 1, -1)
        hole = (disc > 0) & (cv2.dilate(opening, np.ones((9, 9), np.uint8)) > 0)    # the iris may run a little under the lid
        rgb = out[..., :3].astype(np.float32)
        wm = ((white > 0) & ~hole).astype(np.float32)
        if wm.sum() < 20:
            continue
        med = np.median(rgb[wm > 0], axis=0)
        sig = max(6.0, iris[2] * 1.2)
        num = cv2.GaussianBlur(rgb * wm[..., None], (0, 0), sig)
        den = cv2.GaussianBlur(wm, (0, 0), sig)[..., None]
        fill = np.where(den > 0.08, num / np.maximum(den, 1e-3), med)
        fill = 0.85 * fill + 0.15 * med
        k = cv2.GaussianBlur(hole.astype(np.float32), (0, 0), 2.0)[..., None]
        out[..., :3] = np.clip(rgb * (1 - k) + fill * k, 0, 255).astype(np.uint8)
    return out


def main(frames_dir, out_dir, pupils_json):
    src, out = Path(frames_dir), Path(out_dir)
    pupils = json.load(open(pupils_json))
    (out / "f").mkdir(parents=True, exist_ok=True)
    m = json.load(open(src / "frames.json"))
    bx0, by0, bx1, by1 = m["box"]
    W, H = m["size"]
    eyes_json, problems = {}, []
    for k, name in enumerate(m["frames"], 1):
        rgba = np.asarray(Image.open(src / "f" / (name + ".webp")).convert("RGBA")).copy()
        closed = name.endswith("_closed") or name not in pupils
        rec = []
        if not closed:
            eyes = find_eyes(rgba, [(x - bx0, y - by0) for x, y in pupils[name]])
            if sum(e[0] is not None for e in eyes) != 2:
                problems.append(f"{name}: {sum(e[0] is not None for e in eyes)} eyes")
            for opening, iris, _ in eyes:
                if opening is None:
                    continue
                rr = radii(opening)
                if rr is None:
                    continue
                cx, cy, rad = rr
                e = {"cx": round(cx + bx0, 1), "cy": round(cy + by0, 1), "r": [round(v, 1) for v in rad]}
                if iris:
                    e.update(ix=round(iris[0] + bx0, 1), iy=round(iris[1] + by0, 1), ir=round(iris[2], 1))
                rec.append(e)
            rgba = empty_eyes(rgba, eyes)
        eyes_json[name] = rec
        Image.fromarray(rgba).save(out / "f" / (name + ".webp"), "WEBP", quality=QUALITY, method=6)
        if k % 50 == 0 or k == len(m["frames"]):
            print(f"{k}/{len(m['frames'])}", flush=True)

    # the iris sprite, cut from the rest frame (before its iris is painted over)
    rest_name = "y+00_p+00.0_open"
    rest_crop = np.asarray(Image.open(src / "f" / (rest_name + ".webp")).convert("RGBA"))
    eyes = find_eyes(rest_crop, [(x - bx0, y - by0) for x, y in pupils[rest_name]])
    iris = max((e[1] for e in eyes if e[1]), key=lambda t: t[2])
    ix, iy, ir = iris
    R = int(round(ir * 1.3))
    patch = rest_crop[int(iy) - R:int(iy) + R, int(ix) - R:int(ix) + R].copy()
    yy, xx = np.mgrid[0:2 * R, 0:2 * R].astype(np.float32)
    d = np.hypot(xx - R, yy - R)
    patch[..., 3] = (np.clip((ir * 1.08 - d) / 2.0, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(patch).save(out / "eye-iris.png", optimize=True)
    shutil.copy2(src / "rest.webp", out / "rest.webp")
    m.update(eyes=eyes_json, iris={"file": "eye-iris.png", "r": round(ir, 1), "size": 2 * R})
    json.dump(m, open(out / "frames.json", "w"))
    print("iris radius", round(ir, 1), "| frames with problems:", len(problems), problems[:8])


if __name__ == "__main__":
    main(*sys.argv[1:4])
