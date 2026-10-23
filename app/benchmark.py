"""
Measure this Mac (HANDOFF.md section 8.4), and the end-of-setup self-test (9.3, item 7).

  python -m app.benchmark --full --md BENCHMARK.md   # the section 8.4 benchmark
  python -m app.benchmark --selftest llm --json      # one check, as setup runs it

Full benchmark: one LLM answer cold and warm, one XTTS utterance of about ten
seconds, Wav2Lip on a ten-second clip with and without GFPGAN, and the peak
memory of each. Every item runs in its own process so its peak is its own;
memory is the whole process tree sampled four times a second, plus MLX's own
count of Metal memory, which a resident-size figure can miss.

The results go to benchmark.json in the app's folder. The app takes its time
estimates ("about N minutes") from there, so they are this Mac's numbers. The
self-test writes the same fields from shorter runs until a full benchmark
replaces them.

Uses the person's own voice clip and photo when the twin exists. Before that
(during setup) it uses a sentence from macOS's built-in `say` and a drawn test
face with a fixed face box, which exercises the same models at the same speed.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE / "src"))
sys.path.insert(0, str(CODE))

from app.paths import Paths  # noqa: E402

TEN_SECONDS = ("Thanks for asking. I usually spend my evenings coding on my project, and on weekends "
               "I play badminton with friends or cook something new, which helps me relax after a long week.")
ONE_SENTENCE = "This is a short test of the voice model."
TEST_Q = "What do you usually do to relax after a long week?"
TEST_SYSTEM = ("You are a digital twin of a student. Answer using only the context below, in their tone, "
               "in one or two sentences.\n\nContext:\nQ: What do you do to relax?\n"
               "A: Long walks with music, or cooking something new.")


# ---------- shared ----------
def env_for(p):
    e = dict(os.environ)
    e["PATH"] = f"{p.bin}{os.pathsep}{e.get('PATH', '/usr/bin:/bin')}"
    e.setdefault("COQUI_TOS_AGREED", "1")
    return e


def mm_kwargs(p, out_dir, box=None):
    return dict(xtts_dir=str(p.xtts), mt_dir=str(p.translator), tts_device="cpu",
                wav2lip_dir=str(p.wav2lip_src), wav2lip_python=str(p.py("wav2lip")),
                wav2lip_checkpoint=str(p.wav2lip_ckpt), gfpgan_python=str(p.py("gfpgan")),
                gfpgan_home=str(p.gfpgan_home), out_dir=str(out_dir), sharpen=False,
                env=env_for(p), face_box=box)


def ffmpeg(p):
    return str(p.ffmpeg) if p.ffmpeg.exists() else (shutil.which("ffmpeg") or "ffmpeg")


TEST_SENTENCE = ("Hello. This recording is used only to test the voice model during setup. It is about ten "
                 "seconds long, which is enough for the test to run. Nothing here is kept.")


def reference_voice(p, tmp):
    """The person's clip if there is one, else a sentence from macOS's `say` (Windows: its built-in voice)."""
    if p.ref_wav.exists():
        return str(p.ref_wav), "your voice clip"
    out = Path(tmp) / "say_ref.wav"
    if sys.platform == "win32":
        raw = Path(tmp) / "sapi.wav"
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SetOutputToWaveFile('{str(raw).replace(chr(39), chr(39) * 2)}'); $s.Speak('{TEST_SENTENCE}'); "
              "$s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True,
                       stdin=subprocess.DEVNULL, capture_output=True, creationflags=0x08000000)
        subprocess.run([ffmpeg(p), "-nostdin", "-v", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", "22050",
                        str(out)], check=True, creationflags=0x08000000)
        return str(out), "Windows test voice"
    if shutil.which("say"):
        aiff = Path(tmp) / "say.aiff"
        subprocess.run(["say", "-o", str(aiff), TEST_SENTENCE], check=True, stdin=subprocess.DEVNULL)
        subprocess.run([ffmpeg(p), "-nostdin", "-v", "error", "-y", "-i", str(aiff), "-ac", "1", "-ar", "22050",
                        str(out)], check=True)
        return str(out), "macOS test voice"
    raise RuntimeError("no voice clip yet and no `say` command to make a test one")


def test_face(tmp):
    """A drawn face, with the box Wav2Lip should use (top, bottom, left, right)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (512, 512), (214, 222, 230))
    d = ImageDraw.Draw(im)
    d.ellipse((146, 96, 366, 396), fill=(224, 182, 150))
    for x in (206, 306):
        d.ellipse((x - 18, 206, x + 18, 226), fill=(255, 255, 255))
        d.ellipse((x - 8, 208, x + 8, 224), fill=(60, 40, 30))
    d.polygon([(256, 236), (240, 290), (272, 290)], fill=(205, 160, 130))
    d.line((216, 330, 296, 330), fill=(150, 70, 70), width=6)
    path = Path(tmp) / "test_face.jpg"
    im.save(path, quality=95)
    return str(path), (96, 396, 146, 366)


