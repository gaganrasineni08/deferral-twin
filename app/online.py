"""
Optional Online mode: the twin answers with Google Gemini (off by default).

Everything else stays on the Mac. In online mode, and only then, the twin's interview answers,
corrections and notes (once, to write the profile) and each question (with the profile and the
closest real answers) are sent to Gemini. Voice and video stay local.

The API key is stored in the macOS Keychain (via `keyring`), never in a file, a log, a response
or an error message. Any failure -- no key, no internet, quota used up, a bad answer -- raises
Unavailable, and the caller answers offline instead.
"""
import hashlib
import json
import re
import threading
import time
from pathlib import Path

SERVICE, ACCOUNT = "DeferralTwin", "gemini-api-key"
PROFILE_FILE = "online_profile.json"
TIMEOUT_S = 40


class Unavailable(Exception):
    """Online mode can't answer right now; answer offline."""


# ---------- the key: macOS Keychain only ----------
def get_key():
    try:
        import keyring
        return keyring.get_password(SERVICE, ACCOUNT) or None
    except Exception:
        return None


def set_key(key):
    import keyring
    keyring.set_password(SERVICE, ACCOUNT, key.strip())


def clear_key():
    try:
        import keyring
        keyring.delete_password(SERVICE, ACCOUNT)
    except Exception:
        pass


def scrub(text, key=None):
    """Remove the key (and anything key-shaped) from text before it is logged or returned."""
    text = str(text or "")
    key = key or get_key()
    if key:
        text = text.replace(key, "[key]")
    return re.sub(r"(AIza[0-9A-Za-z_\-]{20,}|AQ\.[0-9A-Za-z_\-.]{20,})", "[key]", text)


# ---------- models: asked of the API, never guessed ----------
_models = {"key_hash": None, "pro": None, "flash": None, "chain": [], "checked": 0.0}
_cool = {}                                   # model -> time until which it is skipped (quota used up, overloaded)
_lock = threading.Lock()


def _client(key):
    from google import genai
    from google.genai import types
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_S * 1000))


def _version(name):
    """'models/gemini-2.5-pro' -> (2, 5); unknown -> (0,)."""
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0,)


def pick_models(names_actions):
    """[(name, actions)] -> (newest Pro, newest Flash) that can generate text. Stable names beat
    previews of the same version; specialised variants (image, tts, live, embedding) are skipped."""
    ok = []
    for name, actions in names_actions:
        n = name.split("/")[-1]
        if "generateContent" not in (actions or []) or not n.startswith("gemini-"):
            continue
        if re.search(r"image|tts|audio|live|embed|vision|thinking-exp|learnlm|robotics|computer-use", n):
            continue
        ok.append(n)
    rank = lambda n: (_version(n), "preview" not in n and "exp" not in n, -len(n))
    pro = sorted([n for n in ok if "-pro" in n], key=rank, reverse=True)
    flash = sorted([n for n in ok if "-flash" in n and "lite" not in n], key=rank, reverse=True) or \
        sorted([n for n in ok if "-flash" in n], key=rank, reverse=True)
    return (pro[0] if pro else None), (flash[0] if flash else None)


def pick_chain(names_actions):
    """The models to try, in order: the newest Pro, the four newest Flash models (each its own serving pool,
    so one is often free when another is busy), then the newest Flash-Lite."""
    pro, flash = pick_models(names_actions)
    ok = [n.split("/")[-1] for n, a in names_actions if "generateContent" in (a or [])]
    rank = lambda n: (_version(n), "preview" not in n, -len(n))
    plain = lambda n: re.fullmatch(r"gemini-\d+(\.\d+)?-flash(-preview)?", n)
    flashes = sorted([n for n in ok if plain(n)], key=rank, reverse=True)
    lite = sorted([n for n in ok if re.fullmatch(r"gemini-\d+(\.\d+)?-flash-lite", n)], key=rank, reverse=True)[:1]
    chain = []
    for n in [pro, flash] + flashes[:4] + lite:
        if n and n not in chain:
            chain.append(n)
    return chain


