#!/bin/bash
# Developer entry point (HANDOFF.md section 4.2): the same server the app's window uses,
# opened in a browser at http://localhost:8000 instead.
#
#   ./run.sh                 # after setup: the twin;  before setup: the setup screens in the browser
#   ./run.sh --llm stub      # extra flags go to `python -m app` (stub = no language model)
#
# Uses the same app folder as DeferralTwin.app (~/Library/Application Support/DeferralTwin),
# or DEFERRALTWIN_HOME if set.
set -euo pipefail
cd "$(dirname "$0")"
HOME_DIR="${DEFERRALTWIN_HOME:-$HOME/Library/Application Support/DeferralTwin}"
PY="$HOME_DIR/envs/main/bin/python"

ready=0
if [ -x "$PY" ]; then
  ready=$(python3 -c "import sys; sys.path.insert(0, '.'); from bootstrap.engine import Engine; print(int(Engine(sys.argv[1]).ready()))" "$HOME_DIR" 2>/dev/null || echo 0)
fi
if [ "$ready" = "1" ]; then
  export PATH="$HOME_DIR/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 COQUI_TOS_AGREED=1 \
         PYTHONPYCACHEPREFIX="$HOME_DIR/cache/pyc" TOKENIZERS_PARALLELISM=false
  exec "$PY" -m app --home "$HOME_DIR" --port 8000 --open "$@"
else
  echo "Setup hasn't finished yet; opening the setup screens in your browser."
  exec python3 bootstrap/launcher.py --browser --home "$HOME_DIR"
fi
