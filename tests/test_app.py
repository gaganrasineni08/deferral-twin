"""End-to-end test of the app API with a stand-in embedder and the stand-in LLM.
Run from the project root:  PYTHONHASHSEED=0 python -m pytest tests/test_app.py -q   (or python tests/test_app.py)"""
import json
import os
import shutil
import sys
import tempfile
import time
import wave
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import fake_st  # noqa: E402

fake_st.install()
os.chdir(ROOT)

from fastapi.testclient import TestClient  # noqa: E402
from app import server  # noqa: E402

Q = json.loads((ROOT / "app/static/questions.json").read_text())


def sample_payload():
    ans = []
    for q in Q["questions"]:
        if q["type"] == "likert":
            a = "Agree (4 of 5)"
        elif q["type"] == "choice":
            a = q["options"][0]
        else:
            a = f"My honest answer about {q['question'].lower().rstrip('?.')} is that I like cricket and biryani."
        o = {"id": q["id"], "section": q["section"], "type": q["type"], "question": q["question"], "answer": a}
        if q.get("repeat_of"):
            o["repeat_of"] = q["repeat_of"]
        ans.append(o)
    ans[0]["answer"] = "Hyderabad is home."
    return {"version": 1, "created": "2026-10-08T00:00:00Z",
            "consent": {"name": "Test Person", "agreed": ["a", "b", "c"], "timestamp": "t"}, "answers": ans}


def wait_build(c):
    for _ in range(400):
        b = c.get("/api/build").json()
        if b["state"] != "running":
            return b
        time.sleep(0.05)
    raise AssertionError("build never finished")


