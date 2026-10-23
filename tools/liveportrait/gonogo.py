"""
Go/no-go for the LivePortrait head turn: the Retargeting Image tab's own pipeline call
(GradioPipeline.execute_image_retargeting), swept over the tab's slider ranges, saved as
before/after contact sheets.

    tools/liveportrait/.venv/bin/python tools/liveportrait/gonogo.py SOURCE.png OUT_DIR
"""
import os
import sys
from pathlib import Path

import cv2
import numpy as np

ARGS = [str(Path(a).resolve()) for a in sys.argv[1:]]     # before the chdir below
LP = Path(__file__).resolve().parent / "vendor" / "LivePortrait"
sys.path.insert(0, str(LP))
os.chdir(LP)                                   # LivePortrait resolves its weights relative to the repo

from src.config.argument_config import ArgumentConfig  # noqa: E402
from src.config.crop_config import CropConfig  # noqa: E402
from src.config.inference_config import InferenceConfig  # noqa: E402
from src.gradio_pipeline import GradioPipeline  # noqa: E402


def partial_fields(target_class, kwargs):
    return target_class(**{k: v for k, v in kwargs.items() if hasattr(target_class, k)})


def sheet(images, labels, cols, path, w=300):
    tiles = []
    for im, lab in zip(images, labels):
        im = cv2.resize(im, (w, int(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA)
        cv2.rectangle(im, (0, 0), (w, 26), (0, 0, 0), -1)
        cv2.putText(im, lab, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(im)
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(path), cv2.cvtColor(np.vstack(rows), cv2.COLOR_RGB2BGR))


def main(source, out_dir):
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    args = ArgumentConfig()
    pipe = GradioPipeline(inference_cfg=partial_fields(InferenceConfig, args.__dict__),
                          crop_cfg=partial_fields(CropConfig, args.__dict__), args=args)
    eye0, lip0 = pipe.init_retargeting_image(2.5, 0.0, 0.0, input_image=str(source))
    print("source eye-open ratio", eye0, "lip-open ratio", lip0)

    def run(pitch=0.0, yaw=0.0, roll=0.0, eye=None, gx=0.0, gy=0.0):
        _, full = pipe.execute_image_retargeting(
            eye0 if eye is None else eye, lip0, pitch, yaw, roll, 0.0, 0.0, 1.0, 0, 0, 0, 0, 0, 0, 0,
            gx, gy, str(source), 2.5, True, True)
        return full

    rest = run()                                # the rest pose through LivePortrait, for its size
    before = cv2.resize(cv2.cvtColor(cv2.imread(str(source)), cv2.COLOR_BGR2RGB), (rest.shape[1], rest.shape[0]))
    sweeps = {
        "yaw": [(f"yaw {v:+d}", dict(yaw=v)) for v in (-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25)],
        "pitch": [(f"pitch {v:+d}", dict(pitch=v)) for v in (-15, -10, -5, 0, 5, 10, 15)],
        "gaze": [(f"gaze x {v:+d}", dict(gx=v)) for v in (-30, -15, 15, 30)] +
                [(f"gaze y {v:+d}", dict(gy=v)) for v in (-40, -20, 20, 40)],
        "eyes": [(f"eye-open {v:.2f}", dict(eye=v)) for v in (eye0, 0.30, 0.20, 0.10, 0.0)],
    }
    for name, items in sweeps.items():
        imgs, labs = [before], ["before (source)"]
        for lab, kw in items:
            imgs.append(run(**kw))
            labs.append(lab)
        sheet(imgs, labs, 4, out / f"gonogo_{name}.png")
        print("wrote", out / f"gonogo_{name}.png")


if __name__ == "__main__":
    main(*ARGS[:2])
