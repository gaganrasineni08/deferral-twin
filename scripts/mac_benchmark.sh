#!/bin/bash
# HANDOFF.md section 8.4 on the target Mac. Run after setup has finished (the app's own
# setup, or `python3 -m bootstrap.engine`). Writes BENCHMARK.md here and benchmark.json
# into the app folder, which the app reads for its time estimates.
set -euo pipefail
cd "$(dirname "$0")/.."
DT="${DEFERRALTWIN_HOME:-$HOME/Library/Application Support/DeferralTwin}"
PY="$DT/envs/main/bin/python"
[ -x "$PY" ] || { echo "Setup hasn't run yet: no $PY. Open DeferralTwin.app (or ./run.sh) first."; exit 1; }
export PATH="$DT/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 COQUI_TOS_AGREED=1 \
       PYTHONPYCACHEPREFIX="$DT/cache/pyc" TOKENIZERS_PARALLELISM=false
echo "Close other heavy apps first: peak memory is part of what's measured."
"$PY" -m app.benchmark --home "$DT" --full --md BENCHMARK.md
echo "Wrote BENCHMARK.md and $DT/benchmark.json"
