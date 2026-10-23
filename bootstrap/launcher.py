"""
DeferralTwin.app's (and DeferralTwin.exe's) entry point -- the bootstrapper (HANDOFF.md section 9.2).

Runs on the small Python inside the .app with nothing but the standard
library and pywebview, so it can show the setup screens before any
environment exists. Then it starts the twin server from envs/main and points
its window at it.

  python3 bootstrap/launcher.py              # a native window (pywebview: WebKit on the Mac, WebView2 on Windows)
  python3 bootstrap/launcher.py --browser    # the same screens in a browser; ./run.sh uses this
  python3 bootstrap/launcher.py --media-test # does the window allow camera and microphone here?

The server exits with code 87 after "Remove everything": the bootstrapper then
deletes the whole app folder and shows the last screen.
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from bootstrap.engine import CODE, NO_WINDOW, WINDOWS, Engine, default_home, human_bytes, venv_python  # noqa: E402

UI = HERE / "ui"
EXIT_REMOVE_EVERYTHING = 87
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


def free_port(prefer=None):
    for port in ([prefer] if prefer else []) + [0]:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("no free port")


class Launcher:
    def __init__(self, home=None, mode="window", server_port=None, llm="auto"):
        self.home = Path(home).expanduser() if home else default_home()
        self.engine = Engine(self.home)
        self.mode, self.llm = mode, llm
        self.server_port = server_port
        self.server = None
        self.launch = {"state": "idle", "url": None, "error": None}
        self.check = None
        self.goodbye = None
        self.window = None
        self.url = None

    # ---- the twin server ----
    def server_env(self):
        e = dict(os.environ)
        for k in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONDONTWRITEBYTECODE"):
            e.pop(k, None)
        e.update(PYTHONPYCACHEPREFIX=str(self.home / "cache" / "pyc"),   # never write into the .app
                 HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                 COQUI_TOS_AGREED="1", TOKENIZERS_PARALLELISM="false", PYTHONNOUSERSITE="1",
                 PATH=f"{self.home / 'bin'}{os.pathsep}{e.get('PATH', '/usr/bin:/bin')}")
        if WINDOWS:
            # torch and faiss each bring an OpenMP DLL; without this the second one aborts the process
            e.update(KMP_DUPLICATE_LIB_OK="TRUE", PYTHONUTF8="1")
        return e

    def start_server(self):
        if self.server and self.server.poll() is None:
            return self.launch
        py = venv_python(self.home / "envs" / "main")
        if not py.exists():
            self.launch = {"state": "error", "url": None, "error": "The main environment is missing. Run setup again."}
            return self.launch
        port = free_port(self.server_port)
        url = f"http://127.0.0.1:{port}"
        logs = self.home / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = open(logs / "server.log", "a", encoding="utf-8")
        log.write(f"\n---- start {time.ctime()} ----\n")
        log.flush()
        cmd = [str(py), "-m", "app", "--home", str(self.home), "--port", str(port), "--host", "127.0.0.1",
               "--bootstrap-url", self.url, "--llm", self.llm]
        self.server = subprocess.Popen(cmd, cwd=str(CODE), env=self.server_env(), stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        log.close()         # the server has its own handle; ours would keep Windows from deleting the folder
        self.launch = {"state": "starting", "url": url, "error": None, "started": time.time()}
        threading.Thread(target=self._watch_server, args=(self.server, url), daemon=True).start()
        return self.launch

    def _watch_server(self, proc, url):
        while proc.poll() is None:
            if self.launch["state"] == "starting":
                try:
                    with urllib.request.urlopen(url + "/api/status", timeout=2) as r:
                        if r.status == 200:
                            self.launch["state"] = "ready"
                except OSError:
                    pass
            time.sleep(0.3)
        code = proc.returncode
        if code == EXIT_REMOVE_EVERYTHING:
            self.remove_everything()
        elif self.launch["state"] != "stopping":
            self.launch.update(state="crashed", error=f"The twin stopped unexpectedly (code {code}).",
                               log=self.log_tail())

    def log_tail(self, n=25):
        try:
            return (self.home / "logs" / "server.log").read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        except OSError:
            return []

    def stop_server(self):
        p = self.server
        if p and p.poll() is None:
            self.launch["state"] = "stopping"
            if WINDOWS:     # no SIGTERM there: end the server and llama-server with it
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            else:
                p.send_signal(signal.SIGTERM)
            try:
                p.wait(8)
            except subprocess.TimeoutExpired:
                p.kill()

    def remove_everything(self):
        """Delete the whole app folder: models, environments, the twin, logs. Then say goodbye."""
        home = self.home
        size = 0
        for root, _, files in os.walk(home):
            for f in files:
                try:
                    size += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        shutil.rmtree(home, ignore_errors=True)
        if WINDOWS and home.exists():
            # Files in use can't be deleted on Windows: the window's own WebView2 data is, until it closes.
            # run_window deletes the rest then.
            time.sleep(1)
            shutil.rmtree(home, ignore_errors=True)
        left = [f for f in home.rglob("*") if f.is_file()] if home.exists() else []
        only_window_data = all((home / "cache" / "webview") in f.parents for f in left)
        self.goodbye = {"removed": not home.exists() or (WINDOWS and only_window_data),
                        "home": str(home), "freed": human_bytes(size)}
        self.launch = {"state": "removed", "url": None, "error": None}

    # ---- the setup screens' API ----
    def api(self, method, path, body):
        eng = self.engine
        if path == "/api/state":
            s = eng.snapshot()
            s.update(launch=self.launch, check=self.check, goodbye=self.goodbye, mode=self.mode,
                     home=str(self.home), installed=eng.installed())
            return s
        if path == "/api/check" and method == "POST":
            m = eng.measure(force=bool(body.get("force")))
            self.check = {"measure": m, "machine": eng.machine_check()}
            return self.check
        if path == "/api/start" and method == "POST":
            eng.start(body.get("only"))
            return {"ok": True}
        if path == "/api/pause" and method == "POST":
            threading.Thread(target=eng.stop, daemon=True).start()
            return {"ok": True}
        if path == "/api/selftest" and method == "POST":
            items = body.get("items") or ["llm", "voice", "lipsync"]
            threading.Thread(target=eng.selftest, args=(items,), daemon=True).start()
            return {"ok": True}
        if path == "/api/launch":
            if method == "POST":
                return self.start_server()
            return self.launch
        if path == "/api/quit" and method == "POST":
            threading.Thread(target=self.quit, daemon=True).start()
            return {"ok": True}
        return None

    def quit(self):
        time.sleep(0.3)
        self.engine.stop()
        self.stop_server()
        if self.window is not None:
            self.window.destroy()
        else:
            os._exit(0)

    # ---- serve ----
    def serve(self):
        launcher = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                b = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b)

            def _handle(self, method):
                path = self.path.split("?", 1)[0]
                if path.startswith("/api/"):
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                        out = launcher.api(method, path, body)
                    except Exception as e:
                        return self._send(500, {"detail": f"{type(e).__name__}: {e}"})
                    return self._send(404, {"detail": "not found"}) if out is None else self._send(200, out)
                name = "setup.html" if path in ("/", "") else path.lstrip("/")
                f = (UI / name).resolve()
                if UI not in f.parents or not f.is_file():
                    return self._send(404, b"not found", "text/plain")
                self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{srv.server_address[1]}"
        return self.url


def allow_media_capture():
    """Let the window's getUserMedia reach the camera and microphone without a second,
    WebKit-level prompt (macOS still asks once, with the reasons in Info.plist).
    pywebview's delegate doesn't implement this, so add it. Best effort: if this fails,
    WebKit's own prompt still works, and onboarding always offers a file chooser and
    recording through Python."""
    try:
        from webview.platforms import cocoa
        base = cocoa.BrowserView.BrowserDelegate

        class DTBrowserDelegate(base):
            def webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_(
                    self, webview, origin, frame, media_type, decision_handler):
                host = str(origin.host()) if origin is not None else ""
                decision_handler(1 if host in ("127.0.0.1", "localhost") else 0)   # 1 = grant
        cocoa.BrowserView.BrowserDelegate = DTBrowserDelegate

        # WKWebView hides navigator.mediaDevices entirely unless this preference is on
        # (checked on macOS 26 with pywebview 6.2.1). Set it before the first page loads.
        load_url = cocoa.BrowserView.load_url

        def load_url_with_media(self, url):
            try:
                self.webview.configuration().preferences().setValue_forKey_(True, "mediaDevicesEnabled")
            except Exception as e:
                print(f"(mediaDevicesEnabled not set: {type(e).__name__}: {e})", file=sys.stderr)
            return load_url(self, url)
        cocoa.BrowserView.load_url = load_url_with_media
        return True
    except Exception as e:
        print(f"(media capture delegate not installed: {type(e).__name__}: {e})", file=sys.stderr)
        return False


def present_as_app():
    """The process is the bundled python3, so its main bundle is Python's, not DeferralTwin.app's:
    the menu says "python3" and macOS finds no camera or microphone reason to show. Give it the
    app's name and the two usage descriptions from Contents/Info.plist. Best effort."""
    try:
        import plistlib
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "Deferral Twin"
        plist = Path(__file__).resolve().parents[3] / "Info.plist"     # Contents/Resources/twin/bootstrap/
        if plist.exists():
            app_info = plistlib.loads(plist.read_bytes())
            for key in ("NSCameraUsageDescription", "NSMicrophoneUsageDescription"):
                if key in app_info:
                    info[key] = app_info[key]
    except Exception as e:
        print(f"(app name and usage descriptions not set: {type(e).__name__}: {e})", file=sys.stderr)


