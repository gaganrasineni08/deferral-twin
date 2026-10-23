"""
Where everything lives on the Mac (HANDOFF.md section 9.2):

  ~/Library/Application Support/DeferralTwin/
    envs/main/       this server, MLX, embedder, XTTS, src/
    envs/wav2lip/    isolated Wav2Lip env
    envs/gfpgan/     isolated GFPGAN env (sharpening; was the SadTalker env on Colab)
    envs/wav2lip-src/  the Wav2Lip code
    models/          every weight file, downloaded once
    twin/            the person's data: the section 5.4 layout + voice, photo, notes, corrections
    cache/clips/     rendered avatar videos, keyed by answer text
    logs/
    state.json       setup progress
    settings.json    speak / video / language / sharpening defaults
    benchmark.json   measured timings (self-test and tools/benchmark), used for estimates

On Windows the same layout is in %LOCALAPPDATA%\\DeferralTwin, with each environment's
python at Scripts\\python.exe, llama.cpp in envs\\llama.cpp and a GGUF copy of the model.

DEFERRALTWIN_HOME overrides the location (tests, a second install, Linux dev).
Nothing is ever written next to the code, so the .app bundle stays read-only.
"""
import os
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent          # src/, app/, configs/, docs/
WINDOWS = sys.platform == "win32"
EXE = ".exe" if WINDOWS else ""


def default_home():
    if os.environ.get("DEFERRALTWIN_HOME"):
        return Path(os.environ["DEFERRALTWIN_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "DeferralTwin"
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DeferralTwin"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "DeferralTwin"


class Paths:
    SUBJECT_PID = "phase2_me"

    def __init__(self, home=None):
        self.home = Path(home).expanduser() if home else default_home()
        h = self.home
        self.envs, self.models, self.twin, self.cache = h / "envs", h / "models", h / "twin", h / "cache"
        self.clips, self.audio_cache, self.tmp = self.cache / "clips", self.cache / "audio", self.cache / "tmp"
        self.logs, self.bin = h / "logs", h / "bin"
        self.state, self.settings, self.benchmark = h / "state.json", h / "settings.json", h / "benchmark.json"
        # the person's twin, in the same layout as a Twin-2K-500 participant (section 5.4)
        self.bench_root = self.twin / "benchmark" / "phase2"
        self.subject = self.bench_root / self.SUBJECT_PID

    def ensure(self):
        for d in (self.twin, self.clips, self.audio_cache, self.tmp, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        (self.twin / "uploads").mkdir(exist_ok=True)
        return self

    # ---- the person's files ----
    @property
    def answers(self):
        return self.twin / "phase2_answers.json"

    @property
    def draft(self):
        return self.twin / "draft.json"

    @property
    def ref_wav(self):
        return self.twin / "ref.wav"

    @property
    def photo(self):
        return self.twin / "photo_fixed.jpg"

    # ---- models (filled by bootstrap/engine.py) ----
    @property
    def llm_model(self):
        if WINDOWS:     # llama.cpp reads one GGUF file (manifest-windows.json)
            return self.models / "llm" / "Phi-3-mini-4k-instruct-gguf" / "Phi-3-mini-4k-instruct-q4.gguf"
        return self.models / "llm" / "Phi-3-mini-4k-instruct-4bit"

    @property
    def llama_server(self):
        return self.envs / "llama.cpp" / f"llama-server{EXE}"

    @property
    def embedder(self):
        return self.models / "embedder" / "all-mpnet-base-v2"

    @property
    def xtts(self):
        return self.models / "xtts"

    @property
    def translator(self):
        return self.models / "mt" / "opus-mt-en-hi"

    @property
    def wav2lip_ckpt(self):
        return self.models / "wav2lip" / "wav2lip_gan.pth"

    @property
    def s3fd(self):
        return self.models / "wav2lip" / "s3fd.pth"

    @property
    def yunet(self):
        return self.models / "face" / "face_detection_yunet_2023mar.onnx"

    @property
    def gfpgan_home(self):
        return self.models / "gfpgan"

    # ---- environments ----
    def py(self, env):
        return self.envs / env / ("Scripts" if WINDOWS else "bin") / f"python{EXE}"

    @property
    def wav2lip_src(self):
        return self.envs / "wav2lip-src"

    @property
    def ffmpeg(self):
        return self.bin / f"ffmpeg{EXE}"
