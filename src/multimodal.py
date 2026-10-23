"""
Voice + avatar output for the twin: text -> cloned voice (XTTS-v2) -> talking-head video (Wav2Lip + GFPGAN; SadTalker optional).

Use ONLY with the voice and face of a person who has given consent (the subject of the twin).

  mm = MultiModal(ref_wav="/content/ref.wav", photo="/content/photo_fixed.jpg")
  wav = mm.speak("Hello", lang="en")        # lang "hi" translates English -> Hindi first
  mp4 = mm.animate(wav)                     # Wav2Lip lip-sync + GFPGAN sharpening (backend='sadtalker' for the old way)

The defaults are the Colab paths. The Mac app passes its own (app/media.py):
local XTTS and translator folders instead of downloads, the Wav2Lip and
GFPGAN environments under Application Support, CPU for XTTS (MPS lacks ops
it uses), and sharpening off unless asked for.
"""
import glob
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


class MultiModal:
    def __init__(self, ref_wav, photo, sadtalker_dir="/content/SadTalker",
                 sad_python="/content/sadenv/bin/python", out_dir="/content/mm_out",
                 enhancer=False, backend="wav2lip", wav2lip_dir="/content/Wav2Lip",
                 wav2lip_python="/content/wavenv/bin/python", sharpen=True,
                 xtts_dir=None, mt_dir=None, tts_device=None, wav2lip_checkpoint=None,
                 gfpgan_python=None, gfpgan_home=None, env=None, face_box=None):
        self.backend, self.sharpen = backend, sharpen
        self.xtts_dir, self.mt_dir, self.tts_device = xtts_dir, mt_dir, tts_device
        self.wav2lip_checkpoint = wav2lip_checkpoint or str(Path(wav2lip_dir) / "checkpoints" / "wav2lip_gan.pth")
        self.gfpgan_python = gfpgan_python or sad_python     # GFPGAN lived in the SadTalker env on Colab
        self.gfpgan_home = gfpgan_home
        self.env = env                                       # PATH with ffmpeg, offline flags, ...
        self.face_box = face_box                             # (top, bottom, left, right); self-test only
        self.wav2lip_dir, self.wav2lip_python = wav2lip_dir, wav2lip_python
        self.ref_wav, self.photo = ref_wav, photo
        self.sadtalker_dir, self.sad_python = sadtalker_dir, sad_python
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.enhancer = enhancer
        self._tts = None
        self._mt = None

    # ---- voice ----
    def _load_tts(self):
        if self._tts is None:
            os.environ.setdefault("COQUI_TOS_AGREED", "1")
            import torch
            from TTS.api import TTS
            dev = self.tts_device or ("cuda" if torch.cuda.is_available() else "cpu")
            if self.xtts_dir:                              # weights already on disk, no download
                d = Path(self.xtts_dir)
                self._tts = TTS(model_path=str(d), config_path=str(d / "config.json")).to(dev)
            else:
                self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(dev)
        return self._tts

    def unload_tts(self):
        self._tts = None

    def translate_to_hindi(self, text):
        """English -> Hindi with a small Marian model (quality is moderate)."""
        if self._mt is None:
            from transformers import MarianMTModel, MarianTokenizer
            name = self.mt_dir or "Helsinki-NLP/opus-mt-en-hi"
            self._mt = (MarianTokenizer.from_pretrained(name), MarianMTModel.from_pretrained(name))
        tok, model = self._mt
        batch = tok([text], return_tensors="pt", padding=True, truncation=True, max_length=256)
        out = model.generate(**batch, max_new_tokens=200)
        return tok.decode(out[0], skip_special_tokens=True)

    @staticmethod
    def clean_for_speech(text, max_chars=420):
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^[>*\-\s]+", "", text)
        text = re.sub(r"[*_#`\"“”]", "", text)
        if len(text) > max_chars:
            cut = text[:max_chars]
            end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), cut.rfind("। "), cut.rfind("।"))
            text = cut[:end + 1] if end > 60 else cut
        return text

    @staticmethod
    def sentences(text):
        """Sentence by sentence (the Hindi danda included): XTTS garbles or drops words in long inputs."""
        parts = [p.strip() for p in re.split(r"(?<=[.!?।])\s+|(?<=।)", text) if p.strip()]
        out = []
        for p in parts:                                   # very short pieces ride along with the next one
            if out and len(out[-1]) < 12:
                out[-1] = out[-1] + " " + p
            else:
                out.append(p)
        return out or [text]

    def speak(self, text, lang="en", out_path=None, speaker_wav=None):
        text = self.clean_for_speech(text)
        if lang == "hi" and not re.search(r"[\u0900-\u097F]", text):      # already Hindi: speak it as it is
            text = self.translate_to_hindi(text)
        tts = self._load_tts()
        out = str(out_path or self.out_dir / f"speech_{int(time.time()*1000)}_{lang}.wav")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        import numpy as np
        import soundfile as sf
        sr = int(getattr(getattr(tts, "synthesizer", None), "output_sample_rate", 0) or 24000)
        # top-p / top-k sampling: measured clearer Hindi than the defaults (Whisper CER 0.16-0.17 vs 0.17-0.19)
        kw = dict(temperature=0.65, top_p=0.7, top_k=30)
        gap = np.zeros(int(sr * 0.18), np.float32)
        chunks = []
        for s in self.sentences(text):
            try:
                wav = tts.tts(text=s, speaker_wav=speaker_wav or self.ref_wav, language=lang, **kw)
            except TypeError:                                 # an older TTS without these options
                wav = tts.tts(text=s, speaker_wav=speaker_wav or self.ref_wav, language=lang)
            w = np.asarray(wav, np.float32)
            nz = np.where(np.abs(w) > 0.01)[0]                # trim silence (and the odd breath) at both ends
            if len(nz):
                w = w[max(0, nz[0] - int(sr * 0.03)): nz[-1] + int(sr * 0.08)]
            chunks += [w, gap]
        audio = np.concatenate(chunks[:-1]) if chunks else np.zeros(sr // 2, np.float32)
        peak = float(np.max(np.abs(audio))) or 1.0
        rms = float(np.sqrt(np.mean(audio ** 2))) or 1e-4
        audio = audio * min(0.9 / peak, 0.08 / rms)           # even loudness, no clipping
        sf.write(out, audio, sr)
        return out

    # ---- avatar ----
    def animate(self, wav_path, out_path=None, sharpen=None, on_stage=None):
        if self.backend == "wav2lip":
            return self._animate_wav2lip(wav_path, out_path=out_path, sharpen=sharpen, on_stage=on_stage)
        return self._animate_sadtalker(wav_path)

    def _run(self, cmd, **kw):
        """stdin from /dev/null: ffmpeg (which Wav2Lip calls through a shell, without
        -nostdin) otherwise eats the parent's stdin and corrupts what runs next."""
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=kw.pop("env", self.env), **kw)
        if r.returncode != 0:
            tail = (r.stderr or b"").decode("utf-8", "replace")[-800:]
            raise RuntimeError(f"{Path(str(cmd[1] if len(cmd) > 1 else cmd[0])).name} failed: {tail}")
        return r

    def _animate_wav2lip(self, wav_path, out_path=None, sharpen=None, on_stage=None):
        """Wav2Lip lip-sync, then (optionally) GFPGAN on every frame for clarity (verified on Colab).

        Runs in its own job folder with short relative names: inference.py writes
        temp/ into its working directory, splits --face on the first dot, and
        passes paths to ffmpeg through a shell, so spaces or dots elsewhere in the
        path (Application Support, a user called john.doe) would break it."""
        sharpen = self.sharpen if sharpen is None else sharpen
        d = self.out_dir / f"w2l_{int(time.time()*1000)}"
        (d / "temp").mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.photo, d / "face.jpg")
        shutil.copy2(wav_path, d / "audio.wav")
        cmd = [self.wav2lip_python, str(Path(self.wav2lip_dir) / "inference.py"),
               "--checkpoint_path", str(Path(self.wav2lip_checkpoint).resolve()),
               "--face", "face.jpg", "--audio", "audio.wav",
               "--outfile", "raw.mp4", "--pads", "0", "10", "0", "0",
               "--resize_factor", "1", "--nosmooth"]
        if self.face_box:
            cmd += ["--box"] + [str(int(v)) for v in self.face_box]
        if on_stage:
            on_stage("lipsync")
        self._run(cmd, cwd=str(d))
        raw = d / "raw.mp4"
        if not raw.exists():
            raise RuntimeError("Wav2Lip produced no video")
        final = raw
        try:
            comp = d / "comp.mp4"
            self._composite(raw, d / "face.jpg", d / "audio.wav", comp)
            final = raw = comp
        except Exception:
            pass                                          # keep Wav2Lip's own output if the clean-up fails
        if sharpen:
            if on_stage:
                on_stage("sharpen")
            fin, fout = d / "fr_in", d / "fr_out"
            fin.mkdir(exist_ok=True)
            ff = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
            self._run(ff + ["-i", str(raw), str(fin / "%05d.png")])
            enh = str(Path(__file__).with_name("enhance_frames.py"))
            env = dict(self.env or os.environ, MPLBACKEND="Agg")
            if self.gfpgan_home:
                env["GFPGAN_HOME"] = str(self.gfpgan_home)
            self._run([self.gfpgan_python, enh, str(fin), str(fout)], env=env)
            final = d / "final.mp4"
            self._run(ff + ["-framerate", "25", "-i", str(fout / "%05d.png"), "-i", str(wav_path),
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "14",
                            "-c:a", "aac", "-shortest", str(final)])
        if out_path:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(final), str(out_path))
            shutil.rmtree(d, ignore_errors=True)
            return str(out_path)
        return str(final)

    def _composite(self, raw, photo, wav, out):
        """Wav2Lip regenerates the whole lower-face box at 96x96 and pastes it back: the face goes soft, the box
        edge shows and the colour shifts. Keep the original photo everywhere and blend in only the mouth and jaw,
        through a feathered oval, sharpened and colour-matched to the photo."""
        import cv2
        import numpy as np
        cap = cv2.VideoCapture(str(raw))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frames = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            frames.append(f)
        if not frames:
            raise RuntimeError("no frames")
        H, W = frames[0].shape[:2]
        ph = cv2.resize(cv2.imread(str(photo)), (W, H))
        # where Wav2Lip changed the picture: its face box
        blur = lambda im: cv2.GaussianBlur(im, (9, 9), 0)
        pb = blur(ph).astype(np.int16)
        diff = np.mean([np.abs(blur(f).astype(np.int16) - pb).mean(axis=2) for f in frames[::3]], axis=0)
        ys, xs = np.where(diff > 4)
        if len(xs) < 50:
            raise RuntimeError("no change found")
        x0, x1, y0, y1 = np.percentile(xs, 1), np.percentile(xs, 99), np.percentile(ys, 1), np.percentile(ys, 99)
        bw, bh = x1 - x0, y1 - y0
        # the mouth and jaw: the lower part of that box
        mask = np.zeros((H, W), np.float32)
        cv2.ellipse(mask, (int(x0 + bw / 2), int(y0 + bh * 0.70)), (int(bw * 0.34), int(bh * 0.27)), 0, 0, 360, 1.0, -1)
        k = max(9, int(bw * 0.09)) | 1
        mask = cv2.GaussianBlur(mask, (k, k), 0)[..., None]
        ring = (mask[..., 0] > 0.05) & (mask[..., 0] < 0.6)                   # skin around the mouth, for the colour match
        pf = ph.astype(np.float32)
        ff = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
              "-r", str(fps), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
              "-preset", "medium", "-c:a", "aac", "-shortest", str(out)]
        proc = subprocess.Popen(ff, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=self.env)
        for f in frames:
            g = f.astype(np.float32)
            g = cv2.addWeighted(g, 1.7, cv2.GaussianBlur(g, (0, 0), 1.6), -0.7, 0)   # unsharp: the 96 px mouth, crisper
            if ring.any():
                g += (pf[ring].mean(axis=0) - g[ring].mean(axis=0))                   # match the photo's skin colour
            o = np.clip(pf * (1 - mask) + g * mask, 0, 255).astype(np.uint8)
            proc.stdin.write(o.tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(proc.stderr.read().decode()[-300:])
        return out

    def _animate_sadtalker(self, wav_path):
        result_dir = self.out_dir / f"video_{int(time.time()*1000)}"
        cmd = [self.sad_python, "inference.py", "--driven_audio", str(wav_path),
               "--source_image", str(self.photo), "--result_dir", str(result_dir),
               "--still", "--preprocess", "full"]
        if self.enhancer:
            cmd += ["--enhancer", "gfpgan"]
        env = dict(os.environ, MPLBACKEND="Agg")
        subprocess.run(cmd, check=True, cwd=self.sadtalker_dir, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        vids = sorted(glob.glob(str(result_dir / "**" / "*.mp4"), recursive=True),
                      key=os.path.getmtime)
        if not vids:
            raise RuntimeError("SadTalker produced no video")
        return vids[-1]
