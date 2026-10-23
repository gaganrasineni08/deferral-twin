"""
Ties text -> cloned voice -> lip-synced avatar video together.

Usage:
  python src/generate_multimodal_response.py \
      --text "Hi, I'm doing well, thanks for asking." \
      --speaker_wav data/raw/voice_samples/reference.wav \
      --photo data/raw/avatar_photo/photo.jpg \
      --language en
"""
import argparse
from voice_clone import clone_voice
from avatar_video import generate_avatar_video


def generate_multimodal_response(text, speaker_wav, photo_path, language="en",
                                  audio_out="data/processed/audio/output.wav",
                                  video_out="data/processed/video/output.mp4"):
    print("Step 1/2: cloning voice...")
    clone_voice(text=text, speaker_wav=speaker_wav, language=language, out_path=audio_out)

    print("Step 2/2: generating avatar video...")
    generate_avatar_video(photo_path=photo_path, audio_path=audio_out, out_path=video_out)

    print(f"\nDone. Video ready at: {video_out}")
    return video_out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--speaker_wav", default="data/raw/voice_samples/reference.wav")
    parser.add_argument("--photo", default="data/raw/avatar_photo/photo.jpg")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()
    generate_multimodal_response(text=args.text, speaker_wav=args.speaker_wav, photo_path=args.photo, language=args.language)
