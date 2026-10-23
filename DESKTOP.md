# The desktop app

`DeferralTwin.app` for Apple Silicon Macs with 16 GB (HANDOFF.md section 9). It replaces the Colab notebook and the browser app in the person's path. `./run.sh` stays as the developer entry point.

## How it fits together

Two layers, because the heavy libraries can't be bundled into one app (section 9.2):

| Layer | Where | What's in it |
|---|---|---|
| Bootstrapper | `DeferralTwin.app` (about 80 MB) | standalone Python 3.12 with pywebview, the `uv` binary, and this project's code under `Contents/Resources/twin` |
| Runtime | `~/Library/Application Support/DeferralTwin/` | `envs/main` (3.11: FastAPI, MLX, embedder, XTTS), `envs/wav2lip` and `envs/gfpgan` (3.10), `models/`, `twin/`, `cache/clips`, `logs/`, `state.json` |

On launch, `bootstrap/launcher.py` serves the setup screens (`bootstrap/ui/`) from the standard library only. When setup is done it starts `envs/main/bin/python -m app` on a free local port and points its window at it. The twin server then serves `app/static/`.

| Path | What it does |
|---|---|
| `bootstrap/manifest.json` | every download (Hugging Face repo, file patterns, mirrors) and environment step |
| `bootstrap/locks/*.lock` | pinned packages for the three environments, compiled for macOS 14 on arm64 |
| `bootstrap/engine.py` | measures sizes live, checks the machine, downloads with resume (HTTP Range), retries, checksums, mirrors; builds the environments with `uv`; runs the self-test; keeps `state.json` |
| `bootstrap/launcher.py` | the window (pywebview), the setup API, starting and watching the twin server, Remove everything |
| `app/paths.py` | the app folder layout. The twin itself is at `twin/benchmark/phase2/phase2_me/` (section 5.4) |
| `app/server.py` | the API: interview draft, voice and photo checks, build with progress, streamed answers, corrections, notes, profile, voice and video jobs, demo pre-render, settings, export, redo, delete |
| `app/media.py` | the voice and video queues (video never blocks text or voice) and the clip cache keyed by answer text |
| `app/checks.py` | voice clip and photo checks that run the moment they arrive |
| `app/benchmark.py` | section 8.4, and the setup self-test |
| `src/llm_backend.py` | Phi-3-mini 4-bit on MLX (greedy), CUDA for Colab, or the stand-in |

Nothing writes inside the `.app`. The server runs with `HF_HUB_OFFLINE=1`, so after setup it works with networking off.

## What you run on the Mac

These steps can't run in the cloud container this was built in, because they need macOS, the real downloads, or a person in front of the camera.

**1. Build the app.**
```bash
./packaging/build_app.sh          # dist/DeferralTwin.app and dist/DeferralTwin.dmg
```

**2. Check the window can use the camera and microphone** (section 9.2: "verify early"):
```bash
dist/DeferralTwin.app/Contents/MacOS/DeferralTwin --media-test
```
Allow both prompts and press the two buttons. If either fails, onboarding still works: it falls back to recording and taking the photo in Python, and always offers a file chooser.

**3. Clean-account test** (section 9.8 step 3, acceptance criterion 9). This is the only honest test of "sets itself up":
1. System Settings → Users & Groups → Add User. Make a Standard user.
2. Copy `dist/DeferralTwin.dmg` to `/Users/Shared`.
3. Log in as the new user. Open the dmg, drag the app to Applications, open it. A copy you built yourself opens normally, so to test the Gatekeeper steps in the README too, download the dmg from somewhere first (AirDrop or a browser).
4. Pass if it reaches the consent screen with no terminal, no Homebrew and no Python installed by you. Note the measured download size, the time it took, and whether a "install developer tools" prompt ever appeared (it shouldn't).
5. Then turn Wi-Fi off, quit and reopen the app, and ask a question (criterion 10).
6. Take the screenshot of System Settings → Privacy & Security with **Open Anyway** showing, and save it as `packaging/gatekeeper.png` before rebuilding the dmg.
7. Settings → Remove everything, then check `~/Library/Application Support/DeferralTwin` is gone (criterion 11). Delete the test user.

**4. Benchmark** (section 8.4), after setup:
```bash
./scripts/mac_benchmark.sh        # writes BENCHMARK.md and the app's benchmark.json
```

## Developing

```bash
./run.sh                          # after setup: the twin at http://localhost:8000; before it: setup in the browser
./run.sh --llm stub               # no language model
python3 bootstrap/engine.py       # headless setup into the app folder (needs uv on PATH)
DEFERRALTWIN_HOME=/tmp/dt ./run.sh   # a separate app folder
```

Without any models (any OS), for working on the screens:
```bash
pip install -r requirements.txt fastapi "uvicorn[standard]" python-multipart opencv-python-headless
PYTHONHASHSEED=0 python scripts/dev_server.py --port 8000     # stand-in embedder and model
```
Opening `app/static/index.html` from a plain file server gives the clearly labelled simulated preview.

## Tests

```bash
PYTHONHASHSEED=0 python tests/test_app.py             # the whole API: build, tiers, streaming, correction, note, restart, export, redo, delete
PYTHONHASHSEED=0 python tests/test_gate_and_media.py  # the person-stated boost never reaches the gate; video never blocks voice; the clip cache
python tests/test_engine.py                           # setup against a fake Hugging Face: sizes, resume, dropped connection, checksum, dead mirror
```

## Updating the locks

```bash
cd bootstrap/locks
MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile main.in --python-platform aarch64-apple-darwin --python-version 3.11 -o main.lock
MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile wav2lip.in --python-platform aarch64-apple-darwin --python-version 3.10 -o wav2lip.lock
MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile gfpgan.in --python-platform aarch64-apple-darwin --python-version 3.10 -o gfpgan.lock
```
macOS 14 is the minimum because the arm64 `faiss-cpu` wheels need it. OpenCV stays below 5, which dropped `CascadeClassifier`.
