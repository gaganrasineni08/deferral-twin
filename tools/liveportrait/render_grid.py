"""
Render the landing-page character's head-turn grid with LivePortrait, driven numerically
(no gradio): the same maths as the Retargeting Image tab -- a rotation matrix from pitch, yaw
and roll applied to the source keypoints, optional eye retargeting, then the stitching step,
warp/decode and paste-back.

    tools/liveportrait/.venv/bin/python tools/liveportrait/render_grid.py SOURCE.png OUT_DIR

Writes OUT_DIR/raw/y{yaw:+03d}_p{pitch:+05.1f}_{eyes}.png (full frames, LivePortrait's resolution)
and OUT_DIR/grid.json. Every pose, the rest pose (0, 0) included, goes through LivePortrait, so
there is no jump when he starts moving.
"""
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

ARGS = [str(Path(a).resolve()) for a in sys.argv[1:]]     # before the chdir below
LP = Path(__file__).resolve().parent / "vendor" / "LivePortrait"
sys.path.insert(0, str(LP))
os.chdir(LP)                                     # LivePortrait resolves its weights relative to the repo

from src.config.argument_config import ArgumentConfig  # noqa: E402
from src.config.crop_config import CropConfig  # noqa: E402
from src.config.inference_config import InferenceConfig  # noqa: E402
from src.live_portrait_wrapper import LivePortraitWrapper  # noqa: E402
from src.utils.camera import get_rotation_matrix  # noqa: E402
from src.utils.crop import paste_back, prepare_paste_back  # noqa: E402
from src.utils.cropper import Cropper  # noqa: E402
from src.utils.io import load_img_online  # noqa: E402
from src.utils.retargeting_utils import calc_eye_close_ratio  # noqa: E402

YAWS = list(range(-20, 21))                            # 1 degree steps
PITCHES = [-10.0, -7.5, -5.0, -2.5, 0.0, 2.5, 5.0, 7.5, 10.0]
ROLL_PER_YAW = 0.12
CROP_SCALE = 2.5                                       # the Retargeting Image tab's default
GAZE_PER_YAW, GAZE_PER_PITCH = 0.5, -1.0               # baked gaze; the page paints over the irises and draws its own (eye layer)
BLINK_YAWS = list(range(-20, 21, 5))                   # coarse grid for blinking
BLINK_PITCHES = [-10.0, -5.0, 0.0, 5.0, 10.0]
EYES = {"half": 0.5, "closed": 0.0}                    # fraction of the source eye-open ratio


def partial_fields(target_class, kwargs):
    return target_class(**{k: v for k, v in kwargs.items() if hasattr(target_class, k)})


def gaze(delta, gx, gy):
    """The Retargeting tab's eyeball-direction edit (GradioPipeline.update_delta_new_eyeball_direction)."""
    if gx > 0:
        delta[0, 11, 0] += gx * 0.0007
        delta[0, 15, 0] += gx * 0.001
    else:
        delta[0, 11, 0] += gx * 0.001
        delta[0, 15, 0] += gx * 0.0007
    delta[0, 11, 1] += gy * -0.001
    delta[0, 15, 1] += gy * -0.001
    blink = -gy / 2.0
    delta[0, 11, 1] += blink * -0.001
    delta[0, 13, 1] += blink * 0.0003
    delta[0, 15, 1] += blink * -0.001
    delta[0, 16, 1] += blink * 0.0003
    return delta


class Renderer:
    def __init__(self, source):
        args = ArgumentConfig()
        inf = partial_fields(InferenceConfig, args.__dict__)
        crop = partial_fields(CropConfig, args.__dict__)
        crop.scale = CROP_SCALE
        self.w = LivePortraitWrapper(inference_cfg=inf)
        self.cropper = Cropper(crop_cfg=crop)
        self.img = load_img_online(str(source), mode="rgb", max_dim=1280, n=2)
        ci = self.cropper.crop_source_image(self.img, crop)
        if ci is None:
            raise SystemExit("LivePortrait found no face in the source")
        self.lmk, self.M = ci["lmk_crop"], ci["M_c2o"]
        self.mask = prepare_paste_back(inf.mask_crop, self.M, dsize=(self.img.shape[1], self.img.shape[0]))
        I_s = self.w.prepare_source(ci["img_crop_256x256"])
        self.info = self.w.get_kp_info(I_s)
        self.f_s = self.w.extract_feature_3d(I_s)
        self.x_s = self.w.transform_keypoint(self.info)
        self.eye0 = float(calc_eye_close_ratio(self.lmk[None]).mean())

    @torch.no_grad()
    def render(self, yaw, pitch, eyes=None):
        dev = self.w.device
        i = self.info
        R = get_rotation_matrix(i["pitch"] + pitch, i["yaw"] + yaw, i["roll"] + ROLL_PER_YAW * yaw).to(dev)
        delta = i["exp"].clone().to(dev)
        if GAZE_PER_YAW or GAZE_PER_PITCH:
            delta = gaze(delta, GAZE_PER_YAW * yaw, GAZE_PER_PITCH * pitch)
        x_d = i["scale"].to(dev) * (i["kp"].to(dev) @ R + delta) + i["t"].to(dev)
        if eyes is not None:
            ratio = self.w.calc_combined_eye_ratio([[self.eye0 * EYES[eyes]]], self.lmk)
            x_d = x_d + self.w.retarget_eye(self.x_s, ratio)
        x_d = self.w.stitching(self.x_s, x_d)
        out = self.w.parse_output(self.w.warp_decode(self.f_s, self.x_s, x_d)["out"])[0]
        return paste_back(out, self.M, self.img, self.mask)


def name(yaw, pitch, eyes="open"):
    return f"y{yaw:+03d}_p{pitch:+05.1f}_{eyes}.png"


def main(source, out_dir):
    out = Path(out_dir).resolve()
    (out / "raw").mkdir(parents=True, exist_ok=True)
    r = Renderer(Path(source).resolve())
    jobs = [(y, p, None) for p in PITCHES for y in YAWS]
    jobs += [(y, p, e) for e in EYES for p in BLINK_PITCHES for y in BLINK_YAWS]
    t0 = time.time()
    for k, (y, p, e) in enumerate(jobs, 1):
        f = out / "raw" / name(y, p, e or "open")
        if not f.exists():
            cv2.imwrite(str(f), cv2.cvtColor(r.render(y, p, e), cv2.COLOR_RGB2BGR))
        if k % 25 == 0 or k == len(jobs):
            print(f"{k}/{len(jobs)}  {time.time() - t0:.0f}s", flush=True)
    json.dump({"yaws": YAWS, "pitches": PITCHES, "roll_per_yaw": ROLL_PER_YAW, "blink_yaws": BLINK_YAWS,
               "blink_pitches": BLINK_PITCHES, "eyes": list(EYES), "size": [int(r.img.shape[1]), int(r.img.shape[0])],
               "crop_scale": CROP_SCALE, "gaze_per_yaw": GAZE_PER_YAW, "gaze_per_pitch": GAZE_PER_PITCH},
              open(out / "grid.json", "w"), indent=1)


if __name__ == "__main__":
    main(*ARGS[:2])
