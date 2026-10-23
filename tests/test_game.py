"""'Guess me', work and skills, and changing the photo or voice after the build: no network, stub model.
Run from the project root:  PYTHONHASHSEED=0 python tests/test_game.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app import TestClient, sample_payload, server, wait_build  # noqa: E402  (also installs the stand-ins)


def built():
    home = Path(tempfile.mkdtemp())
    st = server.boot(home, llm="stub", multimodal=False)
    c = TestClient(server.create_app(st))
    assert c.post("/api/build", json=sample_payload()).status_code == 200
    assert wait_build(c)["state"] == "done"
    return st, c


def test_guess_me():
    st, c = built()
    g = c.get("/api/game").json()
    q = g["question"]
    assert q in server.GAME_QUESTIONS and g["stats"]["rounds"] == 0
    # no answer before the guess is locked, and the guess itself isn't shown until the person answers
    assert c.post("/api/game/answer", json={"question": q, "answer": "x"}).status_code == 409
    r = c.post("/api/game/guess", json={"question": q}).json()
    assert r["locked"] and "answer" not in r and "guess" not in r
    assert c.post("/api/game/guess", json={"question": "What is your passport number?"}).status_code == 400
    assert c.post("/api/game/answer", json={"question": q, "answer": "  "}).status_code == 400    # guess kept
    before = c.get("/api/status").json()["memory"]["corrections"]
    a = c.post("/api/game/answer", json={"question": q, "answer": "I'd fix it myself and carry on."}).json()
    assert a["guess"] and a["answer"] == "I'd fix it myself and carry on." and (a["match"] is None or 0 <= a["match"] <= 1)
    assert a["stats"]["rounds"] == 1
    assert c.get("/api/status").json()["memory"]["corrections"] == before + 1                      # remembered
    assert c.get("/api/game").json()["question"] != q                                               # next question
    assert c.post("/api/game/reset").status_code == 200 and c.get("/api/game").json()["stats"]["rounds"] == 0


def test_settings_lists_media():
    st, c = built()
    s = c.get("/api/status").json()
    assert "has_photo" in s and "has_voice" in s and s["built"]


def test_work_and_skills():
    st, c = built()
    assert c.post("/api/work", json={"title": ""}).status_code == 400
    n0 = c.get("/api/status").json()["memory"]["notes"]
    r = c.post("/api/work", json={"title": "Civil engineer", "skills": "concrete design, site supervision, AutoCAD",
                                  "about": "Five years on residential buildings."}).json()
    assert r["work"]["title"] == "Civil engineer" and r["work"]["skills"][0] == "concrete design"
    assert c.get("/api/status").json()["memory"]["notes"] == n0 + 1          # "I work as a civil engineer" is memory
    for _ in range(100):                                                       # topics are written in the background
        if c.get("/api/work").json()["job"]["state"] != "making":
            break
        __import__("time").sleep(0.05)
    # Expert mode is Online-mode only: offline, a field question goes through the usual gate
    off = c.post("/api/ask", json={"question": "Which grade of concrete design mix should a residential slab use?"}).json()
    assert off["tier"] != "field_knowledge", off
    from app import online
    real_gen, real_ready = online.generate, st.online_ready
    online.generate = lambda system, prompt, **kw: ("yes" if system.startswith("You classify") else
                                                     "Use M20 for a house slab; check with a structural engineer.", "gemini-test")
    st.online_ready = lambda: True
    # a field question the person never answered: expert knowledge, labelled, not a refusal
    f = c.post("/api/ask", json={"question": "Which grade of concrete design mix should a residential slab use?"}).json()
    assert f["tier"] == "field_knowledge" and f["field"] == "Civil engineer" and f["answer"], f
    # questions about the person still go through the gate, untouched
    p = c.post("/api/ask", json={"question": "What is your passport number?"}).json()
    assert p["tier"] == "hard_defer", p
    p = c.post("/api/ask", json={"question": "Do you like concrete design?"}).json()
    assert p["tier"] != "field_knowledge", p
    assert st.session.twin.hard_floor == 0.15 and st.session.twin.threshold == 0.35
    # the gate's confidence is reported as it was, not raised
    raw = st.session.store.retrieve("Which grade of concrete design mix should a residential slab use?")[1]
    assert abs(f["confidence"] - raw) < 1e-9
    # files: text and Word are read; nothing breaks on a bad file
    t = c.post("/api/work/upload", files={"file": ("cv.txt", b"Site engineer\n\nAutoCAD, STAAD Pro", "text/plain")}).json()
    assert "STAAD" in t["text"]
    assert c.post("/api/work/upload", files={"file": ("bad.docx", b"not a zip", "application/octet-stream")}).status_code == 400
    assert c.delete("/api/work").status_code == 200 and c.get("/api/work").json()["work"] is None
    # with no work profile, it is picked up from the answers (once, in the background)
    online.generate = lambda system, prompt, **kw: ('{"title": "B.Tech AI student", "skills": ["Python"]}' if "JSON" in prompt
                                                     else "Python\nmachine learning", "gemini-test")
    st.work_auto_tried = False
    st.field_question(st.session, "How do I start with machine learning?")
    for _ in range(100):
        w = c.get("/api/work").json()["work"]
        if w:
            break
        __import__("time").sleep(0.05)
    assert w and w["auto"] and w["title"] == "B.Tech AI student", w
    online.generate, st.online_ready = real_gen, real_ready


if __name__ == "__main__":
    test_guess_me()
    test_work_and_skills()
    test_settings_lists_media()
    print("guess me and work checks passed")
