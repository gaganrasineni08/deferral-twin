"""
One call that turns a person's uploads into a working twin.

The person supplies three things -- interview answers, a voice clip, a photo --
and nothing else. Everything below is automatic: dataset split, fact index,
behaviour profile, audio conversion, photo orientation, voice + avatar wiring,
replay of past corrections, and a backup to Drive so a Colab restart costs
nothing.

  from build_twin import build
  twin = build(answers="phase2_answers.json", voice="voice.m4a", photo="me.jpg",
               llm_fn=my_llm, drive="/content/drive/MyDrive/twin_assets")
  twin.app().launch(share=True)

Only the subject of the twin should supply a voice and photo -- that is the
consent boundary the interview page records.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import load_phase2                                    # noqa: E402
from persona import load_profile                      # noqa: E402

ASSET_NAMES = ("phase2_answers.json", "ref.wav", "photo_fixed.jpg")


def _run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr.decode()[-400:]}")


def prepare_voice(src, out_wav):
    """Any audio the phone produced -> mono 22.05 kHz wav, which is what XTTS wants."""
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
          "-ac", "1", "-ar", "22050", str(out_wav)])
    return str(out_wav)


def prepare_photo(src, out_jpg, max_side=1024):
    """Fix phone rotation (sideways photos break face detection) and cap the size."""
    from PIL import Image, ImageOps
    out_jpg = Path(out_jpg)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    im.thumbnail((max_side, max_side))
    im.save(out_jpg, quality=95)
    return str(out_jpg)


def _restore_from_drive(drive, work):
    """Colab wipes /content on restart; Drive does not. Pull back what we saved."""
    if not drive:
        return {}
    d = Path(drive)
    found = {}
    for name in ASSET_NAMES:
        if (d / name).exists() and not (work / name).exists():
            shutil.copy2(d / name, work / name)
            found[name] = str(work / name)
    return found


def _backup_to_drive(drive, paths, bench_dir):
    if not drive:
        return
    d = Path(drive)
    try:
        d.mkdir(parents=True, exist_ok=True)
        for name, p in paths.items():
            if p and Path(p).exists():
                shutil.copy2(p, d / name)
        dest = d / "benchmark" / Path(bench_dir).name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(bench_dir, dest)
    except OSError as e:                      # a full or unmounted Drive must not kill the demo
        print(f"  (Drive backup skipped: {e})")


class Twin:
    def __init__(self, bench_root, pid, profile, mm=None, llm_fn=None, assets=None):
        self.bench_root, self.pid = str(bench_root), pid
        self.profile, self.mm, self.llm_fn = profile, mm, llm_fn
        self.assets = assets or {}

    @property
    def dir(self):
        return Path(self.bench_root) / self.pid

    def app(self, **kw):
        """The Gradio demo, with voice/avatar enabled for this person only."""
        import demo_app
        if self.llm_fn is None:
            raise RuntimeError("No llm_fn given -- load your model first and pass llm_fn=...")
        kw.setdefault("default_pid", self.pid)
        kw.setdefault("mm", self.mm)
        kw.setdefault("mm_pids", (self.pid,))
        return demo_app.build_app(self.bench_root, self.llm_fn, **kw)

    def session(self):
        import demo_app
        return demo_app.TwinSession(self.bench_root, self.pid, self.llm_fn)


def build(answers=None, voice=None, photo=None, llm_fn=None,
          out_root="data/benchmark/phase2", pid="phase2_me", work_dir="/content",
          drive=None, multimodal=True, seed=0, verbose=True, mm_kwargs=None):
    """
    answers/voice/photo: paths to what the person uploaded. Any of them may be
    omitted if a copy is already on Drive from an earlier run.
    drive: any backup folder -- Google Drive on Colab, or None on the Mac, where
    work_dir itself is already persistent.
    mm_kwargs: extra MultiModal arguments (the Mac app's model and env paths).
    Returns a Twin. Does not load any language model -- pass llm_fn for that.
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    restored = _restore_from_drive(drive, work)
    if verbose and restored:
        print("Restored from Drive:", ", ".join(restored))

    answers = answers or restored.get("phase2_answers.json") or (
        str(work / "phase2_answers.json") if (work / "phase2_answers.json").exists() else None)
    if not answers:
        raise SystemExit("No interview answers found. Upload phase2_answers.json first.")

    if verbose:
        print("1/4  Interview -> dataset")
    info = load_phase2.convert(answers, out_root=out_root, pid=pid, seed=seed)
    bench_dir = Path(info["dir"])
    profile = load_profile(bench_dir / "profile.json")

    if verbose:
        print("2/4  Voice and photo")
    ref_wav = str(work / "ref.wav") if (work / "ref.wav").exists() else None
    if voice:
        ref_wav = prepare_voice(voice, work / "ref.wav")
    photo_fixed = str(work / "photo_fixed.jpg") if (work / "photo_fixed.jpg").exists() else None
    if photo:
        photo_fixed = prepare_photo(photo, work / "photo_fixed.jpg")
    if verbose:
        print(f"     voice: {ref_wav or 'none -- twin will be text only'}")
        print(f"     photo: {photo_fixed or 'none -- no avatar'}")

    mm = None
    if multimodal and ref_wav and photo_fixed:
        if verbose:
            print("3/4  Voice + avatar pipeline (models load on first use)")
        try:
            from multimodal import MultiModal
            mm = MultiModal(ref_wav=ref_wav, photo=photo_fixed, **(mm_kwargs or {}))
        except Exception as e:                      # missing deps must not block the text twin
            print(f"     multimodal unavailable ({type(e).__name__}: {e}); text-only twin")
    elif verbose:
        print("3/4  Voice + avatar skipped (" +
              ("turned off" if not multimodal else "need both a voice clip and a photo") + ")")

    if verbose:
        print("4/4  Backup")
    assets = {"phase2_answers.json": answers, "ref.wav": ref_wav, "photo_fixed.jpg": photo_fixed}
    _backup_to_drive(drive, assets, bench_dir)

    if verbose:
        n_corr = 0
        log = bench_dir / "feedback_demo.jsonl"
        if log.exists():
            n_corr = sum(1 for _ in open(log, encoding="utf-8"))
        print(f"\nTwin ready: {bench_dir}")
        print(f"  {info['train']} facts | {info['test']} held-out questions | "
              f"{info['baseline']} consistency pairs | {n_corr} past corrections")
    return Twin(out_root, pid, profile, mm=mm, llm_fn=llm_fn, assets=assets)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers")
    ap.add_argument("--voice")
    ap.add_argument("--photo")
    ap.add_argument("--out_root", default="data/benchmark/phase2")
    ap.add_argument("--pid", default="phase2_me")
    ap.add_argument("--work_dir", default="/content")
    ap.add_argument("--drive", default=None)
    ap.add_argument("--no_multimodal", action="store_true")
    a = ap.parse_args()
    build(a.answers, a.voice, a.photo, out_root=a.out_root, pid=a.pid,
          work_dir=a.work_dir, drive=a.drive, multimodal=not a.no_multimodal)