def models(key=None, refresh=False):
    key = key or get_key()
    if not key:
        raise Unavailable("no key")
    kh = hashlib.sha256(key.encode()).hexdigest()
    with _lock:
        if not refresh and _models["key_hash"] == kh and (_models["pro"] or _models["flash"]) \
                and time.time() - _models["checked"] < 6 * 3600:
            return _models["pro"], _models["flash"]
    try:
        client = _client(key)                       # keep a reference: the list is fetched lazily
        listed = [(m.name, list(getattr(m, "supported_actions", None) or [])) for m in client.models.list()]
    except Exception as e:
        raise Unavailable(scrub(f"{type(e).__name__}: {e}", key)[:300]) from None
    pro, flash = pick_models(listed)
    if not (pro or flash):
        raise Unavailable("this key can't use any Gemini text model")
    with _lock:
        _models.update(key_hash=kh, pro=pro, flash=flash, chain=pick_chain(listed) or [m for m in (pro, flash) if m],
                       checked=time.time())
    return pro, flash


def _quota(e):
    s = str(e)
    return getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in s or "quota" in s.lower()


def _busy(e):
    """Overloaded, a server error or a timeout: worth trying the next model."""
    s = str(e)
    return getattr(e, "code", None) in (500, 502, 503, 504) or any(
        k in s for k in ("UNAVAILABLE", "high demand", "overloaded", "DEADLINE_EXCEEDED", "INTERNAL")) or \
        "timeout" in type(e).__name__.lower() or "timed out" in s.lower()


def _short(e):
    """A short, readable reason for the badge: '429 quota used up', '503 busy'..."""
    s = str(e)
    if _quota(e):
        return "quota used up"
    if "high demand" in s or "UNAVAILABLE" in s or "overloaded" in s:
        return "busy right now"
    if "timeout" in type(e).__name__.lower() or "timed out" in s.lower() or "DEADLINE" in s:
        return "took too long"
    m = re.search(r"\b(\d{3}) ([A-Z_]+)", s)
    return f"{m.group(1)} {m.group(2).lower().replace('_', ' ')}" if m else type(e).__name__


def _thinking(types, model):
    """Gemini 3 and later think before answering, and the thinking counts against the output limit:
    keep it low (2.x: a small budget) so the answer itself isn't cut short."""
    if _version(model) >= (3,):
        return types.ThinkingConfig(thinking_level="low")
    return types.ThinkingConfig(thinking_budget=0 if "flash" in model else 256)


def generate(system, prompt, key=None, temperature=0.7, max_tokens=400, on_text=None):
    """Text from the first model in the chain that answers (newest Pro, then Flash models). A model out of
    quota is skipped for 30 minutes, a retired one (404) for a day, an overloaded one for a minute; when
    every model is busy, it waits 2 s and goes round once more. Returns (text, model)."""
    key = key or get_key()
    if not key:
        raise Unavailable("no key")
    models(key)
    from google.genai import types
    chain = list(_models["chain"]) or [m for m in (_models["pro"], _models["flash"]) if m]
    reasons, shown = [], []

    def one(model):
        """(text or None, kind of failure or None)."""
        for think in (True, False):                 # retry once without the thinking setting if a model rejects it
            cfg = types.GenerateContentConfig(system_instruction=system, temperature=temperature,
                                              max_output_tokens=max_tokens + 1200,      # room for the (low) thinking
                                              thinking_config=_thinking(types, model) if think else None)
            try:
                client = _client(key)
                if on_text:
                    for chunk in client.models.generate_content_stream(model=model, contents=prompt, config=cfg):
                        t = chunk.text or ""
                        if t:
                            shown.append(t)
                            on_text(t)
                    text = "".join(shown)
                else:
                    text = client.models.generate_content(model=model, contents=prompt, config=cfg).text or ""
                if text.strip():
                    return text.strip(), None
                reasons.append(f"{model}: empty answer")
                return None, "empty"
            except Exception as e:
                msg = scrub(f"{type(e).__name__}: {e}", key)
                if think and not shown and "thinking" in msg.lower() and ("400" in msg or "INVALID" in msg):
                    continue
                reasons.append(f"{model}: {_short(e)}")
                if _quota(e):
                    _cool[model] = time.time() + 1800
                    return None, "quota"
                if _busy(e):
                    _cool[model] = time.time() + 60
                    return None, "busy"
                if getattr(e, "code", None) == 404 or "NOT_FOUND" in msg:
                    _cool[model] = time.time() + 86400
                    return None, "gone"
                raise Unavailable("; ".join(reasons) + " (" + msg[:200] + ")") from None
        return None, "empty"

    order = [m for m in chain if _cool.get(m, 0) <= time.time()] or chain[-1:]
    kinds = []
    for attempt in (0, 1):
        if attempt:                                 # every model that could answer was busy: one more round
            if "busy" not in kinds or any(k in ("empty",) for k in kinds):
                break
            time.sleep(2.0)
            order = [m for m in chain if _cool.get(m, 0) - time.time() < 61 and m != _models["pro"]]
        for model in order:
            text, kind = one(model)
            if text:
                return text, model
            kinds.append(kind)
            if shown:                               # part of an answer is already on screen: don't start another
                raise Unavailable("; ".join(reasons))
    raise Unavailable("; ".join(reasons) or "no model answered")


