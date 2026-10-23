#!/usr/bin/env bash
# Installs Wav2Lip (lip-sync) in its own env. Run AFTER setup_avatar.sh (GFPGAN sharpening uses that env).
set -e
cd /content
rm -rf Wav2Lip
git clone -q https://github.com/Rudrabha/Wav2Lip.git
cd Wav2Lip
pip -q install uv
uv venv /content/wavenv --python 3.9 -q
uv pip install -q --python /content/wavenv/bin/python torch==2.0.1 torchvision==0.15.2 numpy==1.23.5 opencv-python-headless==4.8.1.78 librosa==0.9.2 scipy==1.10.1 numba==0.56.4 tqdm "setuptools<70"
mkdir -p checkpoints face_detection/detection/sfd
get(){ out=$1; shift; for u in "$@"; do curl -sL --fail -o "$out" "$u" && [ $(stat -c%s "$out") -gt 1000000 ] && echo "OK $out" && return; done; echo "FAILED $out"; exit 1; }
get checkpoints/wav2lip_gan.pth https://huggingface.co/Nekochu/Wav2Lip/resolve/main/wav2lip_gan.pth https://huggingface.co/numz/wav2lip_studio/resolve/main/Wav2lip/wav2lip_gan.pth
get face_detection/detection/sfd/s3fd.pth https://huggingface.co/Nekochu/Wav2Lip/resolve/main/s3fd.pth https://www.adrianbulat.com/downloads/python-fan/s3fd-619a316812.pth