def wav_seconds(path):
    import wave
    with wave.open(str(path)) as w:
        return w.getnframes() / float(w.getframerate())


def fit_audio(p, src, seconds, out):
    """Trim or loop a clip to `seconds` so every lip-sync run is the same length."""
    subprocess.run([ffmpeg(p), "-nostdin", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(src),
                    "-t", str(seconds), "-ac", "1", "-ar", "16000", str(out)], check=True)
    return str(out)


# ---------- items (each runs in its own process) ----------
def item_llm(p, short):
    sys.path.insert(0, str(CODE / "src"))
    import llm_backend
    t0 = time.time()
    llm = llm_backend.load("auto", model=str(p.llm_model) if p.llm_model.exists() else None,
                           server=str(p.llama_server) if p.llama_server.exists() else None,
                           log=str(p.logs / "llama-server.log"))
    load_s = time.time() - t0
    t1 = time.time()
    a1 = llm.fn(TEST_SYSTEM, TEST_Q, [])
    first_s = time.time() - t1
    out = {"name": llm.name, "load_s": round(load_s, 2), "first_answer_s": round(first_s, 2),
           "cold_s": round(load_s + first_s, 2), "answer": a1[:200], "stand_in": llm.stand_in}
    if not short:
        t2 = time.time()
        llm.fn(TEST_SYSTEM, "What is your favourite way to spend a Sunday?", [])
        out["warm_s"] = round(time.time() - t2, 2)
    out["peak_metal_bytes"] = llm_backend.mlx_peak_memory()
    if llm.stand_in:
        raise RuntimeError("no language model could be loaded (only the stand-in is available)")
    if not a1.strip():
        raise RuntimeError("the language model returned an empty answer")
    return out


def item_voice(p, short, tmp):
    from multimodal import MultiModal
    ref, ref_kind = reference_voice(p, tmp)
    mm = MultiModal(ref_wav=ref, photo=None, **mm_kwargs(p, tmp))
    t0 = time.time()
    mm._load_tts()
    load_s = time.time() - t0
    text = ONE_SENTENCE if short else TEN_SECONDS
    t1 = time.time()
    wav = mm.speak(text, lang="en", out_path=str(Path(tmp) / "voice_test.wav"))
    synth_s = time.time() - t1
    secs = wav_seconds(wav)
    keep = p.cache / "tmp" / "selftest_voice.wav"
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wav, keep)
    return {"reference": ref_kind, "load_s": round(load_s, 2), "seconds": round(synth_s, 2),
            "audio_seconds": round(secs, 2), "rtf": round(synth_s / secs, 3) if secs else None,
            "overhead_s": 0, "device": "cpu"}


def item_lipsync(p, short, tmp, sharpen=False):
    from multimodal import MultiModal
    seconds = 3 if short else 10
    if p.photo.exists():
        face, box, face_kind = str(p.photo), None, "your photo"
    else:
        (face, box), face_kind = test_face(tmp), "drawn test face"
    src = p.cache / "tmp" / "selftest_voice.wav"
    if not src.exists():
        src = Path(reference_voice(p, tmp)[0])
    audio = fit_audio(p, src, seconds, Path(tmp) / "clip.wav")
    mm = MultiModal(ref_wav=None, photo=face, **mm_kwargs(p, tmp, box=box))
    t0 = time.time()
    out = mm.animate(audio, out_path=str(Path(tmp) / "clip.mp4"), sharpen=sharpen)
    took = time.time() - t0
    if not Path(out).exists() or Path(out).stat().st_size < 1000:
        raise RuntimeError("the lip-sync render produced no video")
    return {"face": face_kind, "audio_seconds": seconds, "seconds": round(took, 1),
            "per_audio_second": round(took / seconds, 2), "overhead_s": 0, "sharpen": sharpen,
            "device": "cpu"}