# ---------- the profile: written once from everything the twin knows, kept until that changes ----------
def fingerprint(facts):
    h = hashlib.sha256()
    for f in facts:
        if not f.get("superseded"):
            h.update((f.get("source", "") + "\x00" + f.get("text", "") + "\x01").encode("utf-8"))
    return h.hexdigest()


PROFILE_ASK = """Below is everything {name} told their digital twin: interview answers (question and answer),
corrections they made, and notes they added. Corrections and notes override the interview.

Write {name}'s profile in the first person, as {name} describing themself, under these headings:
Values; Personality; Habits and daily life; How I decide; Likes and dislikes; How I talk (tone, length,
slang, spelling, lowercase or not); How I typically react (stress, conflict, praise, setbacks, people).
Base every line on what they said. Where something is an inference, keep it modest. Don't invent names,
places, numbers or events. About 350 to 500 words, plain text with the headings.

{facts}"""


def build_profile(name, facts, key=None):
    lines = []
    for f in facts:
        if f.get("superseded"):
            continue
        tag = {"user_correction": "[correction] ", "user_note": "[note] "}.get(f.get("source"), "")
        lines.append(tag + f["text"].replace("\n", " | "))
    text, model = generate("You write accurate, grounded first-person profiles.",
                           PROFILE_ASK.format(name=name, facts="\n".join(lines)), key=key, temperature=0.3, max_tokens=3000)
    return {"text": text, "model": model, "fingerprint": fingerprint(facts), "facts": len(lines), "created": time.time()}


def load_profile(folder):
    p = Path(folder) / PROFILE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_profile(folder, prof):
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / PROFILE_FILE).write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- answering ----------
ONLINE_RULES = """ You are thinking as {name}. Use the profile and their real answers below. You may reason about
situations they were never asked about, in character, the way {name} would. When your answer goes beyond what
they actually said, say so naturally in their voice (for example "I haven't been asked that, but knowing me...").
Never invent specific facts about their life (names, places, numbers, dates, possessions, what they did on a day),
and don't claim skills, subjects, knowledge or experiences they never mentioned; for those say you aren't sure, or
reason from what they did say ("I've never said, but I'd probably..."). If the question is a statement to rate, answer with one of: Strongly disagree (1 of 5),
Disagree (2 of 5), Neutral (3 of 5), Agree (4 of 5), Strongly agree (5 of 5). If it is an either-or question, choose one.
It is now {now}."""


def system_prompt(name, persona_rules, profile_text, retrieved, now):
    real = "\n".join(f"- {r['text'].replace(chr(10), ' | ')}" for r in retrieved[:6])
    return (f"You are {name}'s digital twin." + persona_rules + ONLINE_RULES.format(name=name, now=now) +
            f"\n\n{name}'s profile, in their own words:\n{profile_text}\n\nTheir closest real answers to this question:\n{real}")
