"""
HTTP API in front of the existing backend. Nothing here re-implements the twin:
the dataset, profile, retrieval, deferral, corrections, notes and voice/avatar
all come from src/ (build_twin, demo_app.TwinSession, multimodal, eval_phase2).
This file adds what the notebook used to do by hand -- take the interview and
uploads, run the build with real progress, and serve the twin -- plus the
desktop app's own needs: voice and avatar as background jobs with a clip
cache, the demo pre-render, settings, export, and deletion.

All state lives under the app folder (app/paths.py); nothing is written next
to the code.
"""
import contextlib
import io
import json
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.paths import CODE, Paths

STATIC = Path(__file__).resolve().parent / "static"
FIGURES = CODE / "docs" / "figures"
sys.path.insert(0, str(CODE / "src"))
sys.path.insert(0, str(CODE))

import rag_pipeline  # noqa: E402
import deferral  # noqa: E402

from app import checks  # noqa: E402
from app import online  # noqa: E402
from app import work  # noqa: E402
from app import lang as langs  # noqa: E402
from app import asr  # noqa: E402
from app.media import MediaService  # noqa: E402

SUBJECT_PID = Paths.SUBJECT_PID
LOGS = ("feedback_demo.jsonl", "notes.jsonl")
EXIT_REMOVE_EVERYTHING = 87        # tells the bootstrapper to delete the app folder

# One embedder for the whole process: a fresh SentenceTransformer per session
# leaks memory badly (HANDOFF.md section 6). RagStore looks the class up at
# call time, so a cached factory here is enough. LOCAL_MODELS points the
# configured name at the copy setup downloaded, so nothing is fetched at run time.
_EMBEDDERS = {}
LOCAL_MODELS = {}
_RealST = rag_pipeline.SentenceTransformer


def _shared_st(name, *a, **kw):
    if name not in _EMBEDDERS:
        _EMBEDDERS[name] = _RealST(LOCAL_MODELS.get(name, name), *a, **kw)
    return _EMBEDDERS[name]


rag_pipeline.SentenceTransformer = _shared_st

PHASE1 = {
    "participants": 100, "heldout_questions": 9564,
    "twin_mean": 0.332, "twin_ci": [0.325, 0.339], "human_ceiling": 0.892,
    "tiers": [
        {"tier": "hard_defer", "share": 0.010, "score": None},
        {"tier": "soft_answer", "share": 0.194, "score": 0.160},
        {"tier": "confident_answer", "share": 0.796, "score": 0.373},
    ],
    "selective": {"score": 0.373, "coverage": 0.80, "gain": 0.042},
    "feedback": {"participants": 9, "before": 0.350, "after5": 0.563, "control": 0.570, "personal20": 0.023},
}

BUILD_STAGES = [
    ("save", "Saving your interview"),
    ("dataset", "Splitting answers into memory and held-out questions"),
    ("media", "Preparing your voice and photo"),
    ("wire", "Connecting voice and avatar"),
    ("backup", "Saving everything to disk"),
    ("memory", "Embedding the twin's memory"),
    ("llm", "Loading the language model"),
]
_STAGE_MARKS = {"1/4": 1, "2/4": 2, "3/4": 3, "4/4": 4}
DEFAULT_SETTINGS = {"speak": False, "video": False, "lang": "en", "sharpen": False, "welcome": True, "online": False}


class _StageWriter(io.TextIOBase):
    """build_twin prints '1/4 ... 4/4' as it goes; turn those lines into progress."""
    def __init__(self, on_stage, log):
        self.on_stage, self.log, self.buf = on_stage, log, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.log.append(line.rstrip())
                for mark, i in _STAGE_MARKS.items():
                    if line.strip().startswith(mark):
                        self.on_stage(i)
        return len(s)


def _norm_name(s):
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


