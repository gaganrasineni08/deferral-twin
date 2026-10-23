"""
Behaviour profile: turns interview answers into a structured model of HOW the
person is, not just WHAT they said.

Why this exists
---------------
Retrieval alone can only answer questions close to something the person
actually answered. A question like "your flight is cancelled, what do you do?"
may match nothing stored -- but the person's reaction pattern is knowable from
how they answered OTHER situations, plus their trait scores. This module builds
that profile so the twin can reason by analogy on the middle tier, while the
answer stays explicitly marked as an inference rather than something they said.

  profile = build_profile(answers)      # answers = the interview JSON "answers" list
  profile.narrative()                   # prompt text for the LLM
  profile.as_markdown()                 # human-readable, shown in the demo UI

Scoring is deterministic (no model guessing personality), so the profile is
reproducible and can be reported in the write-up.
"""
import json
import re
from dataclasses import dataclass, field, asdict

# --- Likert item -> (trait, direction). Keyed on a distinctive fragment of the
# question text, so renumbering the questionnaire does not break the mapping. ---
TRAIT_ITEMS = {
    "start conversations with new people": ("extraversion", +1),
    "long social events leave me drained": ("extraversion", -1),
    "tend to stay quiet in groups": ("extraversion", -1),
    "centre of attention": ("extraversion", +1),
    "other people's point of view": ("agreeableness", +1),
    "irritated quickly by other people": ("agreeableness", -1),
    "avoid arguments to keep the peace": ("agreeableness", +1),
    "help others even when it is inconvenient": ("agreeableness", +1),
    "keep my things organised": ("conscientiousness", +1),
    "leave tasks until the last minute": ("conscientiousness", -1),
    "postpone tasks until the deadline": ("conscientiousness", -1),
    "follow through on what i promise": ("conscientiousness", +1),
    "set goals and track my progress": ("conscientiousness", +1),
    "deadlines stress me out": ("emotional_steadiness", -1),
    "stay calm when things go wrong": ("emotional_steadiness", +1),
    "worry about things i cannot control": ("emotional_steadiness", -1),
    "mood changes quickly": ("emotional_steadiness", -1),
    "trying unfamiliar things": ("openness", +1),
    "new experiences appeal to me": ("openness", +1),
    "abstract or theoretical ideas": ("openness", +1),
    "routine bores me": ("openness", +1),
    "curious about how things work": ("openness", +1),
}

TRAIT_POLES = {
    "extraversion": ("reserved, keeps to a small circle", "outgoing, energised by people"),
    "agreeableness": ("blunt, will push back", "accommodating, keeps the peace"),
    "conscientiousness": ("flexible, works in bursts", "organised, plans and follows through"),
    "emotional_steadiness": ("reacts strongly under pressure", "steady under pressure"),
    "openness": ("prefers the familiar and concrete", "drawn to new and abstract things"),
}

# Sections of the questionnaire, by the labels the interview page writes.
SEC_SITUATION = "What would you do?"
SEC_BELIEF = "What you believe"
SEC_EITHEROR = "Either or"
SEC_OPEN = "In your own words"
SEC_ABOUT = "About you"
SEC_PREFS = "Everyday preferences"

LIKERT_RE = re.compile(r"\((\d)\s+of\s+5\)")


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())


def likert_value(ans):
    """'Agree (4 of 5)' -> 4. Returns None if this is not a Likert answer."""
    m = LIKERT_RE.search(ans or "")
    return int(m.group(1)) if m else None


def _band(score):
    if score >= 70:
        return "high"
    if score >= 55:
        return "moderately high"
    if score > 45:
        return "mixed"
    if score > 30:
        return "moderately low"
    return "low"


