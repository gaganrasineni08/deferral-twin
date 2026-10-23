"""
First-run setup (HANDOFF.md section 9.3). Standard library only: this runs
inside the .app's own small Python, before any environment exists.

  eng = Engine(home="~/Library/Application Support/DeferralTwin")   # %LOCALAPPDATA%\\DeferralTwin on Windows
  eng.machine_check()        # Apple Silicon/macOS (or 64-bit Windows), memory, free disk -- before any download
  eng.measure()              # real download sizes, asked of the servers, not guessed
  eng.start()                # background thread; eng.snapshot() for the UI
  eng.selftest()             # one answer, one spoken sentence, a short lip-sync render

Every finished step is written to state.json, and a download that stops keeps
its .part file and resumes with an HTTP Range request. Quitting, sleep or a
dropped connection costs only the unfinished part.

The same engine sets up the Windows version: manifest-windows.json lists what
differs there (llama.cpp instead of MLX, the *-win.lock files).
"""
import fnmatch
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import msvcrt
else:
    import fcntl

HERE = Path(__file__).resolve().parent
CODE = HERE.parent                      # src/, app/, configs/ next to bootstrap/
MANIFEST = HERE / ("manifest-windows.json" if WINDOWS else "manifest.json")
LOCKS = HERE / "locks"
UA = "DeferralTwin-setup/1.0"
HF = os.environ.get("DEFERRALTWIN_HF_ENDPOINT", "https://huggingface.co")
CHUNK = 1 << 20


class Cancelled(Exception):
    pass


class SetupError(Exception):
    """A failure with a sentence a person can act on."""


def human_bytes(n):
    if n is None:
        return "unknown size"
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{int(n)} bytes"


def _match(path, patterns):
    """Patterns without a slash match top-level files only."""
    for p in patterns:
        if "/" not in p and "/" in path:
            continue
        if fnmatch.fnmatch(path, p):
            return True
    return False