ITEMS = {
    "llm": lambda p, short, tmp: item_llm(p, short),
    "voice": item_voice,
    "lipsync": lambda p, short, tmp: item_lipsync(p, short, tmp, sharpen=False),
    "lipsync_sharpen": lambda p, short, tmp: item_lipsync(p, short, tmp, sharpen=True),
}


def run_item(name, home, short):
    p = Paths(home)
    tmp = tempfile.mkdtemp(prefix=f"bench_{name}_")
    try:
        res = ITEMS[name](p, short, tmp)
        res["ok"] = True
    except Exception as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:500]}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(res), flush=True)


# ---------- parent: one process per item, memory sampled across its tree ----------
def measure(name, home, short, timeout=3600):
    import psutil
    cmd = [sys.executable, "-m", "app.benchmark", "--home", str(home), "--item", name] + (["--short"] if short else [])
    proc = subprocess.Popen(cmd, cwd=str(CODE), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, text=True, env=env_for(Paths(home)))
    peak = [0]
    stop = threading.Event()

    def sample():
        try:
            root = psutil.Process(proc.pid)
        except psutil.Error:
            return
        while not stop.is_set():
            total = 0
            try:
                for q in [root] + root.children(recursive=True):
                    try:
                        total += q.memory_info().rss
                    except psutil.Error:
                        pass
            except psutil.Error:
                break
            peak[0] = max(peak[0], total)
            stop.wait(0.25)
    t = threading.Thread(target=sample, daemon=True)
    t.start()
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return {"ok": False, "error": f"took longer than {timeout // 60} minutes"}
    finally:
        stop.set()
        t.join(2)
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("{")), None)
    res = json.loads(line) if line else {"ok": False, "error": (err or "no output").strip()[-500:]}
    res["peak_rss_bytes"] = peak[0]
    return res


def machine_info():
    info = {"python": platform.python_version(), "macos": platform.mac_ver()[0] or None, "arch": platform.machine()}
    if sys.platform == "win32":
        try:
            from bootstrap.engine import windows_cpu, windows_memory
            info.update(windows=platform.version(), chip=windows_cpu(), memory_bytes=windows_memory())
        except Exception:
            pass
    if sys.platform == "darwin":
        for k, f in (("machdep.cpu.brand_string", "chip"), ("hw.memsize", "memory_bytes")):
            try:
                v = subprocess.run(["sysctl", "-n", k], capture_output=True, text=True).stdout.strip()
                info[f] = int(v) if f == "memory_bytes" else v
            except (OSError, ValueError):
                pass
    return info