@dataclass
class Profile:
    name: str = "this person"
    traits: dict = field(default_factory=dict)       # trait -> {score, band, n_items}
    style: list = field(default_factory=list)        # decided either/or dispositions
    values: list = field(default_factory=list)       # strongly held beliefs
    precedents: list = field(default_factory=list)   # (situation, what they did)
    basics: list = field(default_factory=list)       # identity / everyday facts
    n_answers: int = 0

    # ---- rendering ----
    def trait_lines(self):
        out = []
        for t, d in self.traits.items():
            low, high = TRAIT_POLES[t]
            pole = high if d["score"] >= 50 else low
            out.append(f"{t.replace('_', ' ')}: {d['band']} ({d['score']}/100) -- {pole}")
        return out

    def narrative(self, max_precedents=6):
        """Compact prompt text. Kept short so it does not crowd out retrieved facts."""
        p = [f"Behaviour profile of {self.name} (derived from their own interview answers):"]
        if self.traits:
            p.append("Disposition: " + "; ".join(self.trait_lines()) + ".")
        if self.style:
            p.append("Decision style: " + "; ".join(self.style) + ".")
        if self.values:
            p.append("Holds strongly: " + "; ".join(self.values[:6]) + ".")
        if self.basics:
            p.append("Background: " + "; ".join(self.basics[:5]) + ".")
        if self.precedents:
            p.append("How they have handled comparable situations:")
            for q, a in self.precedents[:max_precedents]:
                p.append(f"  - {q} -> {a}")
        return "\n".join(p)

    def as_markdown(self):
        out = [f"### Behaviour profile — {self.name}",
               f"*Derived from {self.n_answers} interview answers. Scores are computed, not guessed.*", ""]
        if self.traits:
            out.append("| Trait | Score | Reads as |")
            out.append("|---|---|---|")
            for t, d in self.traits.items():
                low, high = TRAIT_POLES[t]
                pole = high if d["score"] >= 50 else low
                out.append(f"| {t.replace('_', ' ').title()} | **{d['score']}**/100 ({d['band']}) | {pole} |")
            out.append("")
        if self.style:
            out.append("**Decision style:** " + "; ".join(self.style) + "\n")
        if self.values:
            out.append("**Holds strongly:** " + "; ".join(self.values[:8]) + "\n")
        if self.precedents:
            out.append("**Reaction precedents used for unseen situations:**\n")
            for q, a in self.precedents[:8]:
                out.append(f"- *{q}* → {a}")
        return "\n".join(out)

    def to_dict(self):
        return asdict(self)


def _condense(text, n=160):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 40 else cut) + "..."


def first_name(full):
    """'R Gagan Chowdary' -> 'Gagan', 'Karthikeya singh' -> 'Karthikeya': the first word that isn't an initial."""
    words = (full or "").split()
    real = [w for w in words if len(w.strip(".")) > 1]
    if not words:
        return "this person"
    w = (real or words)[0]
    return w[:1].upper() + w[1:]


def build_profile(answers, name="this person"):
    """answers: list of {id, section, type, question, answer, repeat_of?}."""
    prof = Profile(name=name)
    answers = [a for a in answers if str(a.get("answer", "")).strip()
               and not a.get("repeat_of")]        # repeats are the consistency baseline
    prof.n_answers = len(answers)

    buckets = {}
    for a in answers:
        qn = _norm(a.get("question"))
        sec = a.get("section", "")
        ans = str(a.get("answer", "")).strip()
        v = likert_value(ans)

        if v is not None:
            for frag, (trait, sign) in TRAIT_ITEMS.items():
                if _norm(frag) in qn:          # fragments may hold apostrophes; qn has none
                    buckets.setdefault(trait, []).append(v if sign > 0 else 6 - v)
                    break
            else:
                if sec == SEC_BELIEF and v in (1, 5):
                    stance = "agrees" if v == 5 else "disagrees"
                    prof.values.append(f"{stance} that {_condense(a['question'], 90).rstrip('.')}")
            continue

        if sec == SEC_EITHEROR:
            prof.style.append(f"{_condense(a['question'], 70).rstrip('?.')} -> {ans}")
        elif sec == SEC_SITUATION:
            prof.precedents.append((_condense(a["question"], 90), _condense(ans, 150)))
        elif sec in (SEC_ABOUT, SEC_PREFS):
            prof.basics.append(f"{_condense(a['question'], 60).rstrip('?.')}: {_condense(ans, 90)}")
        elif sec == SEC_OPEN:
            prof.precedents.append((_condense(a["question"], 90), _condense(ans, 150)))

    for trait, vals in buckets.items():
        score = round(100 * (sum(vals) / len(vals) - 1) / 4)
        prof.traits[trait] = {"score": score, "band": _band(score), "n_items": len(vals)}
    prof.traits = {k: prof.traits[k] for k in TRAIT_POLES if k in prof.traits}
    return prof


def load_profile(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return Profile(**d)


def save_profile(profile, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