def default_home():
    if os.environ.get("DEFERRALTWIN_HOME"):
        return Path(os.environ["DEFERRALTWIN_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "DeferralTwin"
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DeferralTwin"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "DeferralTwin"


EXE = ".exe" if WINDOWS else ""


def venv_python(env):
    """A virtual environment's python: bin/python, or Scripts\\python.exe on Windows."""
    return Path(env) / ("Scripts" if WINDOWS else "bin") / f"python{EXE}"


def find_uv():
    """The app ships its own uv next to the code; a developer checkout uses the one on PATH."""
    for c in (CODE.parent / "bin" / f"uv{EXE}", CODE / "bin" / f"uv{EXE}"):
        if c.exists():
            return str(c)
    return shutil.which("uv")


# subprocesses of the windowless app must not each flash a console window on Windows
NO_WINDOW = 0x08000000 if WINDOWS else 0          # CREATE_NO_WINDOW


def lock_file(fh):
    """An exclusive, non-blocking lock on an open file. OSError if someone else holds it."""
    if WINDOWS:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


def windows_memory():
    """Total physical memory in bytes (GlobalMemoryStatusEx)."""
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return int(st.ullTotalPhys)


def windows_cpu():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except OSError:
        return platform.processor() or None


class Engine:
    def __init__(self, home=None, manifest=None, opener=None, uv=None, machine=None):
        self.home = Path(home).expanduser() if home else default_home()
        self.manifest = json.loads(Path(manifest or MANIFEST).read_text(encoding="utf-8"))
        self.state_path = self.home / "state.json"
        self.log_path = self.home / "logs" / "setup.log"
        self._open = opener or self._urlopen
        self.uv = uv or find_uv()
        self._machine = machine                  # tests inject a fake machine
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.state = self._load_state()
        self.live = {"phase": "idle", "current": None, "error": None, "error_step": None,
                     "speed": 0.0, "row_bytes": {}, "started": None, "resuming": []}
        self._samples = []

    # ---------- state ----------
    def _load_state(self):
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
            if s.get("version") == 1:
                return s
        except (OSError, ValueError):
            pass
        return {"version": 1, "steps": {}, "resolved": {}, "selftest": None, "completed": None}

    def _save(self):
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
            os.replace(tmp, self.state_path)

    def log(self, msg):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg.rstrip() + "\n")

    def done(self, step_id):
        return bool(self.state["steps"].get(step_id, {}).get("done"))

    def _mark(self, step_id, **kw):
        with self._lock:
            self.state["steps"].setdefault(step_id, {}).update(kw)
            self._save()

    # ---------- network ----------
    @staticmethod
    def _urlopen(req, timeout=60):
        return urllib.request.urlopen(req, timeout=timeout)

    def _json(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with self._open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    # ---------- machine check ----------
    def machine(self):
        if self._machine:
            return dict(self._machine)
        m = {"system": platform.system(), "arch": platform.machine(), "macos": platform.mac_ver()[0] or None,
             "memory_bytes": None, "free_bytes": None, "chip": None}
        if WINDOWS:
            v = sys.getwindowsversion()
            m["windows_build"] = v.build
            m["windows"] = f"Windows {'11' if v.build >= 22000 else v.major} (build {v.build})"
            m["chip"] = windows_cpu()
            try:
                m["memory_bytes"] = windows_memory()
            except (OSError, AttributeError, ValueError):
                pass
        if sys.platform == "darwin":
            for key, field, conv in (("hw.memsize", "memory_bytes", int), ("machdep.cpu.brand_string", "chip", str)):
                try:
                    m[field] = conv(subprocess.run(["sysctl", "-n", key], capture_output=True, text=True,
                                                   timeout=5).stdout.strip())
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
        elif not WINDOWS:
            try:
                m["memory_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            except (ValueError, OSError, AttributeError):
                pass
        probe = self.home
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        m["free_bytes"] = shutil.disk_usage(probe).free
        return m

    def machine_check(self, need_bytes=None):
        m = self.machine()
        M = self.manifest
        checks = []

        def add(cid, label, ok, detail):
            checks.append({"id": cid, "label": label, "ok": bool(ok), "detail": detail})

        if M.get("platform") == "windows":
            win = m["system"] == "Windows"
            x64 = (m["arch"] or "").upper() in ("AMD64", "X86_64")
            add("windows", "64-bit Windows", win and x64,
                f"{m.get('windows') or 'Windows'}, {m.get('chip') or m['arch']}" if win and x64 else
                ("This PC runs Windows on an ARM processor. The twin needs an Intel or AMD (x64) processor."
                 if win else f"This is {m['system']} on {m['arch']}. This version of the app runs on Windows only."))
            build = m.get("windows_build") or 0
            add("winver", "Windows version", win and build >= M["min_windows_build"],
                m.get("windows") if win and build >= M["min_windows_build"] else
                "Windows 10 (version 1809) or Windows 11 is needed" + (f"; this PC has build {build}." if win else "."))
        else:
            self._mac_checks(m, add)
        self._common_checks(m, add, need_bytes)
        return {"ok": all(c["ok"] for c in checks), "checks": checks, "machine": m}

    def _mac_checks(self, m, add):
        M = self.manifest
        mac = m["system"] == "Darwin"
        add("apple_silicon", "Apple Silicon", mac and m["arch"] == "arm64",
            f"{m.get('chip') or m['arch']}" if mac and m["arch"] == "arm64" else
            ("This Mac has an Intel processor. The twin needs an M-series chip (M1 or later)."
             if mac else f"This is {m['system']} on {m['arch']}. The app runs on Macs with Apple Silicon only."))
        want = tuple(int(x) for x in M["min_macos"].split("."))
        have = tuple(int(x) for x in (m.get("macos") or "0").split(".")[:2])
        add("macos", "macOS version", mac and have >= want,
            f"macOS {m.get('macos')}" if mac and have >= want else
            f"macOS {M['min_macos']} (Sonoma) or later is needed" + (f"; this Mac has {m.get('macos')}." if mac else "."))

    def _common_checks(self, m, add, need_bytes):
        M = self.manifest
        gb = (m.get("memory_bytes") or 0) / 2 ** 30
        add("memory", "Memory", gb >= M["min_memory_gb"] * 0.95,
            f"{gb:.0f} GB" if gb >= M["min_memory_gb"] * 0.95 else
            f"{gb:.0f} GB. The language, voice and lip-sync models need {M['min_memory_gb']} GB.")
        need = need_bytes if need_bytes is not None else self.bytes_needed()
        free = m.get("free_bytes") or 0
        if need is None:
            add("disk", "Free disk space", True, f"{human_bytes(free)} free (sizes not measured yet)")
        else:
            total = need + M["disk_margin_bytes"]
            add("disk", "Free disk space", free >= total,
                f"{human_bytes(free)} free, {human_bytes(total)} needed including working space" if free >= total else
                f"Only {human_bytes(free)} free. Setup needs {human_bytes(total)}, including working space. "
                f"Free up {human_bytes(total - free)} and check again.")

    # ---------- plan ----------
    def rows(self):
        return self.manifest["rows"]

    def _items(self, row):
        """Download items of a row, as step ids."""
        return [(f"{row['id']}:{i}", it) for i, it in enumerate(row.get("items", []))]

    def steps(self, only=None):
        out = []
        for row in self.rows():
            if only and row["id"] not in only and not (row["id"] == "envs" and only):
                continue
            for sid, it in self._items(row):
                out.append(("download", row["id"], sid, it))
            for st in row.get("steps", []):
                out.append(("env", row["id"], st["id"], st))
        return out

    def measure(self, force=False):
        """Ask the servers how big every file is (HF tree API, or a HEAD request)."""
        errors = []
        for row in self.rows():
            for sid, it in self._items(row):
                if sid in self.state["resolved"] and not force:
                    continue
                try:
                    self.state["resolved"][sid] = self._resolve(it)
                except Exception as e:
                    if it.get("optional"):
                        self.state["resolved"][sid] = {"files": [], "skipped": f"{type(e).__name__}: {e}"}
                    else:
                        errors.append(f"{row['label']}: {e}")
        self._save()
        return {"ok": not errors, "errors": errors, "download_bytes": self.download_bytes(),
                "env_bytes": self.env_bytes(), "needed_bytes": self.bytes_needed()}

    def _resolve(self, it):
        if "hf" in it:
            repo = it["hf"]
            rev = it.get("revision") or "main"
            info = self._json(f"{HF}/api/models/{repo}/revision/{urllib.parse.quote(rev, safe='')}")
            sha = info.get("sha") or rev
            tree = self._json(f"{HF}/api/models/{repo}/tree/{sha}?recursive=true")
            files = []
            names = {e["path"] for e in tree if e.get("type") == "file"}
            pick = next((n for n in it.get("one_of", []) if n in names), None)
            for e in tree:
                if e.get("type") != "file":
                    continue
                if not (_match(e["path"], it.get("include", ["*"])) or e["path"] == pick):
                    continue
                lfs = e.get("lfs") or {}
                files.append({"path": e["path"], "size": int(lfs.get("size") or e.get("size") or 0),
                              "sha256": lfs.get("oid"),
                              "urls": [f"{HF}/{repo}/resolve/{sha}/{urllib.parse.quote(e['path'])}"]})
            if not files:
                raise SetupError(f"no files matched in {repo}")
            return {"repo": repo, "revision": sha, "files": files}
        size, last = None, None
        for u in it["url"]:
            try:
                size = self._head_size(u)
                break
            except Exception as e:
                last = e
        if size is None and last is not None:
            raise SetupError(f"none of the download addresses answered ({last})")
        return {"files": [{"path": Path(it["dest"]).name, "size": size, "sha256": it.get("sha256"),
                           "urls": it["url"], "single": True}]}

    def _head_size(self, url):
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with self._open(req, timeout=30) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None

    def _files(self, sid, it):
        res = self.state["resolved"].get(sid)
        if res is None:
            res = self.state["resolved"][sid] = self._resolve(it)
            self._save()
        dest = self.home / it["dest"]
        for f in res["files"]:
            yield f, (dest if f.get("single") else dest / f["path"])

    def download_bytes(self):
        total = 0
        for row in self.rows():
            for sid, _ in self._items(row):
                res = self.state["resolved"].get(sid)
                if res is None:
                    return None
                total += sum(f["size"] or 0 for f in res["files"])
        return total

    def env_bytes(self):
        return sum(st.get("expected_bytes", 0) for r in self.rows() for st in r.get("steps", []))

    def bytes_needed(self):
        d = self.download_bytes()
        if d is None:
            return None
        have = sum(self._row_done_bytes(r) for r in self.rows())
        return max(0, d + self.env_bytes() - have)

    # ---------- progress ----------
    def _row_total(self, row):
        if row.get("steps"):
            return sum(st.get("expected_bytes", 0) for st in row["steps"])
        total = 0
        for sid, _ in self._items(row):
            res = self.state["resolved"].get(sid)
            if res is None:
                return None
            total += sum(f["size"] or 0 for f in res["files"])
        return total

    def _row_done_bytes(self, row):
        if row.get("steps"):
            done = 0
            for st in row["steps"]:
                if self.done(st["id"]):
                    done += st.get("expected_bytes", 0)
                elif self.live["current"] == st["id"]:
                    done += min(self.live["row_bytes"].get(st["id"], 0), st.get("expected_bytes", 0))
            return done
        done = 0
        for sid, it in self._items(row):
            res = self.state["resolved"].get(sid)
            if not res:
                continue
            for f, path in self._files(sid, it):
                if path.exists():
                    done += f["size"] or path.stat().st_size
                else:
                    part = path.with_name(path.name + ".part")
                    if part.exists():
                        done += part.stat().st_size
        return done

    def _row_state(self, row):
        sids = [sid for sid, _ in self._items(row)] + [st["id"] for st in row.get("steps", [])]
        if all(self.done(s) or self._optional_skipped(s) for s in sids):
            return "done"
        if self.live["error_step"] in sids:
            return "error"
        if self.live["current"] in sids:
            return "active"
        return "waiting"

    def snapshot(self):
        rows, total, done = [], 0, 0
        for row in self.rows():
            t, d = self._row_total(row), self._row_done_bytes(row)
            rows.append({"id": row["id"], "label": row["label"], "detail": row.get("detail"),
                         "total_bytes": t, "done_bytes": d, "state": self._row_state(row),
                         "step": next((st["label"] for st in row.get("steps", [])
                                       if st["id"] == self.live["current"]), None)})
            total += t or 0
            done += d
        speed = self.live["speed"]
        eta = (total - done) / speed if speed > 1000 and total > done else None
        return {"phase": self.live["phase"], "rows": rows, "total_bytes": total, "done_bytes": done,
                "speed": speed, "eta_s": eta, "error": self.live["error"], "error_step": self.live["error_step"],
                "resuming": self.live["resuming"], "complete": self.complete(), "selftest": self.state["selftest"],
                "ready": self.ready(), "measured": self.download_bytes() is not None}

    def _tick(self, n):
        now = time.time()
        self._samples.append((now, n))
        while self._samples and now - self._samples[0][0] > 8:
            self._samples.pop(0)
        span = now - self._samples[0][0] if len(self._samples) > 1 else 0
        if span > 0.5:
            self.live["speed"] = sum(b for _, b in self._samples) / span

    # ---------- run ----------
    def complete(self, only=None):
        return all(self.done(sid) or (kind == "download" and it.get("optional") and self._optional_skipped(sid))
                   for kind, _, sid, it in self.steps(only))

    def _optional_skipped(self, sid):
        return bool(self.state["steps"].get(sid, {}).get("skipped"))

    def ready(self):
        st = self.state.get("selftest") or {}
        return self.complete() and bool(st.get("llm", {}).get("ok"))

    def resuming(self):
        out = []
        for row in self.rows():
            d, t = self._row_done_bytes(row), self._row_total(row)
            if d and self._row_state(row) != "done":
                out.append({"label": row["label"], "done_bytes": d, "total_bytes": t})
        return out

    def start(self, only=None):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self.live.update(phase="running", error=None, error_step=None, started=time.time(),
                             resuming=self.resuming())
            self._thread = threading.Thread(target=self._run, args=(only,), daemon=True, name="setup")
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def wait(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)
        return self.live["phase"]

    def _run(self, only):
        lock = open(self.home / "setup.lock", "a")
        try:
            try:
                # Two setups on one folder would append to the same .part files and corrupt them.
                lock_file(lock)
            except OSError:
                raise SetupError("Setup is already running in another window or terminal. Close it, then press Retry.")
            for kind, row_id, sid, it in self.steps(only):
                if self._stop.is_set():
                    raise Cancelled()
                if self.done(sid):
                    continue
                self.live["current"] = sid
                self.log(f"start {sid}")
                try:
                    if kind == "download":
                        self._download_item(sid, it)
                    else:
                        self._env_step(it)
                except Exception as e:
                    if kind == "download" and it.get("optional") and not isinstance(e, Cancelled):
                        self.log(f"optional {sid} skipped: {e}")
                        self._mark(sid, skipped=str(e)[:300])
                        continue
                    raise
                self._mark(sid, done=True, at=time.time())
                self.log(f"done {sid}")
            self.live.update(current=None, phase="done")
            if self.complete():
                self.state["completed"] = time.time()
                self._save()
        except Cancelled:
            self.live.update(phase="paused", current=None)
            self.log("paused")
        except Exception as e:
            msg = str(e) if isinstance(e, SetupError) else f"{type(e).__name__}: {e}"
            self.log(f"error in {self.live['current']}: {msg}")
            self.live.update(phase="error", error=msg[:600], error_step=self.live["current"], current=None)
        finally:
            lock.close()

    # ---------- downloads ----------
    def _download_item(self, sid, it):
        if self.state["resolved"].get(sid, {}).get("skipped"):     # unreachable when measured: try once more
            self.state["resolved"][sid] = self._resolve(it)
            self._save()
        for f, path in self._files(sid, it):
            if not (path.exists() and (not f["size"] or path.stat().st_size == f["size"])):
                self._fetch(f["urls"], path, f["size"], f.get("sha256"), it.get("min_bytes"))
            if it.get("extract"):
                self._extract(path, self.home / it["extract"], it.get("check"))

    def _extract(self, zpath, dest, check=None):
        """Unpack a downloaded zip (llama.cpp on Windows). The zip is kept, so the row's bytes still add up."""
        if check and (dest / check).exists():
            return
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(dest)
        except zipfile.BadZipFile:
            zpath.unlink()
            raise SetupError(f"{zpath.name} was damaged and has been discarded. Press Retry.")
        if check and not (dest / check).exists():
            raise SetupError(f"{zpath.name} doesn't contain {check}. Press Retry.")

    def _fetch(self, urls, dest, size=None, sha256=None, min_bytes=None, attempts=6):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        last = None
        for url in urls:
            attempt = 0
            while attempt < attempts:
                if self._stop.is_set():
                    raise Cancelled()
                before = part.stat().st_size if part.exists() else 0
                try:
                    self._fetch_once(url, part, size)
                    last = None
                    break
                except Cancelled:
                    raise
                except urllib.error.HTTPError as e:
                    last = e
                    if e.code in (404, 403, 401, 410):
                        break                               # this address is gone; try the next one
                except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
                    last = e
                if part.exists() and part.stat().st_size > before:
                    attempt = 0                             # it was moving: a flaky line, not a dead one
                wait = min(30, 2 ** (attempt + 1))
                attempt += 1
                self.log(f"retry {url} in {wait}s ({last})")
                if self._stop.wait(wait):
                    raise Cancelled()
            if last is None:
                break
        if last is not None:
            raise SetupError(f"Couldn't download {dest.name}: {last}. Check the internet connection and press Retry; "
                             "what has arrived so far is kept.")
        got = part.stat().st_size
        if size and got != size:
            part.unlink()
            raise SetupError(f"{dest.name} arrived incomplete ({human_bytes(got)} of {human_bytes(size)}). Press Retry.")
        if min_bytes and got < min_bytes:
            part.unlink()
            raise SetupError(f"{dest.name} is too small to be the real file ({human_bytes(got)}). Press Retry.")
        if sha256:
            h = hashlib.sha256()
            with open(part, "rb") as fh:
                for chunk in iter(lambda: fh.read(CHUNK), b""):
                    h.update(chunk)
            if h.hexdigest() != sha256:
                part.unlink()
                raise SetupError(f"{dest.name} failed its checksum and was discarded. Press Retry.")
        os.replace(part, dest)

    def _fetch_once(self, url, part, size):
        have = part.stat().st_size if part.exists() else 0
        if size and have > size:
            part.unlink()
            have = 0
        if size and have == size:
            return
        headers = {"User-Agent": UA}
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            r = self._open(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:              # we already have all of it
                return
            raise
        with r:
            status = getattr(r, "status", 200)
            mode = "ab" if have and status == 206 else "wb"
            if mode == "wb" and have:
                self.log(f"server ignored the resume request for {part.name}; starting it again")
            with open(part, mode) as fh:
                while True:
                    if self._stop.is_set():
                        raise Cancelled()
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    self._tick(len(chunk))
        expect = size or ((have if mode == "ab" else 0) + int(r.headers.get("Content-Length") or 0)) or None
        got = part.stat().st_size
        if expect and got < expect:                 # the connection ended early; keep what arrived and retry
            raise ConnectionError(f"connection dropped at {human_bytes(got)} of {human_bytes(expect)}")

    # ---------- environments ----------
    def _env(self):
        e = dict(os.environ)
        path = e.get("PATH", "/usr/bin:/bin")
        if not WINDOWS:
            shim = self.home / "cache" / "shim"
            shim.mkdir(parents=True, exist_ok=True)
            git = shim / "git"
            if not git.exists():
                # A clean Mac's /usr/bin/git only offers to install Xcode tools. Some
                # setup.py files call git for a version string; this answers "no git".
                git.write_text("#!/bin/sh\nexit 1\n")
                git.chmod(0o755)
            path = f"{shim}{os.pathsep}{path}"
        e.update(UV_CACHE_DIR=str(self.home / "cache" / "uv"), UV_PYTHON_INSTALL_DIR=str(self.home / "python"),
                 UV_PYTHON_PREFERENCE="only-managed", UV_NO_CONFIG="1",
                 UV_LINK_MODE="copy" if WINDOWS else "clone",
                 PATH=path, PYTHONNOUSERSITE="1", PYTHONUTF8="1")
        if WINDOWS:
            e["KMP_DUPLICATE_LIB_OK"] = "TRUE"      # torch's and faiss's OpenMP DLLs in one process (self-test)
        e.pop("VIRTUAL_ENV", None)
        e.pop("PYTHONPATH", None)
        return e

    def _uv(self, *args, watch=None):
        if not self.uv:
            raise SetupError("The setup tool (uv) is missing from the app. Download DeferralTwin again.")
        cmd = [self.uv, *map(str, args)]
        self.log("$ " + " ".join(cmd))
        with open(self.log_path, "a", encoding="utf-8") as logf:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT, env=self._env(),
                                 creationflags=NO_WINDOW)
            while p.poll() is None:
                if self._stop.is_set():
                    p.terminate()
                    p.wait(10)
                    raise Cancelled()
                if watch:
                    watch()
                time.sleep(0.5)
        if p.returncode != 0:
            raise SetupError(f"Preparing an environment failed (uv {args[0]} {args[1] if len(args) > 1 else ''}). "
                             f"Press Retry; details are in {self.log_path}.")

    def _watch_dir(self, step_id, *dirs):
        last = [0]

        def f():
            n = sum(_du(d) for d in dirs)
            if n > last[0]:
                self._tick(n - last[0])
            last[0] = n
            self.live["row_bytes"][step_id] = n
        return f

    def _env_step(self, st):
        sid = st["id"]
        if sid == "python":
            vers = sorted(set(self.manifest["python"].values()))
            self._uv("python", "install", *vers, watch=self._watch_dir(sid, self.home / "python"))
        elif sid.startswith("env-"):
            name = sid[4:]
            env = self.home / "envs" / name
            py = venv_python(env)
            if not self.state["steps"].get(sid, {}).get("venv") or not py.exists():
                if env.exists():
                    shutil.rmtree(env)
                self._uv("venv", "--python", self.manifest["python"][st["python"]], env)
                self._mark(sid, venv=True)
            watch = self._watch_dir(sid, env)
            self._uv("pip", "install", "--python", py, "-r", LOCKS / st["lock"], watch=watch)
            if st.get("after"):
                # basicsr's setup.py imports torch, so it builds against the env, not in isolation
                self._uv("pip", "install", "--python", py, "--no-build-isolation", "--no-deps", *st["after"], watch=watch)
        elif sid == "wav2lip-src":
            src = self.home / "envs" / "wav2lip-src"
            z = self.home / "cache" / "wav2lip-src.zip"
            if not z.exists():
                self._fetch(st["url"], z, min_bytes=10000)
            tmp = self.home / "envs" / "wav2lip-src.tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(tmp)
            inner = next((p for p in tmp.iterdir() if p.is_dir()), tmp)
            if src.exists():
                shutil.rmtree(src)
            shutil.move(str(inner), str(src))
            shutil.rmtree(tmp, ignore_errors=True)
            z.unlink()
            if not (src / "inference.py").exists():
                raise SetupError("The lip-sync code download looks wrong (no inference.py). Press Retry.")
        elif sid == "finish":
            self._finish()
        else:
            raise SetupError(f"unknown setup step {sid}")

    def _finish(self):
        share_openmp(self.home / "envs" / "main")
        # Wav2Lip loads S3FD from inside its own source tree
        src = self.home / "envs" / "wav2lip-src"
        s3fd = self.home / "models" / "wav2lip" / "s3fd.pth"
        target = src / "face_detection" / "detection" / "sfd" / "s3fd.pth"
        if src.exists() and s3fd.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            try:
                target.symlink_to(s3fd)
            except OSError:
                shutil.copy2(s3fd, target)
        # ffmpeg: the static build that ships inside the imageio-ffmpeg wheel (no Homebrew)
        py = venv_python(self.home / "envs" / "main")
        if py.exists():
            r = subprocess.run([str(py), "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
                               capture_output=True, text=True, env=self._env(), stdin=subprocess.DEVNULL,
                               creationflags=NO_WINDOW)
            exe = r.stdout.strip()
            if r.returncode != 0 or not exe:
                raise SetupError("ffmpeg could not be found in the main environment. Press Retry.")
            b = self.home / "bin"
            b.mkdir(parents=True, exist_ok=True)
            link = b / f"ffmpeg{EXE}"
            if link.exists() or link.is_symlink():
                link.unlink()
            if WINDOWS:
                shutil.copy2(exe, link)        # symlinks need admin rights or Developer Mode on Windows
            else:
                link.symlink_to(exe)
        # The downloaded wheels are no longer needed: the environments hold their own copies.
        if self.uv:
            subprocess.run([self.uv, "cache", "clean"], env=self._env(), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)

    # ---------- what is installed (used by the app to offer installs) ----------
    def installed(self):
        out = {}
        for row in self.rows():
            sids = [sid for sid, _ in self._items(row)] + [st["id"] for st in row.get("steps", [])]
            out[row["id"]] = all(self.done(s) or self._optional_skipped(s) for s in sids)
        return out

    # ---------- self-test ----------
    def selftest(self, items=("llm", "voice", "lipsync"), timeout=1800):
        py = venv_python(self.home / "envs" / "main")
        results = dict(self.state.get("selftest") or {})
        self.live["phase"] = "selftest"
        for item in items:
            results[item] = {"ok": None, "running": True}
            self.state["selftest"] = results
            env = self._env()
            env.update(PYTHONPYCACHEPREFIX=str(self.home / "cache" / "pyc"), HF_HUB_OFFLINE="1",
                       TRANSFORMERS_OFFLINE="1", COQUI_TOS_AGREED="1", TOKENIZERS_PARALLELISM="false",
                       PATH=f"{self.home / 'bin'}{os.pathsep}{env['PATH']}")
            cmd = [str(py), "-m", "app.benchmark", "--home", str(self.home), "--selftest", item, "--json"]
            self.log("$ " + " ".join(cmd))
            try:
                r = subprocess.run(cmd, cwd=str(CODE), env=env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                                   creationflags=NO_WINDOW)
                self.log(r.stdout[-3000:] + r.stderr[-3000:])
                line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("{")), None)
                res = json.loads(line) if line else {"ok": False, "error": (r.stderr or "no output")[-400:]}
            except subprocess.TimeoutExpired:
                res = {"ok": False, "error": f"took longer than {timeout // 60} minutes"}
            except OSError as e:
                res = {"ok": False, "error": f"the main environment is missing ({e})"}
            results[item] = res
            self.state["selftest"] = results
            self._save()
        self.live["phase"] = "done"
        return results


def _du(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def share_openmp(env):
    """faiss and scikit-learn wheels each bundle their own libomp next to torch's. Three OpenMP
    runtimes in the twin server make XTTS hang or segfault once faiss has searched (seen on an
    M3, macOS 26). Point the other two at torch's copy so the process loads one. Idempotent;
    returns the names it changed. macOS only: Windows wheels ship DLLs, not .dylibs."""
    if WINDOWS:
        return []
    sp = next(iter(sorted(Path(env).glob("lib/python3*/site-packages"))), None)
    torch_omp = sp / "torch" / "lib" / "libomp.dylib" if sp else None
    if not torch_omp or not torch_omp.exists():
        return []
    changed = []
    for pkg in ("faiss", "sklearn"):
        lib = sp / pkg / ".dylibs" / "libomp.dylib"
        if not (lib.exists() or lib.is_symlink()) or (lib.is_symlink() and lib.resolve() == torch_omp.resolve()):
            continue
        lib.unlink()
        lib.symlink_to(Path("..") / ".." / "torch" / "lib" / "libomp.dylib")
        changed.append(pkg)
    return changed


if __name__ == "__main__":
    # Headless setup, for developers: python3 bootstrap/engine.py [--home DIR]
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--home")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--skip-selftest", action="store_true")
    a = ap.parse_args()
    eng = Engine(a.home)
    print("Measuring download sizes...")
    m = eng.measure()
    if not m["ok"]:
        print("\n".join(m["errors"]))
    chk = eng.machine_check()
    for c in chk["checks"]:
        print(("  ok  " if c["ok"] else "  NO  ") + f"{c['label']}: {c['detail']}")
    if not chk["ok"] or a.check_only:
        sys.exit(0 if chk["ok"] else 1)
    eng.start()
    while eng.wait(2) == "running":
        s = eng.snapshot()
        print(f"\r  {human_bytes(s['done_bytes'])} of {human_bytes(s['total_bytes'])}   "
              f"{human_bytes(s['speed'])}/s   ", end="", flush=True)
    print()
    if eng.live["phase"] != "done":
        print("Setup stopped:", eng.live["error"])
        sys.exit(1)
    if not a.skip_selftest:
        for k, v in eng.selftest().items():
            print(f"  self-test {k}: {'ok' if v.get('ok') else 'FAILED ' + str(v.get('error'))}")
