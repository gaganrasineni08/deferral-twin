"""
Voice and avatar as background jobs (HANDOFF.md section 8.3).

Text never waits for either. Voice and video have separate queues, so a
several-minute avatar render never holds up the next spoken answer. Every
result is cached by the answer text: re-asking is free, and clips rendered
ahead of time on the Prepare demo screen play instantly.

XTTS loads on the first spoken answer and stays. The avatar models load only
inside the Wav2Lip and GFPGAN subprocesses, so their memory is released the
moment a render finishes.
"""
import hashlib
import json
import queue
import threading
import time
import uuid
import wave
from pathlib import Path

WORDS_PER_SECOND = 2.6          # speaking rate, to size a clip before its audio exists


def key(text, lang, sharpen=None):
    k = f"{lang}|{text.strip()}" if sharpen is None else f"{lang}|{int(bool(sharpen))}|{text.strip()}"
    return hashlib.sha1(k.encode("utf-8")).hexdigest()[:20]


def wav_seconds(p):
    try:
        with wave.open(str(p)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, OSError, EOFError):
        return None


class Estimates:
    """Times taken from benchmark.json -- what this Mac actually measured, never a guess."""

    def __init__(self, path):
        self.path = Path(path)

    def data(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def voice_seconds(self, audio_seconds):
        v = self.data().get("voice") or {}
        if not v.get("rtf"):
            return None
        return v["rtf"] * audio_seconds + v.get("overhead_s", 0)

    def video_seconds(self, audio_seconds, sharpen):
        d = self.data()
        v = d.get("lipsync_sharpen" if sharpen else "lipsync") or {}
        if not v.get("per_audio_second"):
            return None
        return v["per_audio_second"] * audio_seconds + v.get("overhead_s", 0)

    def summary(self, words=40):
        """For the toggles: what a typical answer of `words` words costs here."""
        secs = words / WORDS_PER_SECOND
        d = self.data()
        return {"measured": bool(d), "source": d.get("source"), "measured_at": d.get("measured_at"),
                "typical_audio_seconds": round(secs),
                "voice_seconds": _r(self.voice_seconds(secs)),
                "video_seconds": _r(self.video_seconds(secs, False)),
                "video_sharpen_seconds": _r(self.video_seconds(secs, True))}


def _r(x):
    return None if x is None else round(x)


class MediaService:
    def __init__(self, paths, get_mm, tts_lock=None):
        self.p, self.get_mm = paths, get_mm
        self.tts_lock = tts_lock or threading.Lock()
        self.est = Estimates(paths.benchmark)
        self.jobs = {}
        self._voice_q, self._video_q = queue.Queue(), queue.Queue()
        self._index_lock = threading.Lock()
        threading.Thread(target=self._voice_worker, daemon=True, name="voice").start()
        threading.Thread(target=self._video_worker, daemon=True, name="video").start()

    # ---- cache ----
    def audio_path(self, text, lang):
        return self.p.audio_cache / f"{key(text, lang)}.wav"

    def clip_path(self, text, lang, sharpen):
        return self.p.clips / f"{key(text, lang, sharpen)}.mp4"

    def index(self):
        try:
            return json.loads((self.p.clips / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, text, lang, sharpen, question, seconds):
        with self._index_lock:
            idx = self.index()
            idx[key(text, lang, sharpen)] = {"text": text, "lang": lang, "sharpen": bool(sharpen),
                                              "question": question, "render_seconds": round(seconds, 1),
                                              "created": time.time()}
            self.p.clips.mkdir(parents=True, exist_ok=True)
            (self.p.clips / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- jobs ----
    def submit(self, text, lang="en", video=False, sharpen=False, question=None):
        text = (text or "").strip()
        for j in list(self.jobs.values()):            # the same clip is already on its way: don't make it twice
            if (j["text"], j["lang"], j["want_video"], j["sharpen"]) == (text, lang, bool(video), bool(sharpen)) \
                    and j["state"] not in ("done", "error"):
                return self.view(j["id"])
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "state": "queued", "text": text, "lang": lang, "want_video": bool(video),
               "sharpen": bool(sharpen), "question": question, "audio": None, "video": None,
               "error": None, "created": time.time(), "stage": None, "started": None,
               "estimate_s": None, "cached": False}
        self.jobs[jid] = job
        a, c = self.audio_path(text, lang), self.clip_path(text, lang, sharpen)
        if a.exists():
            job["audio"] = str(a)
        if video and c.exists():
            job.update(video=str(c), cached=True)
        if job["audio"] and (not video or job["video"]):
            job.update(state="done", cached=True)
        elif not job["audio"]:
            self._voice_q.put(jid)
        else:
            self._queue_video(job)
        return self.view(jid)

    def _queue_video(self, job):
        secs = wav_seconds(job["audio"]) or len(job["text"].split()) / WORDS_PER_SECOND
        job.update(state="video_queued", estimate_s=self.est.video_seconds(secs, job["sharpen"]))
        self._video_q.put(job["id"])

    def view(self, jid):
        j = self.jobs.get(jid)
        if not j:
            return None
        out = {k: j[k] for k in ("id", "state", "want_video", "sharpen", "error", "stage", "cached",
                                 "estimate_s", "question")}
        out["audio"], out["video"] = bool(j["audio"]), bool(j["video"])
        out["elapsed_s"] = round(time.time() - j["started"], 1) if j["started"] else 0
        if j["state"] == "video" and j["estimate_s"]:
            out["progress"] = min(0.95, out["elapsed_s"] / j["estimate_s"])
            out["remaining_s"] = max(5, round(j["estimate_s"] - out["elapsed_s"]))
        out["text"] = j["text"][:80]
        return out

    def active(self):
        return [self.view(j) for j, v in self.jobs.items()
                if v["want_video"] and v["state"] not in ("done", "error")]

    def file(self, jid, kind):
        j = self.jobs.get(jid) or {}
        p = j.get(kind) if kind in ("audio", "video") else None
        return p if p and Path(p).exists() else None

    def _voice_worker(self):
        while True:
            jid = self._voice_q.get()
            job = self.jobs[jid]
            try:
                mm = self.get_mm()
                if mm is None:
                    raise RuntimeError("Voice is not available for this twin.")
                job.update(state="voice", started=time.time())
                out = self.audio_path(job["text"], job["lang"])
                with self.tts_lock:
                    mm.speak(job["text"], lang=job["lang"], out_path=str(out))
                job["audio"] = str(out)
                if job["want_video"]:
                    c = self.clip_path(job["text"], job["lang"], job["sharpen"])
                    if c.exists():
                        job.update(video=str(c), state="done", cached=True)
                    else:
                        self._queue_video(job)
                else:
                    job["state"] = "done"
            except Exception as e:                     # keep the app alive; say what failed
                job.update(state="error", error=f"{type(e).__name__}: {str(e)[:300]}")

    def _video_worker(self):
        while True:
            jid = self._video_q.get()
            job = self.jobs[jid]
            try:
                mm = self.get_mm()
                if mm is None:
                    raise RuntimeError("The avatar is not available for this twin.")
                job.update(state="video", started=time.time())
                out = self.clip_path(job["text"], job["lang"], job["sharpen"])
                t0 = time.time()
                mm.animate(job["audio"], out_path=str(out), sharpen=job["sharpen"],
                           on_stage=lambda s: job.update(stage=s))
                job["video"] = str(out)
                self._remember(job["text"], job["lang"], job["sharpen"], job["question"], time.time() - t0)
                job["state"] = "done"
            except Exception as e:
                job.update(state="error", error=f"{type(e).__name__}: {str(e)[:300]}")

    def clear(self):
        self.jobs.clear()
