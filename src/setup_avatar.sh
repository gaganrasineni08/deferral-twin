#!/bin/bash
# Colab: bash src/setup_avatar.sh   (about 10-15 min; creates a separate Python 3.10 env)
set -e
pip -q install uv
uv venv /content/sadenv --python 3.10
PY=/content/sadenv/bin/python
cd /content
[ -d SadTalker ] || git clone -q https://github.com/OpenTalker/SadTalker.git

uv pip install --python $PY setuptools==69.5.1 wheel
uv pip install --python $PY -r "$(dirname "$0")/../requirements-avatar.txt"
# basicsr 1.4.2 needs torch visible at build time
uv pip install --python $PY --no-build-isolation basicsr==1.4.2
uv pip install --python $PY gfpgan

cd SadTalker && bash scripts/download_models.sh
echo "SadTalker setup complete."
