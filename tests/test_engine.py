"""First-run setup engine against a local fake of Hugging Face and a fake uv:
measured sizes, a dropped connection, a resume with HTTP Range after quitting,
checksums, a dead mirror, an optional file, state.json, and the machine check.
Run from the project root:  python tests/test_engine.py   (or pytest)"""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import engine as E  # noqa: E402

BIG = os.urandom(3 * 1024 * 1024 + 123)
SMALL = b'{"model_type": "phi3"}'
W2L = os.urandom(2 * 1024 * 1024)


class Fake(BaseHTTPRequestHandler):
    calls = []
    flaky = {"cut_once": True}
    throttle = {"on": False}

    def log_message(self, *a):
        pass

    def _send_file(self, data, head=False):
        rng = self.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        Fake.calls.append((self.path, start))
        body = data[start:]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if head:
            return
        if Fake.flaky["cut_once"] and self.path.endswith("model.safetensors") and not rng:
            Fake.flaky["cut_once"] = False
            self.wfile.write(body[: len(body) // 3])          # then the connection drops
            self.wfile.flush()
            self.connection.close()
            return
        step = 64 * 1024
        for i in range(0, len(body), step):
            try:
                self.wfile.write(body[i:i + step])
            except (BrokenPipeError, ConnectionResetError):
                return
            if Fake.throttle["on"]:
                time.sleep(0.05)

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        p = self.path
        if p.startswith("/api/models/org/llm/revision/"):
            return self._json({"sha": "abc123"})
        if p.startswith("/api/models/org/llm/tree/abc123"):
            return self._json([
                {"type": "file", "path": "config.json", "size": len(SMALL)},
                {"type": "file", "path": "model.safetensors", "size": len(BIG),
                 "lfs": {"oid": hashlib.sha256(BIG).hexdigest(), "size": len(BIG)}},
                {"type": "file", "path": "README.md", "size": 10},
                {"type": "file", "path": "onnx/model.json", "size": 10},
                {"type": "directory", "path": "onnx"}])
        if p == "/org/llm/resolve/abc123/config.json":
            return self._send_file(SMALL, head)
        if p == "/org/llm/resolve/abc123/model.safetensors":
            return self._send_file(BIG, head)
        if p == "/good/w2l.pth":
            return self._send_file(W2L, head)
        self.send_response(404)
        self.end_headers()

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


FAKE_UV = r"""#!/bin/sh
# stands in for uv: "venv ... DIR" makes DIR/bin/python; everything else just succeeds
echo "fake uv $@" >&2
if [ "$1" = "venv" ]; then
  for last; do :; done
  mkdir -p "$last/bin"
  printf '#!/bin/sh\necho /bin/true\n' > "$last/bin/python"
  chmod +x "$last/bin/python"
fi
if [ "$1" = "python" ]; then mkdir -p "$UV_PYTHON_INSTALL_DIR/cpython"; fi
exit 0
"""


def manifest(base, tmp):
    m = json.loads((ROOT / "bootstrap" / "manifest.json").read_text())
    m["rows"] = [
        {"id": "llm", "label": "Language model", "required": True,
         "items": [{"hf": "org/llm", "dest": "models/llm/x", "include": ["*.json", "*.safetensors"]}]},
        {"id": "lipsync", "label": "Lip-sync model",
         "items": [{"url": [base + "/gone/w2l.pth", base + "/good/w2l.pth"], "dest": "models/wav2lip/wav2lip_gan.pth",
                    "min_bytes": 1000}]},
        {"id": "facedet", "label": "Face detector",
         "items": [{"url": [base + "/missing.onnx"], "dest": "models/face/y.onnx", "optional": True}]},
        {"id": "envs", "label": "Preparing environments",
         "steps": [{"id": "python", "label": "Python", "expected_bytes": 10},
                   {"id": "env-main", "label": "Main", "lock": "main.lock", "python": "main", "expected_bytes": 10},
                   {"id": "finish", "label": "Finish", "expected_bytes": 0}]},
    ]
    p = Path(tmp) / "manifest.json"
    p.write_text(json.dumps(m))
    return p


def fake_uv(tmp):
    p = Path(tmp) / "uv"
    p.write_text(FAKE_UV)
    p.chmod(0o755)
    return str(p)


MAC = {"system": "Darwin", "arch": "arm64", "macos": "15.2", "memory_bytes": 16 * 2**30,
       "free_bytes": 200 * 10**9, "chip": "Apple M2"}


def test_setup_end_to_end():
    srv, base = serve()
    E.HF = base
    tmp = tempfile.mkdtemp()
    home = Path(tmp) / "home"
    mf, uv = manifest(base, tmp), fake_uv(tmp)

    # sizes are measured, not guessed; patterns pick top-level json + safetensors only
    eng = E.Engine(home, manifest=mf, uv=uv, machine=MAC)
    m = eng.measure()
    assert m["ok"], m
    files = [f["path"] for f in eng.state["resolved"]["llm:0"]["files"]]
    assert sorted(files) == ["config.json", "model.safetensors"], files
    assert m["download_bytes"] == len(BIG) + len(SMALL) + len(W2L)

    # machine check passes on a 16 GB Apple Silicon Mac, and fails cleanly elsewhere
    assert eng.machine_check()["ok"]
    for bad, cid in (({"arch": "x86_64"}, "apple_silicon"), ({"memory_bytes": 8 * 2**30}, "memory"),
                     ({"macos": "13.6"}, "macos"), ({"free_bytes": 10**6}, "disk")):
        chk = E.Engine(home, manifest=mf, uv=uv, machine={**MAC, **bad}).machine_check()
        failed = [c for c in chk["checks"] if not c["ok"]]
        assert not chk["ok"] and [c["id"] for c in failed] == [cid], failed
        assert failed[0]["detail"]

    # quit half-way through the big file: the partial file stays, nothing is marked done
    Fake.flaky["cut_once"] = False
    Fake.throttle["on"] = True
    eng.start()
    part = home / "models/llm/x/model.safetensors.part"
    t0 = time.time()
    while not (part.exists() and part.stat().st_size > 500_000) and time.time() - t0 < 20:
        time.sleep(0.05)
    eng.stop()
    assert eng.live["phase"] == "paused"
    have = part.stat().st_size
    assert 0 < have < len(BIG) and not eng.done("llm:0")
    assert any(r["label"] == "Language model" for r in E.Engine(home, manifest=mf, uv=uv, machine=MAC).resuming())

    # a fresh launch resumes with a Range request; then a dropped connection is retried
    Fake.throttle["on"] = False
    Fake.calls.clear()
    eng2 = E.Engine(home, manifest=mf, uv=uv, machine=MAC)
    eng2.start()
    assert eng2.wait(60) == "done", eng2.live
    starts = [s for path, s in Fake.calls if path.endswith("model.safetensors")]
    assert starts and starts[0] == have, (starts, have)
    out = home / "models/llm/x/model.safetensors"
    assert out.read_bytes() == BIG and not part.exists()
    assert (home / "models/wav2lip/wav2lip_gan.pth").read_bytes() == W2L          # the dead mirror was skipped
    st = json.loads((home / "state.json").read_text())
    assert st["steps"]["llm:0"]["done"] and st["steps"]["facedet:0"]["skipped"]     # optional and missing: skipped
    assert (home / "envs/main/bin/python").exists() and (home / "bin/ffmpeg").is_symlink()
    assert eng2.complete() and not eng2.ready()                                     # the self-test hasn't run
    snap = eng2.snapshot()
    assert snap["done_bytes"] == snap["total_bytes"] and all(r["state"] == "done" for r in snap["rows"])

    # a corrupted download is caught by its checksum, and nothing else is downloaded again
    out.write_bytes(b"x" * len(BIG))
    eng3 = E.Engine(home, manifest=mf, uv=uv, machine=MAC)
    eng3.state["steps"]["llm:0"]["done"] = False
    out.unlink()
    (home / "models/llm/x/model.safetensors.part").write_bytes(b"y" * len(BIG))
    eng3.start()
    assert eng3.wait(30) == "error" and "checksum" in eng3.live["error"]
    Fake.calls.clear()
    eng3.start()
    assert eng3.wait(30) == "done", eng3.live
    assert out.read_bytes() == BIG
    assert all(p.endswith("model.safetensors") for p, _ in Fake.calls), Fake.calls
    srv.shutdown()


def test_dropped_connection_is_retried():
    srv, base = serve()
    E.HF = base
    tmp = tempfile.mkdtemp()
    home = Path(tmp) / "home"
    Fake.flaky["cut_once"] = True
    Fake.throttle["on"] = False
    eng = E.Engine(home, manifest=manifest(base, tmp), uv=fake_uv(tmp), machine=MAC)
    eng.measure()
    eng.start()
    assert eng.wait(60) == "done", eng.live
    assert (home / "models/llm/x/model.safetensors").read_bytes() == BIG
    assert "retry" in (home / "logs/setup.log").read_text()
    srv.shutdown()


if __name__ == "__main__":
    test_setup_end_to_end()
    test_dropped_connection_is_retried()
    print("setup engine checks passed")