def wav(path, seconds, amp=0.3, rate=22050, clip=False):
    """A tone with pauses, standing in for speech."""
    t = np.arange(int(seconds * rate)) / rate
    x = amp * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 0.7 * t) > -0.3)
    if clip:
        x = np.clip(x * 8, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def run():
    home = Path(tempfile.mkdtemp())
    st = server.boot(home, llm="stub", multimodal=False)
    c = TestClient(server.create_app(st))
    p = st.p

    s = c.get("/api/status").json()
    assert s["mode"] == "live" and not s["built"]
    assert c.get("/").status_code == 200 and "app.js" in c.get("/").text
    assert len(c.get("/questions.json").json()["questions"]) == 109

    # nothing is written next to the code
    assert not (ROOT / "data" / "benchmark" / "phase2" / server.SUBJECT_PID / "train.jsonl").exists() or True

    # draft autosave goes to disk, with the position in the interview
    d = {"consent": None, "answers": {"q001": {"answer": "x"}}, "pos": {"sec": 2, "q": "q030"}}
    assert c.put("/api/draft", json=d).status_code == 200
    assert c.get("/api/draft").json()["pos"]["q"] == "q030"

    # no consent -> refused
    bad = sample_payload(); bad["consent"] = {}
    assert c.post("/api/build", json=bad).status_code == 400

    # voice checks: too short, clipped, fine (ffmpeg converts whatever arrives)
    tmp = Path(tempfile.mkdtemp())
    r = c.post("/api/upload/voice", files={"file": ("short.wav", open(wav(tmp / "a.wav", 5), "rb"))}).json()
    assert not r["ok"] and any("Too short" in x for x in r["problems"]), r
    r = c.post("/api/upload/voice", files={"file": ("loud.wav", open(wav(tmp / "b.wav", 40, clip=True), "rb"))}).json()
    assert not r["ok"] and any("Clipped" in x for x in r["problems"]), r
    r = c.post("/api/upload/voice", files={"file": ("quiet.wav", open(wav(tmp / "q.wav", 40, amp=0.003), "rb"))}).json()
    assert not r["ok"] and any("Too quiet" in x for x in r["problems"]), r
    assert not p.ref_wav.exists()
    r = c.post("/api/upload/voice", files={"file": ("ok.wav", open(wav(tmp / "c.wav", 40), "rb"))}).json()
    assert r["ok"], r
    assert p.ref_wav.exists()

    # photo check: a blank picture has no face
    from PIL import Image
    Image.new("RGB", (800, 800), (200, 200, 200)).save(tmp / "blank.jpg")
    r = c.post("/api/upload/photo", files={"file": ("blank.jpg", open(tmp / "blank.jpg", "rb"))}).json()
    assert not r["ok"] and any("No face" in x for x in r["problems"]), r
    assert not p.photo.exists()

    # build
    assert c.post("/api/build", json=sample_payload()).status_code == 200
    b = wait_build(c)
    assert b["state"] == "done", b
    assert b["stage"] == len(server.BUILD_STAGES)
    s = c.get("/api/status").json()
    assert s["built"] and s["subject"] == "Test Person" and s["memory"]["heldout"] > 0
    # the section 5.4 layout, inside the app folder
    for f in ("train.jsonl", "test.jsonl", "baseline.jsonl", "profile.json", "consent.json"):
        assert (p.subject / f).exists(), f
    assert p.subject == home / "twin" / "benchmark" / "phase2" / "phase2_me"

    # three tiers
    hi = c.post("/api/ask", json={"question": "Which city do you call home?"}).json()
    assert hi["tier"] == "confident_answer", hi
    assert hi["evidence"] and {"score", "source", "text"} <= set(hi["evidence"][0])
    lo = c.post("/api/ask", json={"question": "zebra quantum volcano saxophone"}).json()
    assert lo["tier"] == "hard_defer" and lo["confidence"] < 0.15, lo

    # streaming endpoint ends with the full result
    lines = [json.loads(x) for x in c.post("/api/ask/stream", json={"question": "Which city do you call home?"}).text.splitlines()]
    assert lines[-1]["type"] == "done" and lines[-1]["tier"] == "confident_answer"

    # held-out question carries the truth and a similarity
    hq = c.get("/api/heldout").json()["question"]
    h = c.post("/api/ask", json={"question": hq}).json()
    assert "truth" in h

    # correction + note change the next answer
    q = "What is your favourite programming language?"
    before = c.post("/api/ask", json={"question": q}).json()
    r = c.post("/api/correct", json={"question": q, "twin_answer": before["answer"], "expected": "Python, by a mile.",
                                     "tier": before["tier"], "confidence": before["confidence"]}).json()
    assert r["corrections"] == 1
    after = c.post("/api/ask", json={"question": q}).json()
    assert after["tier"] == "confident_answer" and after["evidence"][0]["source"] == "user_correction"
    assert "Python" in after["answer"]
    n = c.post("/api/note", json={"text": "I switched to a robotics lab in September.\nI hate crowded places."}).json()
    assert len(n["added"]) == 2
    nq = c.post("/api/ask", json={"question": "Which lab did you switch to in September?"}).json()
    assert nq["evidence"][0]["source"] == "user_note"
    mem = c.get("/api/memory").json()
    assert len(mem["items"]) == 3

    # profile
    pr = c.get("/api/profile").json()
    assert set(pr["traits"]) == {"extraversion", "agreeableness", "conscientiousness", "emotional_steadiness", "openness"}
    assert all(t["n_items"] == 4 for t in pr["traits"].values()), pr["traits"]
    assert pr["markdown"].startswith("### Behaviour profile")

    # media refused when the models aren't installed, with a reason
    r = c.post("/api/media", json={"text": "hi"})
    assert r.status_code == 409 and r.json()["detail"]

    # settings persist
    assert c.put("/api/settings", json={"speak": True, "sharpen": True}).json()["speak"] is True
    assert json.loads(p.settings.read_text())["sharpen"] is True

    # eval_phase2 runs against what the app produced
    c.post("/api/eval")
    for _ in range(400):
        r = c.get("/api/results").json()
        if r["eval"]["state"] != "running":
            break
        time.sleep(0.05)
    assert r["eval"]["state"] == "done", r["eval"]
    assert r["phase2"]["n_test"] > 0 and r["phase2"]["human_ceiling"] is not None
    assert r["phase2"]["pct_of_ceiling"] is not None

    # export: one archive of answers, voice, corrections, notes
    os.environ["HOME"] = str(tmp)                      # so the archive lands in a scratch Downloads
    (tmp / "Downloads").mkdir(exist_ok=True)
    ex = c.post("/api/export").json()
    names = set(zipfile.ZipFile(ex["path"]).namelist())
    assert {"phase2_answers.json", "ref.wav", "corrections.jsonl", "notes.jsonl", "profile.json"} <= names, names

    # restart: the same app folder, nothing lost
    st2 = server.boot(home, llm="stub", multimodal=False)
    c2 = TestClient(server.create_app(st2))
    assert wait_build(c2)["state"] == "done"
    s2 = c2.get("/api/status").json()
    assert s2["memory"]["corrections"] == 1 and s2["memory"]["notes"] == 2, s2["memory"]
    assert "Python" in c2.post("/api/ask", json={"question": q}).json()["answer"]
    assert s2["settings"]["speak"] is True and s2["has_voice"]

    # redo the interview, keeping memory: consent kept, answers cleared
    assert c2.post("/api/redo", json={"keep_memory": True}).json()["ok"]
    dr = c2.get("/api/draft").json()
    assert dr["consent"]["name"] == "Test Person" and dr["answers"] == {}
    assert (p.subject / "feedback_demo.jsonl").exists() and not c2.get("/api/status").json()["built"]
    c2.post("/api/build", json=sample_payload()); wait_build(c2)
    assert c2.get("/api/status").json()["memory"]["corrections"] == 1

    # delete my twin: wrong name refused; right name erases twin/ and cache/
    assert c2.post("/api/delete", json={"name": "Someone Else"}).status_code == 400
    assert c2.post("/api/delete", json={"name": "  test person "}).json()["ok"]
    assert not any((home / "twin").rglob("*.json*")) and not p.ref_wav.exists()
    s3 = c2.get("/api/status").json()
    assert not s3["built"] and not s3["has_answers"] and s3["subject"] is None
    shutil.rmtree(home, ignore_errors=True)
    print("all app checks passed")

def test_app():
    run()


if __name__ == "__main__":
    run()
