"""
Turns a still photo + audio into a lip-synced talking-head video via SadTalker.
Run src/setup_avatar.sh once first.

Usage:
  python src/avatar_video.py --photo data/raw/avatar_photo/photo.jpg \
      --audio data/processed/audio/output.wav --out data/processed/video/output.mp4
"""
import argparse
import subprocess
import shutil
from pathlib import Path


def _python_bin():
    """SadTalker runs in its own Python 3.10 env (see setup_avatar.sh) when present."""
    venv_py = Path("/content/sadenv/bin/python")
    return str(venv_py) if venv_py.exists() else "python"


def generate_avatar_video(photo_path: str, audio_path: str, out_path: str,
                          sadtalker_dir: str = "/content/SadTalker"):
    sadtalker_dir = Path(sadtalker_dir)
    if not sadtalker_dir.exists():
        raise FileNotFoundError(f"{sadtalker_dir} not found -- run `bash src/setup_avatar.sh` first.")

    result_dir = Path("data/processed/video/_sadtalker_raw")
    result_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        _python_bin(), str(sadtalker_dir / "inference.py"),
        "--driven_audio", str(Path(audio_path).resolve()),
        "--source_image", str(Path(photo_path).resolve()),
        "--result_dir", str(result_dir.resolve()),
        "--still",
        "--preprocess", "full",
        "--enhancer", "gfpgan",
    ]
    print("Running SadTalker inference...")
    import os
    env = dict(os.environ, MPLBACKEND="Agg")   # Colab's inline backend is missing in the SadTalker env
    subprocess.run(cmd, check=True, cwd=str(sadtalker_dir), env=env)

    generated = sorted(result_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not generated:
        raise RuntimeError("SadTalker ran but no output video was found.")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(generated[-1], out_path)
    print(f"Saved avatar video -> {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--photo", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--out", default="data/processed/video/output.mp4")
    parser.add_argument("--sadtalker_dir", default="/content/SadTalker")
    args = parser.parse_args()
    generate_avatar_video(args.photo, args.audio, args.out, args.sadtalker_dir)
