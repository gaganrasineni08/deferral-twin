"""Run GFPGAN over a folder of PNG frames (used to sharpen Wav2Lip output).
Run with the SadTalker env python:  /content/sadenv/bin/python enhance_frames.py IN_DIR OUT_DIR [weight]

On the Mac, GFPGAN_HOME points at the downloaded weights (models/gfpgan): it
holds GFPGANv1.4.pth, and gfpgan/weights/ holds the two face-parsing models
that GFPGANer would otherwise download into the working directory.
"""
import glob
import os
import sys

import cv2
from gfpgan import GFPGANer

src, dst = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
weight = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
home = os.environ.get("GFPGAN_HOME")
cands = []
if home:
    cands += glob.glob(os.path.join(home, "GFPGANv1.4.pth"))
    os.chdir(home)                      # GFPGANer looks for gfpgan/weights/ relative to here
for root in ("/content/SadTalker", "/content/Wav2Lip", "/root/.cache", "/content"):
    cands += glob.glob(os.path.join(root, "**", "GFPGANv1.4.pth"), recursive=True)
if not cands:
    raise SystemExit("GFPGANv1.4.pth not found. Run src/setup_avatar.sh first "
                     "(it downloads the GFPGAN weights with SadTalker).")
w = cands[0]
r = GFPGANer(model_path=w, upscale=1, arch="clean", channel_multiplier=2, bg_upsampler=None)
os.makedirs(dst, exist_ok=True)
files = sorted(glob.glob(os.path.join(src, "*.png")))
for i, f in enumerate(files):
    out = r.enhance(cv2.imread(f), has_aligned=False, only_center_face=True,
                    paste_back=True, weight=weight)[2]
    cv2.imwrite(os.path.join(dst, os.path.basename(f)), out)
    if i % 25 == 0:
        print(i, "/", len(files), flush=True)
print("done")