def save(home, results, source):
    p = Paths(home)
    try:
        data = json.loads(p.benchmark.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if source == "selftest" and data.get("source") == "benchmark":
        data.setdefault("selftest", {}).update(results)       # never overwrite a full benchmark
    else:
        data.update({k: v for k, v in results.items() if v.get("ok")})
        data.update(source=source, measured_at=time.strftime("%Y-%m-%dT%H:%M:%S"), machine=machine_info())
    p.home.mkdir(parents=True, exist_ok=True)
    p.benchmark.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def gb(n):
    return "n/a" if not n else f"{n / 2**30:.2f} GB"


def markdown(data):
    m = data.get("machine", {})
    L, V, W, S = (data.get(k) or {} for k in ("llm", "voice", "lipsync", "lipsync_sharpen"))
    lines = [
        "# Benchmark — this Mac",
        "",
        f"Measured {data.get('measured_at', '?')} on {m.get('chip', m.get('arch', '?'))}, "
        f"{gb(m.get('memory_bytes'))} memory, macOS {m.get('macos', '?')}, by `python -m app.benchmark --full`.",
        "Each item ran in its own process; peak memory is the whole process tree, sampled every 0.25 s.",
        "",
        "| Item | Time | Peak memory |",
        "|---|---|---|",
        f"| LLM ({L.get('name', 'n/a')}) — load | {L.get('load_s', 'n/a')} s | {gb(L.get('peak_rss_bytes'))} resident, "
        f"{gb(L.get('peak_metal_bytes'))} Metal |",
        f"| LLM — first answer (cold, after load) | {L.get('first_answer_s', 'n/a')} s | |",
        f"| LLM — cold total (load + answer) | {L.get('cold_s', 'n/a')} s | |",
        f"| LLM — warm answer | {L.get('warm_s', 'n/a')} s | |",
        f"| XTTS-v2 — load (CPU) | {V.get('load_s', 'n/a')} s | {gb(V.get('peak_rss_bytes'))} |",
        f"| XTTS-v2 — {V.get('audio_seconds', 'n/a')} s utterance | {V.get('seconds', 'n/a')} s "
        f"(real-time factor {V.get('rtf', 'n/a')}) | |",
        f"| Wav2Lip — {W.get('audio_seconds', 'n/a')} s clip, no sharpening | {W.get('seconds', 'n/a')} s "
        f"({W.get('per_audio_second', 'n/a')} s per second of speech) | {gb(W.get('peak_rss_bytes'))} |",
        f"| Wav2Lip + GFPGAN — {S.get('audio_seconds', 'n/a')} s clip | {S.get('seconds', 'n/a')} s "
        f"({S.get('per_audio_second', 'n/a')} s per second of speech) | {gb(S.get('peak_rss_bytes'))} |",
        "",
        "## What this decides (HANDOFF.md section 8.3)",
        "",
    ]
    per = W.get("per_audio_second")
    if per:
        typ = 15 * per
        lines.append(f"- A typical 15-second spoken answer takes about **{typ / 60:.1f} minutes** to animate "
                     f"without sharpening" + (f" and **{15 * S['per_audio_second'] / 60:.1f} minutes** with it."
                                               if S.get("per_audio_second") else "."))
        lines.append("- Video stays **off by default**; the toggle shows these numbers. "
                     + ("At this speed live video is usable for short answers, but pre-render the demo anyway."
                        if typ < 60 else "At this speed, rely on **Prepare demo** for anything shown live."))
        lines.append("- Sharpening (GFPGAN) stays **off by default**; it is an explicit opt-in in Settings.")
    else:
        lines.append("- Lip-sync was not measured; the app shows no time estimate until it is.")
    if V.get("rtf"):
        lines.append(f"- Voice: about {V['rtf']:.1f} s of compute per second of speech on the CPU; text never waits for it.")
    if L.get("warm_s"):
        lines.append(f"- Text: a warm answer takes {L['warm_s']} s; the splash on later launches waits about "
                     f"{L.get('load_s', '?')} s for the model to load.")
    failed = [k for k in ("llm", "voice", "lipsync", "lipsync_sharpen") if data.get(k, {}).get("ok") is False]
    if failed:
        lines += ["", "Failed items: " + ", ".join(failed)]
    lines.append("")
    lines.append("Raw numbers: `benchmark.json` in `~/Library/Application Support/DeferralTwin/`.")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(prog="python -m app.benchmark")
    ap.add_argument("--home")
    ap.add_argument("--item", choices=list(ITEMS))
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--selftest", choices=list(ITEMS))
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--md", help="also write the results as Markdown here (BENCHMARK.md)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    home = Paths(a.home).home
    if a.item:
        return run_item(a.item, home, a.short)
    if a.selftest:
        res = measure(a.selftest, home, short=True)
        save(home, {a.selftest: res}, "selftest")
        print(json.dumps(res) if a.json else res, flush=True)
        return
    if a.full:
        results = {}
        for name in ("llm", "voice", "lipsync", "lipsync_sharpen"):
            print(f"measuring {name} ...", flush=True)
            results[name] = r = measure(name, home, short=False)
            print("   ", "ok" if r.get("ok") else "FAILED", {k: v for k, v in r.items() if k not in ("answer",)},
                  flush=True)
        data = save(home, results, "benchmark")
        for k, v in results.items():          # keep failures visible in the report
            data.setdefault(k, v)
        md = markdown(data)
        if a.md:
            Path(a.md).write_text(md, encoding="utf-8")
            print("wrote", a.md)
        else:
            print(md)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
