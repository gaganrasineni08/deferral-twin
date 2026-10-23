#!/bin/bash
# LivePortrait weights for the human model only (no animals, no X-Pose), resumable.
set -e
cd "$(dirname "$0")/vendor/LivePortrait/pretrained_weights"
B=https://huggingface.co/KlingTeam/LivePortrait/resolve/main
for f in insightface/models/buffalo_l/2d106det.onnx insightface/models/buffalo_l/det_10g.onnx \
         liveportrait/base_models/appearance_feature_extractor.pth liveportrait/base_models/motion_extractor.pth \
         liveportrait/base_models/spade_generator.pth liveportrait/base_models/warping_module.pth \
         liveportrait/landmark.onnx liveportrait/retargeting_models/stitching_retargeting_module.pth; do
  mkdir -p "$(dirname "$f")"
  curl -fL --retry 8 --retry-all-errors --connect-timeout 20 --speed-limit 2000 --speed-time 60 -C - -s -o "$f" "$B/$f"
  echo "ok $(du -h "$f" | cut -f1) $f"
done
