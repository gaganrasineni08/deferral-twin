"""Online mode (Google Gemini): offline fallback, the key never leaks, the deferral gate is unchanged.
No network and no real Keychain: both are replaced with fakes.
Run from the project root:  PYTHONHASHSEED=0 python tests/test_online.py"""
import contextlib
import io
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_app import TestClient, sample_payload, server, wait_build  # noqa: E402  (also installs the stand-ins)
from app import online  # noqa: E402

KEY = "AIzaFAKEtestKEY1234567890abcdefghijk"
calls = {"generate": [], "profile": 0}
vault = {}


def fake_backend(fail=False):
    """Replace the Keychain and Gemini with fakes. fail=True: every Gemini call fails."""
    online.get_key = lambda: vault.get("key")
    online.set_key = lambda k: vault.__setitem__("key", k.strip())
    online.clear_key = lambda: vault.pop("key", None)

    def models(key=None, refresh=False):
        if (key or vault.get("key")) != KEY:
            raise online.Unavailable(online.scrub(f"API key not valid: {key}", key))
        online._models.update(pro="gemini-9.9-pro", flash="gemini-9.9-flash")
        return "gemini-9.9-pro", "gemini-9.9-flash"

    def generate(system, prompt, key=None, temperature=0.7, max_tokens=400, on_text=None):
        calls["generate"].append((system, prompt))
        if fail:
            raise online.Unavailable("quota used up on every model")
        return "I haven't been asked that, but knowing me, I'd go for the cricket match.", "gemini-9.9-pro"

    def build_profile(name, facts, key=None):
        calls["profile"] += 1
        if fail:
            raise online.Unavailable("no internet")
        return {"text": f"I am {name}. I value honesty. I love cricket and biryani.", "model": "gemini-9.9-pro",
                "fingerprint": online.fingerprint(facts), "facts": len(facts), "created": time.time()}
    online.models, online.generate, online.build_profile = models, generate, build_profile


def wait_profile(c):
    for _ in range(200):
        if c.get("/api/online").json()["job"]["state"] != "making":
            return
        time.sleep(0.02)


def built_twin():
    home = Path(tempfile.mkdtemp())
    st = server.boot(home, llm="stub", multimodal=False)
    c = TestClient(server.create_app(st))
    assert c.post("/api/build", json=sample_payload()).status_code == 200
    assert wait_build(c)["state"] == "done"
    return home, st, c


def test_key_never_leaks():
    vault.clear(); fake_backend()
    home, st, c = built_twin()
    out, log = io.StringIO(), io.StringIO()
    handler = logging.StreamHandler(log); logging.getLogger().addHandler(handler)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        bad = c.post("/api/online/key", json={"key": KEY[:-4] + "XXXX"})       # rejected key: not echoed back
        assert bad.status_code == 400 and KEY[:-4] not in bad.text, bad.text
        newer = "AQ.Ab8RN6" + "LwqK3dJ9xFAKE.newerFormat-key_0123456789"               # newer AI Studio keys can contain dots
        bad = c.post("/api/online/key", json={"key": newer})
        assert bad.status_code == 400 and "look like" not in bad.text and newer not in bad.text, bad.text   # reached Google, not echoed
        r = c.post("/api/online/key", json={"key": " " + KEY + "\n"})                     # pasted with stray spaces
        assert r.status_code == 200 and KEY not in r.text and vault["key"] == KEY
        c.put("/api/settings", json={"online": True}); wait_profile(c)
        texts = [r.text, c.get("/api/online").text, c.get("/api/status").text, c.get("/api/settings").text,
                 c.post("/api/ask", json={"question": "Which city is home for you?"}).text]
    logging.getLogger().removeHandler(handler)
    for t in texts + [out.getvalue(), log.getvalue()]:
        assert KEY not in t, "the key leaked"
    for f in home.rglob("*"):                                                  # nor in any file in the app folder
        if f.is_file() and f.stat().st_size < 5_000_000:
            assert KEY.encode() not in f.read_bytes(), f"the key is in {f}"
    assert online.scrub(f"error for {KEY}") == "error for [key]"


def test_online_answers_and_gate_unchanged():
    vault.clear(); fake_backend(); vault["key"] = KEY
    home, st, c = built_twin()
    c.put("/api/settings", json={"online": True}); wait_profile(c)
    assert calls["profile"] >= 1 and c.get("/api/online").json()["profile"]["fresh"]
    calls["generate"].clear()
    a = c.post("/api/ask", json={"question": "Which city is home for you?"}).json()
    assert a["mode"] == "online" and "knowing me" in a["answer"] and a["tier"] in ("confident_answer", "soft_answer")
    system = calls["generate"][-1][0]
    assert "Test" in system and "I value honesty" in system and "digital twin" in system   # name, profile, persona rules
    # the gate: thresholds as before, and an unrelated question is refused without calling Gemini
    rag = st.session.store.cfg["rag"]
    assert rag["deferral_hard_floor"] == 0.15 and rag["deferral_similarity_threshold"] == 0.35
    n = len(calls["generate"])
    r = c.post("/api/ask", json={"question": "zorblax quuxifer xylograph?"}).json()
    assert r["tier"] == "hard_defer" and len(calls["generate"]) == n and r.get("mode") is None
    # identity and the clock stay local templates
    assert c.post("/api/ask", json={"question": "who are you"}).json()["tier"] == "about_twin" and len(calls["generate"]) == n
    # a correction makes the profile stale, so it is written again
    before = calls["profile"]
    c.post("/api/correct", json={"question": "Favourite sport?", "twin_answer": "x", "expected": "Kabaddi, not cricket"})
    wait_profile(c)
    assert calls["profile"] == before + 1


