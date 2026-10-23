"""Deferral honesty and the voice/avatar job queue, without any real model.
Run from the project root:  python tests/test_gate_and_media.py   (or pytest)"""
import json
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import fake_st  # noqa: E402

fake_st.install()

import numpy as np  # noqa: E402


def test_boost_never_reaches_the_gate():
    """HANDOFF.md section 2, item 3: person-stated facts rank higher, but the
    confidence that drives the three tiers stays the raw cosine similarity."""
    import rag_pipeline
    d = Path(tempfile.mkdtemp())
    facts = d / "facts.jsonl"
    facts.write_text("\n".join(json.dumps({"text": t, "source": "interview"}) for t in
                               ["Q: Which city is home?\nA: Hyderabad", "Q: Favourite food?\nA: Biryani"]))
    cfg = rag_pipeline.load_config()
    cfg["paths"] = dict(cfg["paths"], qa_facts_file=str(facts))
    cfg["rag"] = dict(cfg["rag"], index_path=str(d / "i.bin"), correction_boost=0.05)
    st = rag_pipeline.RagStore(cfg)
    st.build()
    st.load()
    q = "robotics lab september switched"
    _, before = st.retrieve(q)
    st.add_fact("I switched to a robotics lab in September and love it.", source="user_note")
    res, conf = st.retrieve(q)
    emb = st.embedder.encode([q, res[0]["text"]], normalize_embeddings=True)
    raw = float(np.dot(emb[0], emb[1]))
    assert res[0]["source"] == "user_note"
    assert abs(conf - raw) < 1e-5 and abs(res[0]["score"] - raw) < 1e-5, (conf, raw)
    assert before < 0.15                                  # it was a refusal before the note


def _wav(path, secs=2.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(np.zeros(int(16000 * secs), "<i2").tobytes())


class FakeMM:
    def __init__(self, video_secs=1.0):
        self.video_secs, self.spoken, self.rendered = video_secs, 0, 0

    def speak(self, text, lang="en", out_path=None):
        _wav(out_path, 2.0)
        self.spoken += 1
        return out_path

    def animate(self, wav, out_path=None, sharpen=False, on_stage=None):
        on_stage and on_stage("lipsync")
        time.sleep(self.video_secs)
        Path(out_path).write_bytes(b"\0" * 2000)
        self.rendered += 1
        return out_path


def _wait(svc, jid, states=("done", "error"), timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = svc.view(jid)
        if v["state"] in states:
            return v
        time.sleep(0.02)
    raise AssertionError(f"job {jid} stuck in {svc.view(jid)['state']}")


def test_video_never_blocks_voice_and_clips_are_cached():
    from app.media import MediaService
    from app.paths import Paths
    p = Paths(tempfile.mkdtemp()).ensure()
    p.benchmark.write_text(json.dumps({"source": "benchmark", "voice": {"rtf": 1.5},
                                       "lipsync": {"per_audio_second": 12.0}}))
    mm = FakeMM(video_secs=1.5)
    svc = MediaService(p, lambda: mm)
    a = svc.submit("First answer, with a video.", "en", video=True)
    _wait(svc, a["id"], states=("video",))
    v = svc.view(a["id"])
    assert v["estimate_s"] == 24 and "remaining_s" in v          # 12 s per second of speech x 2 s, from the benchmark
    b = svc.submit("Second answer, voice only.", "en", video=False)
    vb = _wait(svc, b["id"])
    assert vb["state"] == "done" and vb["audio"]
    assert svc.view(a["id"])["state"] == "video"                 # the render is still going; voice did not wait
    assert _wait(svc, a["id"])["video"]
    again = svc.submit("First answer, with a video.", "en", video=True)
    assert again["state"] == "done" and again["cached"] and mm.rendered == 1 and mm.spoken == 2
    sharp = svc.submit("First answer, with a video.", "en", video=True, sharpen=True)
    assert sharp["state"] != "done"                              # sharpened clips are cached separately
    assert svc.est.summary()["video_seconds"] == round(12.0 * 40 / 2.6)


def test_latest_correction_wins():
    """Correcting the same question again replaces the earlier correction, also when the log is replayed
    on start-up; the gate still sees only raw similarity."""
    import rag_pipeline
    d = Path(tempfile.mkdtemp())
    facts = d / "facts.jsonl"
    facts.write_text(json.dumps({"text": "Q: Favourite food?\nA: Biryani", "source": "interview"}))
    cfg = rag_pipeline.load_config()
    cfg["paths"] = dict(cfg["paths"], qa_facts_file=str(facts))
    cfg["rag"] = dict(cfg["rag"], index_path=str(d / "i.bin"))
    st = rag_pipeline.RagStore(cfg)
    st.build()
    st.load()
    st.add_fact("Q: What can you do?\nA: i am not an ai", source="user_correction", persist=False)
    st.add_fact("Q: what can you do\nA: I build AI projects", source="user_correction", persist=False)
    res, conf = st.retrieve("What can you do?")
    texts = [r["text"] for r in res]
    assert any("I build AI projects" in t for t in texts) and not any("not an ai" in t for t in texts), texts
    emb = st.embedder.encode(["What can you do?", res[0]["text"]], normalize_embeddings=True)
    assert abs(conf - float(np.dot(emb[0], emb[1]))) < 1e-5


def test_unmeasured_mac_gives_no_estimate():
    from app.media import Estimates
    assert Estimates(Path(tempfile.mkdtemp()) / "none.json").video_seconds(10, False) is None


if __name__ == "__main__":
    test_boost_never_reaches_the_gate()
    test_video_never_blocks_voice_and_clips_are_cached()
    test_latest_correction_wins()
    test_unmeasured_mac_gives_no_estimate()
    print("gate and media checks passed")
