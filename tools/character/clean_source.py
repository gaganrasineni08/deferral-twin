"""
Step 1 of the landing-page character: clean the front-facing reel frame once.

Real-ESRGAN realesr-general-x4v3 with moderate denoise (the official tool's "denoise strength"
0.5: the general and the wdn weights blended half and half). No GFPGAN, no anime model.

    python tools/character/clean_source.py FRAME.png GENERAL.pth WDN.pth OUT.png

FRAME is frame 11 of the reference reel (720 x 1280); the character is cropped above the
reel's caption, so the output is 1640 x 1940.
"""
import sys
import time

import numpy as np
import torch
from PIL import Image
from torch import nn
from torch.nn import functional as F

CROP = (150, 170, 560, 655)          # the character in the reel frame, above the "CURSOR TRACKING" caption
DENOISE = 0.5                        # moderate


class SRVGGNetCompact(nn.Module):
    """The realesr-general-x4v3 network (from Real-ESRGAN's srvgg_arch.py)."""

    def __init__(self, num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32, upscale=4):
        super().__init__()
        self.upscale = upscale
        self.body = nn.ModuleList([nn.Conv2d(num_in_ch, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)])
        for _ in range(num_conv):
            self.body.append(nn.Conv2d(num_feat, num_feat, 3, 1, 1))
            self.body.append(nn.PReLU(num_parameters=num_feat))
        self.body.append(nn.Conv2d(num_feat, num_out_ch * upscale * upscale, 3, 1, 1))
        self.upsampler = nn.PixelShuffle(upscale)

    def forward(self, x):
        out = x
        for layer in self.body:
            out = layer(out)
        return self.upsampler(out) + F.interpolate(x, scale_factor=self.upscale, mode="nearest")


def load(path):
    sd = torch.load(path, map_location="cpu")
    return sd.get("params", sd.get("params_ema", sd))


def main(frame, general, wdn, out):
    a, b = load(general), load(wdn)
    net = SRVGGNetCompact()
    net.load_state_dict({k: DENOISE * a[k] + (1 - DENOISE) * b[k] for k in a}, strict=True)
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    net.eval().to(dev)
    img = Image.open(frame).convert("RGB").crop(CROP)
    x = torch.from_numpy(np.asarray(img).astype(np.float32) / 255).permute(2, 0, 1)[None].to(dev)
    t = time.time()
    with torch.no_grad():
        y = net(x).clamp(0, 1)
    Image.fromarray((y[0].permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8)).save(out)
    print(f"{img.size} -> {Image.open(out).size} in {time.time() - t:.1f}s on {dev}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
