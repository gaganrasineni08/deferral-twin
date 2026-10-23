"""
python -m app                      # the twin server, http://127.0.0.1:8000
python -m app --llm stub           # try the interface without a language model
python -m app --home DIR           # use another app folder (default: ~/Library/Application Support/DeferralTwin)

Normally started by the bootstrapper (bootstrap/launcher.py) inside DeferralTwin.app,
or by ./run.sh for developers. On Colab, --drive keeps a backup on Google Drive and
--share opens a public URL.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARED = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"


def open_tunnel(port):
    """Colab only: a free public URL via a Cloudflare quick tunnel."""
    exe = shutil.which("cloudflared") or "/tmp/cloudflared"
    if not Path(exe).exists():
        urllib.request.urlretrieve(CLOUDFLARED, exe)
        os.chmod(exe, 0o755)
    p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def watch():
        for line in p.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                print(f"\n  Public URL:  {m.group(0)}\n", flush=True)
    threading.Thread(target=watch, daemon=True).start()
    return p


def main():
    ap = argparse.ArgumentParser(prog="python -m app")
    ap.add_argument("--home", help="app folder (DEFERRALTWIN_HOME also works)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--llm", default="auto", choices=["auto", "mlx", "llamacpp", "cuda", "stub"])
    ap.add_argument("--bootstrap-url", help="set by the bootstrapper, for installs and Remove everything")
    ap.add_argument("--open", action="store_true", help="open the browser once the server is up")
    ap.add_argument("--no-multimodal", action="store_true")
    ap.add_argument("--drive", help="Colab: a backup folder on Google Drive")
    ap.add_argument("--share", action="store_true", help="Colab: also open a public URL")
    a = ap.parse_args()

    from app.paths import Paths
    home = Paths(a.home).home
    home.mkdir(parents=True, exist_ok=True)
    os.chdir(home)                      # nothing is ever written next to the code
    sys.path.insert(0, str(ROOT))
    bin_dir = str(home / "bin")         # ffmpeg from setup
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if sys.platform == "win32":
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")   # torch's and faiss's OpenMP DLLs in one process
    try:                                # one OpenMP runtime, before faiss or torch load (voice hangs otherwise)
        from bootstrap.engine import share_openmp
        share_openmp(Path(sys.prefix))
    except Exception as e:
        print(f"(OpenMP not shared: {type(e).__name__}: {e})", flush=True)
    import uvicorn
    from app.server import boot, create_app

    if a.bootstrap_url:
        # Started by DeferralTwin.app. Cmd+Q ends the app without its cleanup running, which
        # left this server (about 3 GB) running on its own. Leave when the app is gone.
        parent = os.getppid()

        def watch_parent():
            if sys.platform == "win32":            # Windows never re-parents: wait on the parent's handle
                import ctypes
                k32 = ctypes.WinDLL("kernel32")
                k32.OpenProcess.restype = ctypes.c_void_p
                h = k32.OpenProcess(0x00100000, False, parent)          # SYNCHRONIZE
                if h:
                    k32.WaitForSingleObject(ctypes.c_void_p(h), 0xFFFFFFFF)
                    os._exit(0)
                return
            while os.getppid() == parent:
                time.sleep(2)
            os._exit(0)
        threading.Thread(target=watch_parent, daemon=True, name="parent-watch").start()

    state = boot(home, llm=a.llm, multimodal=not a.no_multimodal, bootstrap_url=a.bootstrap_url, drive=a.drive)
    if a.share:
        open_tunnel(a.port)
    url = f"http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}"
    print(f"Twin server: {url}   (app folder {home})", flush=True)
    if a.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(state), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
