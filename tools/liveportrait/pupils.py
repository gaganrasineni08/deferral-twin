"""
Pupil centres for every rendered frame, from LivePortrait's own 203-point face landmarks
(points 197 and 198 are the pupils). Run in the LivePortrait env on the raw grid:

    tools/liveportrait/.venv/bin/python tools/liveportrait/pupils.py GRID_DIR OUT.json SCALE

SCALE converts raw-frame pixels to the finished frames' pixels (finished height / raw height).
Writes {frame name: [[x, y], [x, y]]}, left eye first; closed-eye frames are skipped.
"""
import json
import os
import sys
from pathlib import Path

import cv2

ARGS = [str(Path(a).resolve()) for a in sys.argv[1:3]] + sys.argv[3:4]     # before the chdir below
LP = Path(__file__).resolve().parent / "vendor" / "LivePortrait"
sys.path.insert(0, str(LP))
os.chdir(LP)

from src.config.argument_config import ArgumentConfig  # noqa: E402
from src.config.crop_config import CropConfig  # noqa: E402
from src.utils.cropper import Cropper  # noqa: E402


def main(grid_dir, out, scale):
    scale = float(scale)
    args = ArgumentConfig()
    crop = CropConfig(**{k: v for k, v in args.__dict__.items() if hasattr(CropConfig, k)})
    crop.scale = 2.5
    cropper = Cropper(crop_cfg=crop)
    res, missing = {}, []
    files = sorted((Path(grid_dir) / "raw").glob("*.png"))
    for k, f in enumerate(files, 1):
        if f.stem.endswith("_closed"):
            continue
        rgb = cv2.imread(str(f))[..., ::-1].copy()
        ci = cropper.crop_source_image(rgb, crop)
        if ci is None:
            missing.append(f.stem)
            continue
        lmk = ci["lmk_crop"]                                   # already in the frame's own pixels
        a, b = lmk[197], lmk[198]
        left, right = (a, b) if a[0] < b[0] else (b, a)
        res[f.stem] = [[round(float(left[0]) * scale, 1), round(float(left[1]) * scale, 1)],
                       [round(float(right[0]) * scale, 1), round(float(right[1]) * scale, 1)]]
        if k % 100 == 0:
            print(f"{k}/{len(files)}", flush=True)
    json.dump(res, open(out, "w"))
    print("pupils for", len(res), "frames; no face found in", len(missing), missing[:5])


if __name__ == "__main__":
    main(*ARGS[:3])