def test_offline_fallback():
    vault.clear(); fake_backend(fail=True); vault["key"] = KEY
    home, st, c = built_twin()
    c.put("/api/settings", json={"online": True}); wait_profile(c)
    a = c.post("/api/ask", json={"question": "Which city is home for you?"}).json()      # no profile yet: offline
    assert a["mode"] == "offline" and a["answer"]
    online.save_profile(st.p.twin, {"text": "I am Test.", "model": "x", "fingerprint": "old", "created": 0})
    a = c.post("/api/ask", json={"question": "Which city is home for you?"}).json()      # Gemini fails: offline
    assert a["mode"] == "offline" and a["answer"] and "knowing me" not in a["answer"]
    vault.clear()                                                                         # no key: plain offline
    a = c.post("/api/ask", json={"question": "Which city is home for you?"}).json()
    assert a.get("mode") is None and a["answer"]


def test_model_choice():
    listed = [("models/gemini-2.0-flash", ["generateContent"]), ("models/gemini-2.5-pro", ["generateContent"]),
              ("models/gemini-3.0-pro-preview", ["generateContent"]), ("models/gemini-3.0-pro", ["generateContent"]),
              ("models/gemini-3.0-pro-image", ["generateContent"]), ("models/gemini-2.5-flash", ["generateContent"]),
              ("models/gemini-2.5-flash-lite", ["generateContent"]), ("models/text-embedding-004", ["embedContent"])]
    assert online.pick_models(listed) == ("gemini-3.0-pro", "gemini-2.5-flash")


def test_fallback_chain():
    """Pro out of quota, the newest Flash busy, a retired model: the next Flash answers, and the skipped
    ones are remembered. Uses the real generate() with a fake client (no network)."""
    import importlib
    real = importlib.reload(online)                      # undo fake_backend's stand-ins
    listed = [(f"models/{n}", ["generateContent"]) for n in
              ("gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
               "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-3.8-flash-image", "gemini-3.5-transcribe")]
    chain = real.pick_chain(listed)
    assert chain == ["gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
                     "gemini-3.5-flash", "gemini-3.5-flash-lite"], chain

    class Err(Exception):
        def __init__(self, code, msg):
            super().__init__(f"{code} {msg}"); self.code = code
    outcome = {"gemini-3.1-pro-preview": Err(429, "RESOURCE_EXHAUSTED quota"), "gemini-3.8-flash": Err(503, "UNAVAILABLE high demand"),
               "gemini-3.7-flash": Err(404, "NOT_FOUND no longer available")}
    tried = []

    class Models:
        def generate_content(self, model, contents, config):
            tried.append(model)
            assert config.thinking_config is not None and config.max_output_tokens > 1000
            if model in outcome:
                raise outcome[model]
            return type("R", (), {"text": "I'd patch it myself."})()
    real._client = lambda key: type("C", (), {"models": Models()})()
    real.get_key = lambda: KEY
    real._models.update(key_hash=__import__("hashlib").sha256(KEY.encode()).hexdigest(), pro=chain[0], flash=chain[1],
                        chain=chain, checked=time.time())
    real._cool.clear()
    text, model = real.generate("sys", "Your cycle is punctured. What do you do?")
    assert (text, model) == ("I'd patch it myself.", "gemini-3.6-flash") and tried == chain[:4], (model, tried)
    assert real._cool["gemini-3.1-pro-preview"] - time.time() > 1700 and real._cool["gemini-3.7-flash"] - time.time() > 80000
    tried.clear()
    real.generate("sys", "again")                        # skipped models aren't tried again straight away
    assert tried == ["gemini-3.6-flash"], tried
    outcome.update({m: Err(503, "UNAVAILABLE") for m in chain})      # everything busy: a clear reason, no key
    real._cool.clear()
    try:
        real.generate("sys", "q"); raise AssertionError("should have failed")
    except real.Unavailable as e:
        assert "busy right now" in str(e) and KEY not in str(e), e


if __name__ == "__main__":
    test_model_choice()
    test_fallback_chain()
    test_key_never_leaks()
    test_online_answers_and_gate_unchanged()
    test_offline_fallback()
    print("online mode checks passed")
