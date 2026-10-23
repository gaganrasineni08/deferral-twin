"""Run the app API with a stand-in embedder and the stub model, for working on the
screens without any downloaded model.  python scripts/dev_server.py [--home DIR] [--port 8000]"""
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import fake_st  # noqa: E402

fake_st.install()
import uvicorn  # noqa: E402
from app import server  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--home", default=None)
ap.add_argument("--port", type=int, default=8000)
a = ap.parse_args()
home = Path(a.home) if a.home else Path(tempfile.mkdtemp(prefix="twin-dev-"))
print("home:", home)
uvicorn.run(server.create_app(server.boot(home, llm="stub", multimodal=False)), host="127.0.0.1", port=a.port, log_level="warning")
