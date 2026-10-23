"""
Voice cloning with Coqui XTTS-v2 (few-shot, no training needed).

Setup (Colab):
  !pip install coqui-tts      # maintained fork; same `TTS` import, works on Python 3.12/3.13

Usage (single clip):
  python src/voice_clone.py --text "Hello, this is my digital twin." \
      --speaker_wav data/raw/voice_samples/reference.wav \
      --language en \
      --out data/processed/audio/output.wav

Usage (multiple clips -- generally improves quality, comma-separated):
  python src/voice_clone.py --text "Hello." \
      --speaker_wav data/raw/voice_samples/clip1.wav,data/raw/voice_samples/clip2.wav \
      --language en

Supported languages: en, es, fr, de, it, pt, pl, tr, ru, nl, cs, ar, zh-cn, ja, hu, ko, hi
"""
import argparse
from pathlib import Path


def clone_voice(text: str, speaker_wav, language: str, out_path: str):
    import os
    os.environ.setdefault("COQUI_TOS_AGREED", "1")   # XTTS-v2 licence (non-commercial); skips the prompt
    from TTS.api import TTS
    import torch

    # Accept either a single path (str) or multiple (list) -- XTTS-v2 supports both
    if isinstance(speaker_wav, str) and "," in speaker_wav:
        speaker_wav = [p.strip() for p in speaker_wav.split(",")]

    device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"Loading XTTS-v2 on {device}...")
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tts.tts_to_file(text=text, speaker_wav=speaker_wav, language=language, file_path=out_path)
    print(f"Saved cloned speech -> {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--speaker_wav", required=True,
                         help="Single path, or comma-separated list of paths for multiple reference clips")
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", default="data/processed/audio/output.wav")
    args = parser.parse_args()
    clone_voice(args.text, args.speaker_wav, args.language, args.out)

