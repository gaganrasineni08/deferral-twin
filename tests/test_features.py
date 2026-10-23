"""Chat memory, Telugu/Hindi, "What would I do?", the Big Five snapshot and Talk mode: no network, stub model.
Run from the project root:  PYTHONHASHSEED=0 python tests/test_features.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_game import built  # noqa: E402
from app import lang, online  # noqa: E402


def test_language_detection():
    assert lang.detect("Which city is home?") == "en"
    assert lang.detect("आपका घर कहाँ है?") == "hi"
    assert lang.detect("మీ ఊరు ఏది?") == "te"
    assert lang.is_follow_up("why?", [{"q": "Tea or coffee?", "a": "Tea"}])
    assert not lang.is_follow_up("Which city do you call home?", [{"q": "Tea or coffee?", "a": "Tea"}])
    assert not lang.is_follow_up("why?", [])


def test_chat_memory_offline():
    st, c = built()
    h = [{"q": "Tea or coffee?", "a": "Tea"}]
    r = c.post("/api/ask", json={"question": "why that one?", "history": h}).json()
    assert r["asked_as"].startswith("Tea or coffee?"), r          # the follow-up leans on the earlier question
    r2 = c.post("/api/ask", json={"question": "Tea or coffee?"}).json()
    assert "asked_as" not in r2 and r2["lang"] == "en"
    assert st.session.twin.hard_floor == 0.15 and st.session.twin.threshold == 0.35


def test_telugu_and_hindi():
    st, c = built()
    # offline with no translator installed: an honest note, never a crash
    r = c.post("/api/ask", json={"question": "మీకు టీ ఇష్టమా లేక కాఫీ ఇష్టమా?"}).json()
    assert r["lang"] == "te" and r.get("lang_note"), r
    # online: Gemini makes it one English question, and the answer comes back in Telugu
    real_gen, real_ready = online.generate, st.online_ready
    seen = []

    def fake(system, prompt, **kw):
        seen.append(system[:40])
        if system.startswith("Rewrite"):
            return "Tea or coffee?", "gemini-test"
        if system.startswith("Translate"):
            return "నాకు టీ ఇష్టం.", "gemini-test"
        return "Tea, always.", "gemini-test"
    online.generate, st.online_ready = fake, (lambda: True)
    try:
        r = c.post("/api/ask", json={"question": "మీకు టీ ఇష్టమా లేక కాఫీ ఇష్టమా?"}).json()
        assert r["lang"] == "te" and r["asked_as"] == "Tea or coffee?" and lang.detect(r["answer"]) == "te", r
        assert r.get("answer_en")
        p = c.post("/api/ask", json={"question": "आपका पासपोर्ट नंबर क्या है?"}).json()
        assert p["lang"] == "hi"
    finally:
        online.generate, st.online_ready = real_gen, real_ready


def test_decide():
    st, c = built()
    assert c.post("/api/decide", json={"situation": "x", "options": ["a"]}).status_code == 400
    r = c.post("/api/decide", json={"situation": "I have a free weekend and want to relax.",
                                    "options": ["Watch cricket with friends", "Plan every detail of a trip"]}).json()
    assert len(r["support"]) == 2 and all(0 <= s["score"] <= 1 for s in r["support"])
    if r["confidence"] < 0.15:
        assert r["choice"] is None and r["tier"] == "hard_defer"             # it says it doesn't know
    else:
        assert r["choice"] in (0, 1) and r["reasons"] and r["certainty"] in ("high", "medium", "low")
        if r["confidence"] < 0.35:
            assert r["certainty"] != "high"
    n = c.post("/api/decide", json={"situation": "Which passport office should renew my visa stamp?",
                                    "options": ["Office 7", "Office 9"]}).json()
    assert n["confidence"] >= 0.15 or n["choice"] is None


def test_bigfive():
    st, c = built()
    r = c.get("/api/bigfive").json()
    assert [t["trait"] for t in r["traits"]] == ["Openness", "Conscientiousness", "Extraversion", "Agreeableness", "Neuroticism"]
    for t in r["traits"]:
        assert t["items"] == 4 and 0 <= t["score"] <= 100
    by = {t["trait"]: t["score"] for t in r["traits"]}
    assert by["Openness"] == 75                       # "Agree" to all four, none reversed
    assert by["Extraversion"] == 50                   # two reversed of four: (4 + 2 + 2 + 4) / 4 -> 3 -> 50


def test_talk_needs_model():
    st, c = built()
    assert c.get("/api/status").json()["talk"] is False
    assert c.post("/api/transcribe", files={"file": ("a.webm", b"xx", "audio/webm")}).status_code == 409


if __name__ == "__main__":
    test_language_detection()
    test_chat_memory_offline()
    test_telugu_and_hindi()
    test_decide()
    test_bigfive()
    test_talk_needs_model()
    print("feature checks passed")