def run_window(launcher, start_url):
    if sys.platform == "darwin":
        present_as_app()
    import webview
    if sys.platform == "darwin":
        allow_media_capture()
    w = webview.create_window("Deferral Twin", start_url, width=1240, height=860, min_size=(960, 680),
                              background_color="#F4F1EA", text_select=True)
    launcher.window = w
    storage = launcher.home / "cache" / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        # Edge WebView2 (built into Windows 11 and current Windows 10). Without it, say so and use the browser.
        try:
            webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage))
        except Exception as e:
            print(f"(no WebView2 window: {type(e).__name__}: {e}; using the browser)", file=sys.stderr)
            raise ImportError("WebView2 is unavailable") from e
    else:
        webview.start(private_mode=False, storage_path=str(storage))
    launcher.engine.stop()
    launcher.stop_server()
    if launcher.goodbye and launcher.home.exists():
        shutil.rmtree(launcher.home, ignore_errors=True)     # what Windows kept open until the window closed


def log_to_file_when_windowless():
    """Under pythonw.exe (DeferralTwin.exe) there is no console: stdout and stderr are None and
    every print would fail. Send them to a log next to the app folder, never inside it, so
    Remove everything can delete the whole folder."""
    if sys.stderr is not None and sys.stdout is not None:
        return
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    f = open(base / "DeferralTwin-launcher.log", "a", encoding="utf-8", buffering=1)
    f.write(f"\n---- {time.ctime()} ----\n")
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


def main():
    log_to_file_when_windowless()
    ap = argparse.ArgumentParser()
    ap.add_argument("--home")
    ap.add_argument("--browser", action="store_true", help="use the default browser instead of a window")
    ap.add_argument("--port", type=int, help="port for the twin server (browser mode default: 8000)")
    ap.add_argument("--llm", default="auto")
    ap.add_argument("--media-test", action="store_true", help="check camera and microphone in the window")
    a, _ = ap.parse_known_args()        # Finder may add its own arguments
    mode = "browser" if a.browser else "window"
    launcher = Launcher(a.home, mode=mode, server_port=a.port or (8000 if a.browser else None), llm=a.llm)
    base = launcher.serve()
    start = base + ("/media-test.html" if a.media_test else "/setup.html")
    if mode == "window":
        try:
            run_window(launcher, start)
            return
        except ImportError:
            print("pywebview is not installed here; opening the browser instead.")
            launcher.mode = "browser"
    print(f"Setup and launcher: {start}")
    webbrowser.open(start)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        launcher.engine.stop()
        launcher.stop_server()


if __name__ == "__main__":
    main()