class State:
    def __init__(self, home=None, llm="auto", multimodal=True, bootstrap_url=None, drive=None):
        self.p = Paths(home).ensure()
        self.drive = Path(drive) if drive else None           # optional backup folder (Colab)
        self.multimodal, self.llm_kind, self.bootstrap_url = multimodal, llm, bootstrap_url
        if self.p.embedder.exists():
            LOCAL_MODELS["sentence-transformers/all-mpnet-base-v2"] = str(self.p.embedder)
        self.llm = {"state": "loading", "name": None, "error": None, "stand_in": None}
        self._llm = None
        self._llm_ready = threading.Event()
        self.llm_lock, self.tts_lock = threading.Lock(), threading.Lock()
        self._sink = threading.local()
        self.twin = self.session = None
        self._mm = None
        self._session_lock = threading.Lock()
        self.build = {"state": "idle", "stage": 0, "stages": [l for _, l in BUILD_STAGES],
                      "log": [], "error": None, "started": None, "finished": None}
        self.eval = {"state": "idle", "error": None}
        self.demo = {"state": "idle", "error": None}
        self.media = MediaService(self.p, self.get_mm, self.tts_lock)
        self.capture = None
        self.online_job = {"state": "idle", "error": None}
        self.game_pending = {}                            # "Guess me": the twin's locked guesses
        self.work_matcher = None                          # recognises questions about the person's field
        self.translator = langs.Translator(self.p.models)  # offline Hindi <-> English
        self.asr = asr.Recognizer(self.p.models / "asr" / "whisper-small")   # Talk mode, on this Mac
        self.work_auto_tried = False
        self.talk_job = {"state": "idle", "error": None}
        self.work_job = {"state": "idle", "error": None}

    # ---- settings ----
    def settings(self):
        try:
            return {**DEFAULT_SETTINGS, **json.loads(self.p.settings.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return dict(DEFAULT_SETTINGS)

    def save_settings(self, d):
        s = {**self.settings(), **{k: d[k] for k in DEFAULT_SETTINGS if k in d}}
        s["lang"] = "hi" if s["lang"] == "hi" else "en"
        s["online"] = bool(s["online"])
        self.p.settings.write_text(json.dumps(s, indent=1), encoding="utf-8")
        if s["online"]:
            self.refresh_online_profile()
        return s

    # ---- online mode (Google Gemini, off by default) ----
    def online_ready(self):
        return bool(self.settings().get("online")) and online.get_key() is not None

    def refresh_online_profile(self, force=False):
        """Write the first-person profile from everything the twin knows, once, and again when that
        changes (new answers, corrections, notes). In the background; offline answers meanwhile."""
        sess = self.session
        if sess is None or not self.online_ready() or self.online_job["state"] == "making":
            return
        prof = online.load_profile(self.p.twin)
        if prof and prof.get("fingerprint") == online.fingerprint(sess.store.facts) and len(prof.get("text", "")) > 800 \
                and not force:                            # (a profile cut short is written again)
            return
        self.online_job.update(state="making", error=None)

        def work():
            try:
                online.save_profile(self.p.twin, online.build_profile(sess.twin.person_name, list(sess.store.facts)))
                self.online_job.update(state="idle", error=None)
            except online.Unavailable as e:
                self.online_job.update(state="error", error=online.scrub(e)[:300])
            except Exception as e:
                self.online_job.update(state="error", error=online.scrub(f"{type(e).__name__}: {e}")[:300])
        threading.Thread(target=work, daemon=True, name="online-profile").start()

    # ---- the person's field of work ----
    def field_question(self, sess, q):
        """(work profile, topic, results, confidence) when q is about the person's field and their own memory
        has nothing confident on it; None otherwise. The gate's confidence is only read, never changed."""
        if not self.online_ready():
            return None                                   # expert knowledge comes from Gemini, in Online mode only
        w = work.load(self.p.twin)
        if not w:
            self.derive_work(sess)                        # from the interview answers, once, in the background
        if not w or deferral.CLOCK_RE.search(q) or deferral.SELF_RE.search(q) or deferral.GREET_RE.match(q):
            return None
        results, confidence = sess.store.retrieve(q)
        if confidence >= sess.twin.threshold:
            return None                                   # they actually said something close: that answer wins
        if self.work_matcher is None or self.work_matcher.embedder is not sess.store.embedder:
            self.work_matcher = work.Matcher(sess.store.embedder)
        score, topic = self.work_matcher.score(q, w)
        if score < work.MAYBE:
            return None
        if score < work.MATCH:                            # borderline: ask Gemini whether it's about their field
            try:
                yes = online.generate("You classify questions. Answer only yes or no.", work.is_field_ask(q, w),
                                      temperature=0, max_tokens=5)[0].strip().lower().startswith("yes")
            except online.Unavailable:
                yes = False
            if not yes:
                return None
        return (w, topic, results, confidence)

    def derive_work(self, sess):
        """Pull the person's job or field and skills out of their own answers with Gemini, once."""
        if self.work_auto_tried or not self.online_ready():
            return
        self.work_auto_tried = True
        rel = [f["text"].replace("\n", " | ") for f in sess.store.facts if not f.get("superseded") and
               re.search(r"study|studying|course|degree|branch|major|job|work as|profession|career|skill|engineer|"
                         r"developer|doctor|teacher|student|intern|company|college|learning", f.get("text", ""), re.I)][:40]
        if not rel:
            return

        def run():
            try:
                txt, _ = online.generate("You extract facts precisely and output JSON only.",
                    "From these answers by one person, give their job or field of study and their professional or technical "
                    "skills, only what they actually said. The title is short and natural, at most 5 words (e.g. \"Civil engineer\", "
                    "\"AI & data science student\"). Skills are work or study skills only: no languages, hobbies or traits. "
                    'JSON: {"title": "... or empty if not stated", "skills": ["..."]}\n\n' + "\n".join(rel), temperature=0.1, max_tokens=300)
                d = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
                w = work.clean(d)
                if w["title"] and not work.load(self.p.twin):
                    work.save(self.p.twin, {**w, "topics": [], "auto": True, "updated": time.time()})
                    self.refresh_work_topics(w)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True, name="work-derive").start()

    def ask_field(self, sess, q, w, topic, results, confidence, history=None, lg="en"):
        """Expert knowledge of the person's field from Gemini, in their voice, labelled as such.
        None if Gemini can't answer (the question then goes through the usual gate)."""
        import datetime
        twin = sess.twin
        now = datetime.datetime.now().strftime("%A %d %B %Y, %I:%M %p")
        n, st = twin.person_name, (twin._style() or {})
        voice = (f" Speak as {n} in the first person (I, my). Never say you are an AI, a language model or an assistant, "
                 f"and don't introduce yourself; just answer. Use {n}'s own tone: direct and casual"
                 + (", mostly lowercase" if st.get("lower") else "") + ", but give a complete, useful explanation.")
        system = work.system_prompt(n, voice, w, now)
        out = {"tier": "field_knowledge", "confidence": confidence, "field": w["title"], "topic": topic,
               "evidence": [[round(r["score"], 3), r["source"], r["text"].replace("\n", "  ")[:220]] for r in results]}
        sink = getattr(self._sink, "q", None)
        try:
            text, model = online.generate(system, self.chat_prompt(q, history, lg), max_tokens=700,
                                          on_text=(lambda t: sink.put({"type": "token", "text": t})) if sink is not None else None)
            return {**out, "answer": twin._clean(text), "mode": "online", "model": model}
        except online.Unavailable:
            return None

    @staticmethod
    def chat_prompt(q, history, lg):
        """The question, with the earlier turns of this chat, and the language to reply in."""
        h = langs.history_text(history)
        return ((f"Earlier in this chat:\n{h}\n\nNow answer this: " if h else "") + q +
                (f"\n\nReply in {langs.SCRIPT[lg]}." if lg != "en" else ""))

    def refresh_work_topics(self, w):
        """The subjects someone in this job knows, written once in the background (Gemini or the local model)."""
        self.work_job.update(state="making", error=None)

        def gen(system, prompt):
            if self.online_ready():
                try:
                    return online.generate(system, prompt, temperature=0.3, max_tokens=900)[0]
                except online.Unavailable:
                    pass
            return self.llm_fn(system, prompt, [])

        def run():
            try:
                topics = work.make_topics(w, gen)
                cur = work.load(self.p.twin)
                if cur and cur["title"] == w["title"] and cur["skills"] == w["skills"]:
                    work.save(self.p.twin, dict(cur, topics=topics, updated=time.time()))   # keeps "auto" and the CV text
                self.work_job.update(state="idle", error=None)
            except Exception as e:
                self.work_job.update(state="error", error=f"{type(e).__name__}: {str(e)[:200]}")
        threading.Thread(target=run, daemon=True, name="work-topics").start()

    def ask_online(self, sess, q, history=None, lg="en"):
        """Gemini, thinking as the person, behind the same deferral gate. Raises online.Unavailable."""
        import datetime
        import deferral
        twin = sess.twin
        if deferral.CLOCK_RE.search(q) or deferral.SELF_RE.search(q) or deferral.GREET_RE.match(q):
            return None                                   # answered locally from the clock or a template
        results, confidence = sess.store.retrieve(q)
        if confidence < twin.hard_floor:
            return None                                   # the gate refuses: no model call, online or not
        prof = online.load_profile(self.p.twin)
        if not prof:
            self.refresh_online_profile()
            raise online.Unavailable("your online profile is still being written")
        if len(prof.get("text", "")) <= 800:
            self.refresh_online_profile()                 # cut short: written again in the background
        now = datetime.datetime.now().strftime("%A %d %B %Y, %I:%M %p")
        system = online.system_prompt(twin.person_name, twin._persona_rule(), prof["text"], results, now)
        sink = getattr(self._sink, "q", None)
        on_text = (lambda t: sink.put({"type": "token", "text": t})) if sink is not None else None
        text, model = online.generate(system, self.chat_prompt(q, history, lg), on_text=on_text)
        tier = "confident_answer" if confidence >= twin.threshold else "soft_answer"
        evidence = [[round(r["score"], 3), r["source"], r["text"].replace("\n", "  ")[:220]] for r in results]
        return {"answer": twin._clean(text), "tier": tier, "confidence": confidence, "evidence": evidence,
                "mode": "online", "model": model}

    # ---- language model ----
    def start_llm(self):
        def run():
            try:
                import llm_backend
                model = str(self.p.llm_model) if self.p.llm_model.exists() else None
                # Windows: llama.cpp's server, downloaded by setup (unused on the Mac)
                kw = dict(server=str(self.p.llama_server) if self.p.llama_server.exists() else None,
                          log=str(self.p.logs / "llama-server.log"))
                if self.llm_kind == "auto":
                    cfg = rag_pipeline.load_config()
                    self._llm = llm_backend.from_config(cfg, model_override=model, **kw)
                else:
                    self._llm = llm_backend.load(self.llm_kind, model=model, **kw)
                self.llm = {"state": "ready", "name": self._llm.name, "error": None,
                            "stand_in": self._llm.stand_in, "load_s": round(self._llm.load_seconds, 1)}
            except Exception as e:                       # fall back so the app stays usable, and say so
                import llm_backend
                self._llm = llm_backend.load("stub")
                self.llm = {"state": "ready", "name": self._llm.name, "stand_in": True,
                            "error": f"{type(e).__name__}: {str(e)[:300]}"}
            self._llm_ready.set()
        threading.Thread(target=run, daemon=True, name="llm-load").start()

    def llm_fn(self, sp, q, ctx):
        """What deferral.py calls. Streams into the current request when it asked for it."""
        if not self._llm_ready.wait(timeout=900):
            raise RuntimeError("The language model is still loading.")
        sink = getattr(self._sink, "q", None)
        with self.llm_lock:
            if sink is not None and self._llm.stream_fn is not None:
                parts = []
                for piece in self._llm.stream(sp, q):
                    parts.append(piece)
                    sink.put({"type": "token", "text": piece})
                return "".join(parts).strip()
            return self._llm.fn(sp, q, ctx)

    # ---- build ----
    def mm_kwargs(self):
        p = self.p
        import os
        env = dict(os.environ)
        env["PATH"] = f"{p.bin}:{env.get('PATH', '/usr/bin:/bin')}"
        return dict(xtts_dir=str(p.xtts) if (p.xtts / "model.pth").exists() else None,
                    mt_dir=str(p.translator) if p.translator.exists() else None,
                    tts_device="cpu", wav2lip_dir=str(p.wav2lip_src), wav2lip_python=str(p.py("wav2lip")),
                    wav2lip_checkpoint=str(p.wav2lip_ckpt), gfpgan_python=str(p.py("gfpgan")),
                    gfpgan_home=str(p.gfpgan_home), out_dir=str(p.tmp), sharpen=False, env=env)

    def start_build(self, payload=None):
        if self.build["state"] == "running":
            raise HTTPException(409, "A build is already running.")
        if payload is not None:
            self.p.answers.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.to_drive(self.p.answers)
        if not self.p.answers.exists():
            raise HTTPException(400, "No interview answers yet.")
        self.build.update(state="running", stage=0, log=[], error=None, started=time.time(), finished=None)
        threading.Thread(target=self._build, daemon=True, name="build").start()

    def _build(self):
        b = self.build

        def stage(i):
            b["stage"] = i
        try:
            import build_twin
            import demo_app
            stage(1)
            w = _StageWriter(stage, b["log"])
            with contextlib.redirect_stdout(w):
                twin = build_twin.build(answers=str(self.p.answers), voice=None, photo=None,
                                        llm_fn=self.llm_fn, out_root=str(self.p.bench_root),
                                        pid=SUBJECT_PID, work_dir=str(self.p.twin),
                                        drive=str(self.drive) if self.drive else None,
                                        multimodal=self.multimodal and self.consented_media(),
                                        mm_kwargs=self.mm_kwargs())
            stage(5)
            sess = demo_app.TwinSession(str(self.p.bench_root), SUBJECT_PID, self.llm_fn)
            stage(6)
            self._llm_ready.wait(timeout=900)
            with self._session_lock:
                self.twin, self.session, self._mm = twin, sess, twin.mm
            b.update(state="done", stage=len(BUILD_STAGES), finished=time.time())
            self.refresh_online_profile()
        except BaseException as e:                       # load_phase2 raises SystemExit on bad input
            b.update(state="error", error=str(e) or type(e).__name__, finished=time.time())
            b["log"].append(traceback.format_exc()[-1500:])

    def drop_twin(self):
        with self._session_lock:
            self.twin = self.session = self._mm = None
        self.build.update(state="idle", stage=0, error=None, log=[])

    # ---- optional backup folder (Colab's Drive; unused on the Mac) ----
    def to_drive(self, *paths, sub=None):
        if not self.drive:
            return
        try:
            dest = self.drive / sub if sub else self.drive
            dest.mkdir(parents=True, exist_ok=True)
            for q in paths:
                if Path(q).exists():
                    shutil.copy2(q, dest / Path(q).name)
        except OSError as e:
            print(f"(backup copy skipped: {e})")

    def restore_from_drive(self):
        if not self.drive or not self.drive.exists():
            return []
        got = []
        for name in ("draft.json", "phase2_answers.json", "ref.wav", "photo_fixed.jpg"):
            if (self.drive / name).exists() and not (self.p.twin / name).exists():
                shutil.copy2(self.drive / name, self.p.twin / name)
                got.append(name)
        saved = self.drive / "benchmark" / SUBJECT_PID
        for name in LOGS:
            if (saved / name).exists() and not (self.p.subject / name).exists():
                self.p.subject.mkdir(parents=True, exist_ok=True)
                shutil.copy2(saved / name, self.p.subject / name)
                got.append(name)
        return got

    # ---- consent and media ----
    def consent(self):
        q = self.p.subject / "consent.json"
        if q.exists():
            return json.loads(q.read_text(encoding="utf-8"))
        for f in (self.p.answers, self.p.draft):
            if f.exists():
                try:
                    c = json.loads(f.read_text(encoding="utf-8")).get("consent")
                    if c:
                        return c
                except ValueError:
                    pass
        return None

    def consented_media(self):
        c = self.consent() or {}
        return bool(c.get("name")) and len(c.get("agreed") or []) >= 3

    def get_mm(self):
        """Voice and face belong only to the person who consented (HANDOFF.md section 2, item 4)."""
        if not self.consented_media() or not self.p.ref_wav.exists():
            return None
        if self._mm is None:
            try:
                from multimodal import MultiModal
                self._mm = MultiModal(ref_wav=str(self.p.ref_wav),
                                      photo=str(self.p.photo) if self.p.photo.exists() else None,
                                      **self.mm_kwargs())
            except Exception:
                return None
        return self._mm

    def components(self):
        """Which parts setup installed. Missing ones are offered for install, never a stack trace."""
        try:
            from bootstrap.engine import Engine
            return Engine(self.p.home).installed()
        except Exception:
            return {}

    def media_caps(self):
        import importlib.util
        p = self.p
        consented = self.consented_media()
        tts = importlib.util.find_spec("TTS") is not None
        voice_ok = tts and (p.xtts / "model.pth").exists()
        lipsync_ok = (p.py("wav2lip").exists() and (p.wav2lip_src / "inference.py").exists()
                      and p.wav2lip_ckpt.exists() and (p.ffmpeg.exists() or shutil.which("ffmpeg")))
        sharpen_ok = p.py("gfpgan").exists() and (p.gfpgan_home / "GFPGANv1.4.pth").exists()
        hindi_ok = (p.translator / "config.json").exists()
        has_voice, has_photo = p.ref_wav.exists(), p.photo.exists()
        why = None
        if not consented:
            why = "Voice and avatar need the consent record."
        elif not voice_ok:
            why = "The voice model isn't installed."
        elif not has_voice:
            why = "Add a voice recording to hear your twin."
        return {"consented": consented, "voice": bool(consented and voice_ok and has_voice),
                "video": bool(consented and voice_ok and has_voice and has_photo and lipsync_ok),
                "sharpen": bool(sharpen_ok), "hindi": bool(hindi_ok),
                "installed": {"voice": bool(voice_ok), "lipsync": bool(lipsync_ok),
                              "sharpen": bool(sharpen_ok), "hindi": bool(hindi_ok)},
                "has_voice": has_voice, "has_photo": has_photo, "why": why,
                "estimates": self.media.est.summary()}

    def media_changed(self):
        """A new voice clip or photo makes every cached clip stale."""
        self._mm = None
        for d in (self.p.audio_cache, self.p.clips):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

    # ---- demo pre-render ----
    def demo_path(self):
        return self.p.clips / "demo.json"

    def demo_items(self):
        try:
            return json.loads(self.demo_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def start_demo(self, questions, lang, sharpen):
        if self.demo["state"] == "running":
            raise HTTPException(409, "Clips are already being prepared.")
        qs = [q.strip() for q in questions if q and q.strip()][:30]
        if not qs:
            raise HTTPException(400, "Choose at least one question.")
        items = [{"question": q, "lang": lang, "sharpen": sharpen, "answer": None, "tier": None, "job": None}
                 for q in qs]
        self.demo_path().write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        self.demo = {"state": "running", "error": None}

        def go():
            try:
                for it in items:
                    out = self.session.ask(it["question"])
                    it.update(answer=out["answer"], tier=out["tier"])
                    job = self.media.submit(out["answer"], lang, video=True, sharpen=sharpen, question=it["question"])
                    it["job"] = job["id"]
                    self.demo_path().write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
                self.demo = {"state": "queued", "error": None}
            except Exception as e:
                self.demo = {"state": "error", "error": f"{type(e).__name__}: {e}"}
        threading.Thread(target=go, daemon=True, name="demo").start()

    def demo_status(self):
        items = []
        for it in self.demo_items():
            ready = bool(it.get("answer")) and self.media.clip_path(it["answer"], it["lang"], it["sharpen"]).exists()
            j = self.media.view(it.get("job")) if it.get("job") else None
            items.append({**it, "ready": ready, "job": j})
        state = self.demo["state"]
        if state == "queued" and all(i["ready"] or (i["job"] and i["job"]["state"] == "error") for i in items):
            state = "done"
        return {"state": state, "error": self.demo["error"], "items": items}

    # ---- data ----
    def export(self):
        stamp = time.strftime("%Y%m%d-%H%M")
        downloads = Path.home() / "Downloads"
        dest_dir = downloads if downloads.is_dir() else self.p.home / "exports"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"DeferralTwin-export-{stamp}.zip"
        keep = ("phase2_answers.json", "draft.json", "ref.wav", "photo_fixed.jpg")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for name in keep:
                f = self.p.twin / name
                if f.exists():
                    z.write(f, name)
            for f in sorted((self.p.twin / "uploads").glob("*")):
                z.write(f, f"original_uploads/{f.name}")
            for name in LOGS + ("profile.json", "consent.json", "phase2_results.json"):
                f = self.p.subject / name
                if f.exists():
                    z.write(f, {"feedback_demo.jsonl": "corrections.jsonl"}.get(name, name))
            z.writestr("README.txt",
                       "Everything this twin holds about you.\n\n"
                       "phase2_answers.json  your interview, with the consent record\n"
                       "ref.wav, photo_fixed.jpg  the voice clip and photo the twin uses\n"
                       "corrections.jsonl  every 'that's not me' correction\n"
                       "notes.jsonl  everything you told it directly\n"
                       "profile.json  the behaviour profile computed from your answers\n")
        return dest

    def wipe_twin(self):
        self.drop_twin()
        self.media.clear()
        for d in (self.p.twin, self.p.cache):
            shutil.rmtree(d, ignore_errors=True)
        self.p.ensure()


def check_upload_photo(state, raw):
    import build_twin
    if raw.suffix in (".heic", ".heif"):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise HTTPException(400, "This photo format can't be read here. Export it as JPEG and try again.")
    cand = state.p.tmp / "photo_candidate.jpg"
    build_twin.prepare_photo(raw, cand)
    res = checks.check_photo(cand, state.p.yunet)
    if res["ok"]:
        shutil.copy2(cand, state.p.photo)
        state.to_drive(state.p.photo)
        state.media_changed()
    return res


def check_upload_voice(state, raw):
    import build_twin
    cand = state.p.tmp / "voice_candidate.wav"
    build_twin.prepare_voice(raw, cand)
    res = checks.check_voice(cand)
    if res["ok"]:
        shutil.copy2(cand, state.p.ref_wav)
        state.to_drive(state.p.ref_wav)
        state.media_changed()
    return res


class AskIn(BaseModel):
    question: str
    history: list[dict] = []          # earlier turns of this chat: [{"q", "a"}], newest last


class CorrectIn(BaseModel):
    question: str
    twin_answer: str
    expected: str
    tier: str | None = None
    confidence: float | None = None


# "Guess me": new everyday questions, none of them in the interview. The twin locks its guess first,
# then the person answers; the match is scored and their answer becomes memory, so the score can climb.
GAME_QUESTIONS = [
    "Your phone battery hits 5% and you're out for three more hours. What do you do?",
    "You get an unexpected 10,000 rupees. What do you do with it?",
    "Your cycle gets a puncture on the way to college. What do you do?",
    "A friend asks you to lie to their parents for them. What do you say?",
    "You're offered a free trip abroad, but it clashes with your exams. What do you do?",
    "You see a classmate cheating in an exam. What do you do?",
    "You wake up an hour late on the day of an important presentation. What do you do?",
    "Your best friend starts dating someone you don't trust. What do you do?",
    "You're at a restaurant and the food is cold. What do you do?",
    "Someone cuts in front of you in a long queue. What do you do?",
    "You have to choose between a high-paying job far from home and a decent one nearby. Which do you take?",
    "You're asked to speak on stage with no preparation. What do you do?",
    "Your group wants to bunk a class you like. What do you do?",
    "You find a wallet with cash and an ID card on the road. What do you do?",
    "Your parents want you to study something you're not interested in. What do you do?",
    "You get a chance to meet anyone famous for an hour. Who do you pick, and why?",
    "Your internet goes down for a whole day. How do you spend it?",
    "A senior offers to help you, but you'd rather figure it out yourself. What do you do?",
    "You are put in a team with people you don't know at all. What do you do first?",
    "You failed a test you studied hard for. How do you react?",
    "You're invited to two events on the same evening. How do you choose?",
    "Your roommate keeps playing loud music at night. What do you do?",
    "You have one free hour between classes. What do you do?",
    "Someone sends you a rude message by mistake. How do you respond?",
    "You can learn any one new skill instantly. Which one?",
    "A friend borrowed money weeks ago and hasn't returned it. What do you do?",
    "You're asked to choose a movie for the whole group. What do you pick?",
    "Your project idea gets rejected by the professor. What do you do next?",
    "You notice you've been on your phone for four hours straight. What do you do?",
    "You're feeling low on a Sunday evening. What helps you most?",
    "You're asked to give honest feedback on a friend's bad startup idea. What do you say?",
    "It's raining heavily and you have an important class. Do you go?",
    "You get a message from an unknown number offering a job. What do you do?",
    "You have a big decision to make. Who do you talk to first?",
    "You're given a week off with no plans. Where do you go?",
    "Your teammate does all the work and wants equal credit for you. What do you do?",
    "You're at a party where you know nobody. What do you do?",
    "You have to pick between sleeping early and finishing a series. Which wins?",
]


# Big Five from the interview's "How you tend to be" items: four per trait, "-" = reverse-keyed.
BIG_FIVE = [
    ("Openness", "curious, open to new things and ideas", ["q072", "q073", "q074", "q075"]),
    ("Conscientiousness", "organised, dependable, goal-driven", ["q064", "-q065", "q066", "q067"]),
    ("Extraversion", "outgoing, energised by people", ["q056", "-q057", "-q058", "q059"]),
    ("Agreeableness", "considerate, cooperative, kind", ["q060", "-q061", "q062", "q063"]),
    ("Neuroticism", "prone to stress and worry (low = calm, steady)", ["q068", "-q069", "q070", "q071"]),
]


class DecideIn(BaseModel):
    situation: str
    options: list[str]


class GameIn(BaseModel):
    question: str
    answer: str = ""


class NoteIn(BaseModel):
    text: str


class MediaIn(BaseModel):
    text: str
    lang: str = "en"
    video: bool = False
    sharpen: bool | None = None


class DemoIn(BaseModel):
    questions: list[str]
    lang: str = "en"
    sharpen: bool | None = None


class NameIn(BaseModel):
    name: str = ""


class RedoIn(BaseModel):
    keep_memory: bool = True


def create_app(state: State) -> FastAPI:
    api = FastAPI(title="Deferral-Aware Digital Twin")
    S = state

    def need_session():
        if S.session is None:
            raise HTTPException(409, "The twin is not built yet.")
        return S.session

    def _ask(q, history=None):
        sess = need_session()
        t0 = time.time()
        asked = q
        lg = langs.detect(q)
        gem = (lambda sp, pr: online.generate(sp, pr, temperature=0.2, max_tokens=900)[0]) if S.online_ready() else None
        q, lnote = langs.prepare(asked, history, lg, gem, S.translator)   # one standalone English question
        out = None
        fq = S.field_question(sess, q)
        if fq:
            out = S.ask_field(sess, q, *fq, history=history, lg=lg)
        if out is None and S.online_ready():
            try:
                out = S.ask_online(sess, q, history, lg)
            except online.Unavailable as e:              # no internet, quota used up, no profile yet: answer offline
                out = sess.ask(q)
                out.update(mode="offline", online_note=online.scrub(e)[:160])
        if out is None:
            out = sess.ask(q)
        if lg != "en" or q != asked:
            out["asked_as"] = q
        if lg != "en":
            out["answer_en"] = out["answer"]
            out["answer"], note2 = langs.reply_in(out["answer"], lg, gem, S.translator)
            lnote = lnote or note2
        out["lang"] = lg
        if lnote:
            out["lang_note"] = lnote
        out["evidence"] = [{"score": e[0], "source": e[1], "text": e[2]} for e in out["evidence"]]
        out["seconds"] = round(time.time() - t0, 2)
        held = next((r for r in sess.test if r["question"] in (q, asked)), None)
        if not held and out["tier"] in ("hard_defer", "soft_answer"):    # a gap: offered back on the Teach page
            with open(S.p.bench_root / SUBJECT_PID / "gaps.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.time(), "question": q, "tier": out["tier"],
                                    "confidence": round(out["confidence"], 3)}, ensure_ascii=False) + "\n")
        if held:
            out["truth"] = held["answer"]
            out["truth_similarity"] = (None if out["tier"] == "hard_defer"
                                       else round(sess.similarity(out["answer"], held["answer"]), 3))
        return out

    @api.get("/api/status")
    def status():
        sess = S.session
        c = S.consent() or {}
        return {
            "mode": "live", "llm": S.llm, "build": {k: S.build[k] for k in ("state", "stage", "error")},
            "built": sess is not None, "subject": c.get("name"),
            "has_answers": S.p.answers.exists(),
            "has_voice": S.p.ref_wav.exists(), "has_photo": S.p.photo.exists(),
            "media": S.media_caps(), "settings": S.settings(), "components": S.components(),
            "bootstrap_url": S.bootstrap_url, "home": str(S.p.home),
            "memory": {"corrections": sess.n_taught, "notes": sess.n_noted,
                       "facts": len(sess.store.facts), "heldout": len(sess.test)} if sess else None,
            "video_jobs": S.media.active(),
            "online": S.online_ready(),
            "talk": S.asr.available(), "talk_job": dict(S.talk_job),
        }

    @api.get("/api/questions")
    def questions():
        return FileResponse(STATIC / "questions.json")

    # ---- interview draft: saved as it is typed ----
    @api.get("/api/draft")
    def get_draft():
        return JSONResponse(json.loads(S.p.draft.read_text(encoding="utf-8")) if S.p.draft.exists() else {})

    @api.put("/api/draft")
    def put_draft(draft: dict):
        tmp = S.p.draft.with_suffix(".tmp")
        tmp.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        tmp.replace(S.p.draft)
        S.to_drive(S.p.draft)
        return {"ok": True}

    # ---- voice and photo ----
    @api.post("/api/upload/{kind}")
    async def upload(kind: str, file: UploadFile = File(...)):
        if kind not in ("voice", "photo"):
            raise HTTPException(404)
        raw = S.p.twin / "uploads" / f"{kind}_{int(time.time())}{Path(file.filename or '').suffix.lower() or '.bin'}"
        raw.write_bytes(await file.read())
        try:
            return check_upload_voice(S, raw) if kind == "voice" else check_upload_photo(S, raw)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Could not read that {kind} file ({type(e).__name__}: {str(e)[:200]})")

    @api.get("/api/upload/{kind}")
    def get_upload(kind: str, candidate: bool = False):
        if candidate:
            p = S.p.tmp / {"voice": "voice_candidate.wav", "photo": "photo_candidate.jpg"}.get(kind, "_")
        else:
            p = {"voice": S.p.ref_wav, "photo": S.p.photo}.get(kind, S.p.twin / "_")
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-store"})

    # Recording in Python, for when the window cannot use the camera or microphone itself.
    @api.post("/api/capture/voice/start")
    def capture_start():
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            raise HTTPException(409, "Recording from here isn't available. Choose a file instead.")
        if S.capture:
            S.capture["stream"].stop()
        frames = []
        stream = sd.InputStream(samplerate=22050, channels=1, dtype="int16",
                                callback=lambda data, n, t, st: frames.append(data.copy()))
        stream.start()
        S.capture = {"stream": stream, "frames": frames, "started": time.time(), "np": np}
        return {"ok": True}

    @api.post("/api/capture/voice/stop")
    def capture_stop():
        cap = S.capture
        if not cap:
            raise HTTPException(409, "Not recording.")
        cap["stream"].stop()
        cap["stream"].close()
        S.capture = None
        import wave
        raw = S.p.twin / "uploads" / f"voice_{int(time.time())}.wav"
        data = cap["np"].concatenate(cap["frames"]) if cap["frames"] else cap["np"].zeros((0, 1), "int16")
        with wave.open(str(raw), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(data.tobytes())
        return check_upload_voice(S, raw)

    # ---- Talk mode: speech to text with Whisper, on this Mac ----
    def _transcribe(src, hint=None):
        if not S.asr.available():
            raise HTTPException(409, "Speech recognition isn't installed yet.")
        wav = S.p.tmp / f"talk_{int(time.time() * 1000)}.wav"
        try:
            S.asr.to_wav16k(src, wav)
            text, lang = S.asr.transcribe(wav, hint=hint)
        except subprocess.CalledProcessError:
            raise HTTPException(400, "Couldn't read that recording. Try again.")
        finally:
            for f in (wav, Path(src)):
                try:
                    f.unlink()
                except OSError:
                    pass
        if not text:
            raise HTTPException(400, "I didn't catch that. Hold the button and speak a little closer.")
        return {"text": text, "lang": langs.detect(text) if langs.detect(text) != "en" else (lang or "en")}

    @api.post("/api/talk/install")
    def talk_install():
        """Download Whisper (speech recognition, about 970 MB) and the Hindi->English translator, once."""
        if S.asr.available() or S.talk_job["state"] == "running":
            return dict(S.talk_job, installed=S.asr.available())
        S.talk_job.update(state="running", error=None)

        def run():
            try:
                from huggingface_hub import snapshot_download
                for repo, dst, pats in [("openai/whisper-small", "asr/whisper-small", ["*.json", "*.txt", "model.safetensors"]),
                                        ("Helsinki-NLP/opus-mt-hi-en", "mt/opus-mt-hi-en", ["*.json", "*.spm", "pytorch_model.bin"])]:
                    snapshot_download(repo, local_dir=str(S.p.models / dst), allow_patterns=pats)
                S.talk_job.update(state="done", error=None)
            except Exception as e:
                S.talk_job.update(state="error", error=f"{type(e).__name__}: {str(e)[:200]}")
        threading.Thread(target=run, daemon=True, name="talk-install").start()
        return dict(S.talk_job, installed=False)

    @api.post("/api/transcribe")
    async def transcribe(file: UploadFile = File(...), lang: str = ""):
        raw = S.p.tmp / f"talk_in_{int(time.time() * 1000)}{Path(file.filename or '').suffix.lower() or '.webm'}"
        S.p.tmp.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(await file.read())
        return _transcribe(raw, lang or None)

    @api.post("/api/capture/talk/start")
    def talk_start():
        return capture_start()

    @api.post("/api/capture/talk/stop")
    def talk_stop(lang: str = ""):
        """Like the voice recorder, but the clip is only transcribed: it never replaces the voice sample."""
        cap = S.capture
        if not cap:
            raise HTTPException(409, "Not recording.")
        cap["stream"].stop()
        cap["stream"].close()
        S.capture = None
        import wave
        raw = S.p.tmp / f"talk_in_{int(time.time() * 1000)}.wav"
        S.p.tmp.mkdir(parents=True, exist_ok=True)
        data = cap["np"].concatenate(cap["frames"]) if cap["frames"] else cap["np"].zeros((0, 1), "int16")
        with wave.open(str(raw), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(data.tobytes())
        return _transcribe(raw, lang or None)

    @api.post("/api/capture/photo")
    def capture_photo():
        try:
            import cv2
        except ImportError:
            raise HTTPException(409, "The camera isn't available from here. Choose a file instead.")
        cam = cv2.VideoCapture(0, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(0)
        try:
            frame = None
            for _ in range(15):                 # let exposure settle
                ok, f = cam.read()
                if ok:
                    frame = f
            if frame is None:
                where = ("Settings, Privacy & security, Camera (\"Let desktop apps access your camera\")"
                         if sys.platform == "win32" else "System Settings, Privacy & Security, Camera")
                raise HTTPException(409, "The camera didn't return a picture. Check that the app may use it "
                                         f"in {where}, or choose a file.")
        finally:
            cam.release()
        raw = S.p.twin / "uploads" / f"photo_{int(time.time())}.jpg"
        cv2.imwrite(str(raw), frame)
        return check_upload_photo(S, raw)

    # ---- build ----
    @api.post("/api/build")
    def build(payload: dict | None = None):
        if payload is not None and not (payload.get("consent") or {}).get("name"):
            raise HTTPException(400, "Consent and your name are needed before building.")
        S.start_build(payload)
        return S.build

    @api.get("/api/build")
    def build_status():
        return S.build

    # ---- the twin ----
    @api.post("/api/ask")
    def ask(body: AskIn):
        q = body.question.strip()
        if not q:
            raise HTTPException(400, "Type a question first.")
        return _ask(q, body.history[-6:])

    @api.post("/api/ask/stream")
    def ask_stream(body: AskIn):
        """Newline-delimited JSON: token pieces as they are generated, then the full result."""
        q = body.question.strip()
        if not q:
            raise HTTPException(400, "Type a question first.")
        need_session()
        out_q = queue.Queue()

        def work():
            S._sink.q = out_q
            try:
                out_q.put({"type": "done", **_ask(q, body.history[-6:])})
            except Exception as e:
                out_q.put({"type": "error", "detail": f"{type(e).__name__}: {e}"})
            finally:
                S._sink.q = None
                out_q.put(None)
        threading.Thread(target=work, daemon=True).start()

        def gen():
            while True:
                item = out_q.get()
                if item is None:
                    return
                yield json.dumps(item, ensure_ascii=False) + "\n"
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @api.get("/api/heldout")
    def heldout():
        sess = need_session()
        if not sess.test:
            raise HTTPException(404, "No held-out questions.")
        r = random.choice(sess.test)
        return {"question": r["question"], "section": r.get("section")}

    @api.get("/api/heldout/all")
    def heldout_all():
        sess = need_session()
        return {"questions": [r["question"] for r in sess.test]}

    @api.post("/api/correct")
    def correct(body: CorrectIn):
        sess = need_session()
        if not body.expected.strip():
            raise HTTPException(400, "Type what you would have said.")
        sess.teach(body.question, body.twin_answer, body.expected.strip(), body.tier, body.confidence)
        S.to_drive(sess.feedback_log, sub=f"benchmark/{SUBJECT_PID}")
        S.refresh_online_profile()
        return {"ok": True, "corrections": sess.n_taught}

    @api.post("/api/note")
    def note(body: NoteIn):
        sess = need_session()
        added = sess.note(body.text)
        if not added:
            raise HTTPException(400, "Type something first.")
        S.to_drive(sess.note_log, sub=f"benchmark/{SUBJECT_PID}")
        S.refresh_online_profile()
        return {"ok": True, "added": added, "notes": sess.n_noted}

    # ---- work and skills ----
    @api.get("/api/work")
    def work_get():
        w = work.load(S.p.twin)
        return {"work": w, "job": dict(S.work_job)}

    @api.post("/api/work")
    def work_set(d: dict):
        sess = need_session()
        w = work.clean(d)
        if not w["title"]:
            raise HTTPException(400, "Type your job or field, for example “civil engineer”.")
        old = work.load(S.p.twin)
        w["topics"] = old["topics"] if old and old["title"] == w["title"] and old["skills"] == w["skills"] else []
        w["updated"] = time.time()
        work.save(S.p.twin, w)
        if not old or old["title"] != w["title"] or old["skills"] != w["skills"]:
            sess.note(f"I work as a {w['title']}." + (f" My skills: {', '.join(w['skills'])}." if w["skills"] else ""))
            S.to_drive(sess.note_log, sub=f"benchmark/{SUBJECT_PID}")
            S.refresh_online_profile()
            S.refresh_work_topics(w)
        return {"ok": True, "work": work.load(S.p.twin), "job": dict(S.work_job)}

    @api.delete("/api/work")
    def work_delete():
        try:
            work.path(S.p.twin).unlink()
        except OSError:
            pass
        return {"ok": True}

    @api.post("/api/work/upload")
    async def work_upload(file: UploadFile = File(...)):
        """Text from a CV or job report (.txt, .md, .pdf, .docx), to fill the box; nothing is kept here."""
        raw, name = await file.read(), (file.filename or "").lower()
        try:
            if name.endswith(".pdf"):
                try:
                    import pypdf
                except ImportError:
                    raise HTTPException(400, "PDFs can't be read here yet. Open it, copy the text and paste it in the box.")
                text = "\n".join((pg.extract_text() or "") for pg in pypdf.PdfReader(io.BytesIO(raw)).pages)
            elif name.endswith(".docx"):
                xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode("utf-8", "ignore")
                text = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
            else:
                text = raw.decode("utf-8", "ignore")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Couldn't read that file ({type(e).__name__}). Paste the text instead.")
        text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()
        if not text:
            raise HTTPException(400, "No text found in that file. Paste the text instead.")
        return {"text": text[:work.ABOUT_MAX], "truncated": len(text) > work.ABOUT_MAX}

    # ---- personality snapshot ----
    @api.get("/api/bigfive")
    def bigfive():
        try:
            rows = json.loads(S.p.answers.read_text(encoding="utf-8"))["answers"]
        except (OSError, ValueError, KeyError):
            raise HTTPException(409, "No interview answers yet.")
        from persona import likert_value
        val = {r["id"]: likert_value(r.get("answer")) for r in rows}
        traits = []
        for name, desc, items in BIG_FIVE:
            vs = []
            for it in items:
                v = val.get(it.lstrip("-"))
                if v:
                    vs.append(6 - v if it.startswith("-") else v)
            traits.append({"trait": name, "about": desc, "items": len(vs),
                           "score": round((sum(vs) / len(vs) - 1) / 4 * 100) if vs else None})
        return {"traits": traits, "source": "Your answers to the 20 “How you tend to be” statements (4 per trait, some reverse-scored)."}

    # ---- "What would I do?" ----
    @api.post("/api/decide")
    def decide(body: DecideIn):
        """A situation and options: which one the person would pick, why, and how sure. Behind the same gate:
        if nothing they said is related, it says it doesn't know."""
        sess = need_session()
        sit = body.situation.strip()
        opts = [o.strip() for o in body.options if o and o.strip()][:6]
        if not sit or len(opts) < 2:
            raise HTTPException(400, "Describe the situation and give at least two options.")
        t0 = time.time()
        results, confidence = sess.store.retrieve(sit + " " + " / ".join(opts))
        evidence = [{"score": round(r["score"], 3), "source": r["source"], "text": r["text"].replace("\n", "  ")[:220]} for r in results]
        support = []
        for o in opts:
            rs, c = sess.store.retrieve(f"{sit} {o}")
            support.append({"option": o, "score": round(c, 3), "best": rs[0]["text"].replace("\n", "  ")[:200] if rs else None})
        base = {"options": opts, "support": support, "confidence": round(confidence, 3), "evidence": evidence}
        if confidence < sess.twin.hard_floor:
            return {**base, "choice": None, "tier": "hard_defer", "reasons": ["Nothing you've told me is close enough to this, so I won't guess."],
                    "certainty": "none", "seconds": round(time.time() - t0, 2)}
        numbered = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(opts))
        real = "\n".join(f"- {e['text']}" for e in evidence[:6])
        ask = (f"Situation: {sit}\nOptions:\n{numbered}\n\nThings I actually said that may help:\n{real}\n\n"
               'Which option would I pick? Reply as JSON only: {"choice": <option number>, "reasons": ["two or three short '
               'first-person reasons, each tied to something I said"], "certainty": "high" | "medium" | "low"}')
        name = sess.twin.person_name
        out = None
        if S.online_ready():
            prof = online.load_profile(S.p.twin)
            system = (f"You are {name}'s digital twin choosing as {name} would." + sess.twin._persona_rule() +
                      (f"\n\n{name}'s profile:\n{prof['text']}" if prof else ""))
            try:
                txt, model = online.generate(system, ask, temperature=0.3, max_tokens=500)
                out = {"raw": txt, "mode": "online", "model": model}
            except online.Unavailable as e:
                base["online_note"] = online.scrub(e)[:160]
        if out is None:
            txt = S.llm_fn(f"You are {name}'s digital twin choosing as {name} would." + sess.twin._persona_rule(), ask, results)
            out = {"raw": txt or "", "mode": "offline"}
        choice, reasons, certainty = None, [], None
        try:
            d = json.loads(re.search(r"\{.*\}", out["raw"], re.S).group(0))
            choice = int(d.get("choice")) - 1
            reasons = [str(r) for r in (d.get("reasons") or [])][:3]
            certainty = str(d.get("certainty") or "").lower() or None
        except Exception:
            m = re.search(r"\b([1-6])\b", out["raw"] or "")
            choice = int(m.group(1)) - 1 if m else None
            reasons = [re.sub(r"\s+", " ", out["raw"] or "").strip()[:300]] if out["raw"] else []
        if choice is None or not 0 <= choice < len(opts):
            choice = max(range(len(opts)), key=lambda i: support[i]["score"])   # the option closest to what they said
            reasons = reasons or ["It's the option closest to things you've said before."]
        if confidence < sess.twin.threshold:                # a guess from related answers, never "high"
            certainty = "low" if certainty in (None, "low") else "medium"
        tier = "confident_answer" if confidence >= sess.twin.threshold else "soft_answer"
        return {**base, "choice": choice, "reasons": reasons, "certainty": certainty or "medium", "tier": tier,
                "mode": out["mode"], "model": out.get("model"), "seconds": round(time.time() - t0, 2)}

    # ---- "Guess me" ----
    def game_log():
        return S.p.bench_root / SUBJECT_PID / "game.jsonl"

    def game_rounds():
        try:
            return [json.loads(l) for l in game_log().read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            return []

    def game_stats(rounds):
        scored = [r["match"] for r in rounds if r.get("match") is not None]
        half = max(1, min(5, len(scored) // 2))
        return {"rounds": len(rounds), "average": round(sum(scored) / len(scored), 3) if scored else None,
                "first": round(sum(scored[:half]) / half, 3) if len(scored) >= 2 else None,
                "latest": round(sum(scored[-half:]) / half, 3) if len(scored) >= 2 else None,
                "dont_know": sum(1 for r in rounds if r.get("match") is None),
                "left": len([q for q in GAME_QUESTIONS if q not in {r["question"] for r in rounds}])}

    @api.get("/api/game")
    def game_next():
        need_session()
        rounds = game_rounds()
        played = {r["question"] for r in rounds}
        left = [q for q in GAME_QUESTIONS if q not in played]
        return {"question": left[0] if left else None, "stats": game_stats(rounds)}

    @api.post("/api/game/guess")
    def game_guess(body: GameIn):
        """The twin answers first; the guess is kept here and only shown after the person answers."""
        need_session()
        if body.question not in GAME_QUESTIONS:
            raise HTTPException(400, "Not a game question.")
        out = _ask(body.question)
        S.game_pending[body.question] = out
        return {"locked": True, "mode": out.get("mode"), "seconds": out.get("seconds")}

    @api.post("/api/game/answer")
    def game_answer(body: GameIn):
        sess = need_session()
        out = S.game_pending.pop(body.question, None)
        if out is None:
            raise HTTPException(409, "The twin hasn't guessed this one yet.")
        mine = body.answer.strip()
        if not mine:
            S.game_pending[body.question] = out
            raise HTTPException(400, "Type what you would do.")
        match = None if out["tier"] == "hard_defer" else round(max(0.0, sess.similarity(out["answer"], mine)), 3)
        sess.teach(body.question, out["answer"], mine, out["tier"], out["confidence"])   # memory, never training
        S.to_drive(sess.feedback_log, sub=f"benchmark/{SUBJECT_PID}")
        S.refresh_online_profile()
        rec = {"time": time.time(), "question": body.question, "guess": out["answer"], "answer": mine, "match": match,
               "tier": out["tier"], "confidence": round(out["confidence"], 3), "mode": out.get("mode")}
        with open(game_log(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {**rec, "stats": game_stats(game_rounds())}

    @api.post("/api/game/reset")
    def game_reset():
        """Play the questions again (what was learned stays in memory)."""
        need_session()
        try:
            game_log().unlink()
        except OSError:
            pass
        return {"ok": True}

    # ---- online mode ----
    @api.get("/api/online")
    def online_status():
        prof = online.load_profile(S.p.twin)
        fresh = bool(prof and S.session and prof.get("fingerprint") == online.fingerprint(S.session.store.facts))
        m = online._models
        return {"enabled": bool(S.settings().get("online")), "has_key": online.get_key() is not None,
                "models": {"pro": m["pro"], "flash": m["flash"]} if (m["pro"] or m["flash"]) else None,
                "profile": {"text": prof["text"], "model": prof.get("model"), "created": prof.get("created"),
                            "fresh": fresh} if prof else None,
                "job": dict(S.online_job)}

    @api.post("/api/online/key")
    def online_key(d: dict):
        key = str(d.get("key") or "").strip()
        key = re.sub(r"\s+", "", key)                              # pasted keys can carry spaces or line breaks
        if not re.fullmatch(r"[\x21-\x7e]{20,300}", key):           # Google checks the key itself, just below
            raise HTTPException(400, "That doesn't look like a Gemini API key (paste the whole key from aistudio.google.com).")
        try:
            pro, flash = online.models(key, refresh=True)          # check it works before keeping it
        except online.Unavailable as e:
            msg = online.scrub(e, key)
            if re.search(r"API key not valid|API_KEY_INVALID|UNAUTHENTICATED|401", msg):
                raise HTTPException(400, "Google says this key isn't valid. In aistudio.google.com → API Keys, press the copy "
                                         "button next to the key (the list only shows its last 4 characters) and paste the whole thing here.")
            if re.search(r"PERMISSION_DENIED|403|SERVICE_DISABLED", msg):
                raise HTTPException(400, "Google accepted the key but won't let it use Gemini (the Generative Language API "
                                         "may be off for its project). Try a key created in AI Studio itself.")
            raise HTTPException(400, "Gemini didn't accept that key: " + msg[:200])
        online.set_key(key)
        S.refresh_online_profile()
        return {"ok": True, "models": {"pro": pro, "flash": flash}}

    @api.delete("/api/online/key")
    def online_key_delete():
        online.clear_key()
        S.save_settings({"online": False})
        return {"ok": True}

    @api.post("/api/online/profile")
    def online_profile():
        need_session()
        if not S.online_ready():
            raise HTTPException(409, "Turn on Online mode and add a key first.")
        S.refresh_online_profile(force=True)
        return dict(S.online_job)

    @api.get("/api/gaps")
    def gaps():
        """Questions the twin refused or had to guess, newest first, minus the ones taught since."""
        sess = need_session()
        log = S.p.bench_root / SUBJECT_PID / "gaps.jsonl"
        if not log.exists():
            return {"items": []}
        taught = {rag_pipeline._question_key(f["text"]) for f in sess.store.facts if f["source"] == "user_correction"}
        seen, items = set(), []
        for line in reversed(log.read_text(encoding="utf-8").splitlines()):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = rag_pipeline._question_key("Q: " + r["question"])
            if k in seen or k in taught or deferral.GREET_RE.match(r["question"]):
                continue
            seen.add(k)
            items.append(r)
        return {"items": items[:12]}

    @api.get("/api/memory")
    def memory():
        sess = need_session()
        mine = [{"kind": "correction" if f["source"] == "user_correction" else "note", "text": f["text"]}
                for f in sess.store.facts if f["source"] in ("user_correction", "user_note") and not f.get("superseded")]
        return {"items": mine[::-1], "interview_facts": sum(1 for f in sess.store.facts
                                                             if f["source"] not in ("user_correction", "user_note"))}

    @api.get("/api/profile")
    def profile():
        sess = need_session()
        p = sess.profile
        if p is None:
            raise HTTPException(404, "No profile.")
        from persona import TRAIT_POLES
        d = p.to_dict()
        d["poles"] = TRAIT_POLES
        d["markdown"] = p.as_markdown()
        return d

    # ---- voice and avatar jobs ----
    @api.post("/api/media")
    def media(body: MediaIn):
        caps = S.media_caps()
        if not caps["consented"]:
            raise HTTPException(403, "Voice and avatar are only for the person who consented.")
        if not caps["voice"] or (body.video and not caps["video"]):
            raise HTTPException(409, caps["why"] or "The lip-sync model isn't installed, or there is no photo yet.")
        lang = "hi" if body.lang == "hi" else "en"
        text = body.text
        if lang == "hi" and langs.detect(text) != "hi" and S.online_ready():
            try:                                          # natural Hindi from Gemini instead of the literal offline model
                text = langs.reply_in(text, "hi", lambda sp, pr: online.generate(sp, pr, temperature=0.2, max_tokens=900)[0])[0]
            except Exception:
                pass
        if lang == "hi" and langs.detect(text) != "hi" and not caps["hindi"]:
            raise HTTPException(409, "The Hindi translator isn't installed.")
        sharpen = S.settings()["sharpen"] if body.sharpen is None else body.sharpen
        return S.media.submit(text, lang, body.video, bool(sharpen and caps["sharpen"]))

    @api.get("/api/welcome")
    def welcome():
        """The greeting that pops up when the app opens: the person's avatar, in their cloned voice.
        Made once through the normal voice and lip-sync queue, then served from the clip cache."""
        caps = S.media_caps()
        name = ((S.consent() or {}).get("name") or "").strip()
        if not (S.session and name and caps["consented"] and caps["video"] and S.settings().get("welcome")):
            return {"state": "off"}
        name = " ".join(w[:1].upper() + w[1:] for w in name.split())     # "karthikeya singh" -> "Karthikeya Singh"
        from persona import first_name
        first = first_name(name)
        text = (f"Hi, I'm {name}'s digital twin. Ask me what I think, what I'd do, or how I spend my days, "
                f"and I'll answer just like {first} would. And if I don't know something, I'll tell you honestly. "
                "So, what would you like to know?")
        return {**S.media.submit(text, "en", True, False), "name": name, "full_text": text}

    @api.get("/api/media")
    def media_active():
        return {"jobs": S.media.active()}

    @api.get("/api/media/{jid}")
    def media_status(jid: str):
        v = S.media.view(jid)
        if not v:
            raise HTTPException(404)
        return v

    @api.get("/api/media/{jid}/{kind}")
    def media_file(jid: str, kind: str):
        p = S.media.file(jid, kind)
        if not p:
            raise HTTPException(404)
        return FileResponse(p)

    # ---- prepare demo ----
    @api.post("/api/demo")
    def demo(body: DemoIn):
        need_session()
        caps = S.media_caps()
        if not caps["video"]:
            raise HTTPException(409, caps["why"] or "The avatar needs a voice clip, a photo and the lip-sync model.")
        sharpen = S.settings()["sharpen"] if body.sharpen is None else body.sharpen
        S.start_demo(body.questions, "hi" if body.lang == "hi" else "en", bool(sharpen and caps["sharpen"]))
        return S.demo_status()

    @api.get("/api/demo")
    def demo_status():
        return S.demo_status()

    # ---- settings and data ----
    @api.get("/api/settings")
    def get_settings():
        return S.settings()

    @api.put("/api/settings")
    def put_settings(d: dict):
        return S.save_settings(d)

    @api.post("/api/export")
    def export():
        dest = S.export()
        return {"ok": True, "path": str(dest), "name": dest.name}

    @api.post("/api/reveal")
    def reveal(d: dict):
        import subprocess
        p = Path(d.get("path", ""))
        if p.exists() and p.name.startswith("DeferralTwin-export-"):
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(p)])
            elif sys.platform == "win32":
                subprocess.run(["explorer", f"/select,{p}"])
        return {"ok": True}

    @api.post("/api/redo")
    def redo(body: RedoIn):
        """Start the interview again. The consent, voice and photo stay; memory stays if asked."""
        S.drop_twin()
        if S.p.answers.exists():
            S.p.answers.replace(S.p.twin / "phase2_answers.previous.json")
        try:
            d = json.loads(S.p.draft.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        S.p.draft.write_text(json.dumps({"consent": d.get("consent") or S.consent(), "answers": {}}), encoding="utf-8")
        if not body.keep_memory:
            for name in LOGS:
                (S.p.subject / name).unlink(missing_ok=True)
        S.media_changed()
        return {"ok": True}

    @api.post("/api/delete")
    def delete(body: NameIn):
        c = S.consent() or {}
        if c.get("name") and _norm_name(body.name) != _norm_name(c["name"]):
            raise HTTPException(400, "That isn't the name on the consent record.")
        S.wipe_twin()
        return {"ok": True}

    @api.post("/api/remove-everything")
    def remove_everything(body: NameIn):
        c = S.consent() or {}
        want = c.get("name") or "remove"
        if _norm_name(body.name) != _norm_name(want):
            raise HTTPException(400, "Type the confirmation exactly as shown.")
        import os

        def bye():
            time.sleep(0.6)
            if S.bootstrap_url:
                os._exit(EXIT_REMOVE_EVERYTHING)      # the bootstrapper deletes the folder and says goodbye
            shutil.rmtree(S.p.home, ignore_errors=True)
            os._exit(0)
        threading.Thread(target=bye, daemon=True).start()
        nxt = (S.bootstrap_url.rstrip("/") + "/#goodbye") if S.bootstrap_url else None
        return {"ok": True, "next": nxt, "home": str(S.p.home)}

    # ---- results ----
    @api.get("/api/results")
    def results():
        f = S.p.subject / "phase2_results.json"
        p2 = json.loads(f.read_text(encoding="utf-8"))["summary"] if f.exists() else None
        figs = [x.name for x in sorted(FIGURES.glob("*.png"))] if FIGURES.exists() else []
        try:
            bench = json.loads(S.p.benchmark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bench = None
        return {"phase1": PHASE1, "phase2": p2, "eval": S.eval, "figures": figs, "benchmark": bench}

    @api.post("/api/eval")
    def run_eval():
        need_session()
        if S.eval["state"] == "running":
            return S.eval

        def go():
            try:
                import eval_phase2
                with contextlib.redirect_stdout(io.StringIO()):
                    eval_phase2.main(str(S.p.bench_root), SUBJECT_PID, llm_fn=S.llm_fn)
                S.eval = {"state": "done", "error": None}
            except BaseException as e:
                S.eval = {"state": "error", "error": str(e) or type(e).__name__}
        S.eval = {"state": "running", "error": None}
        threading.Thread(target=go, daemon=True).start()
        return S.eval

    if FIGURES.exists():
        api.mount("/figures", StaticFiles(directory=str(FIGURES)), name="figures")
    api.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    return api


def boot(home=None, llm="auto", multimodal=True, bootstrap_url=None, drive=None):
    """Create the state, and rebuild the twin from disk if there is one: restarting loses nothing."""
    st = State(home, llm=llm, multimodal=multimodal, bootstrap_url=bootstrap_url, drive=drive)
    restored = st.restore_from_drive()
    if restored:
        print("Restored from backup:", ", ".join(restored))
    st.start_llm()
    if st.p.answers.exists():
        print("Interview found, rebuilding the twin in the background.")
        st.start_build()
    return st
