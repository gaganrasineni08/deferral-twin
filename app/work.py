"""
The person's work and skills ("I'm a civil engineer"). Questions about their field ("what grade of
concrete for a slab?") are answered from general professional knowledge, in their voice, and labelled
as such: "From my field · general knowledge, not a personal memory". Questions about the person go
through the deferral gate exactly as before; this never changes the gate's confidence (HANDOFF 2.3).

Saved as twin/work_profile.json: {"title", "skills": [...], "about", "topics": [...], "updated"}.
"topics" are short subjects someone in that job knows, written once by the model when the profile
is saved, and used (with the job title and skills) to recognise field questions by embedding similarity.
"""
import json
import re
import time
from pathlib import Path

WORK_FILE = "work_profile.json"
MATCH = 0.40                     # cosine to the nearest topic at or above which a question is a field question
MAYBE = 0.22                     # between MAYBE and MATCH: Gemini is asked yes or no
ABOUT_MAX = 6000                 # characters of CV / job report kept

# Questions about the person themself stay with the personal memory and the gate.
PERSONAL_RE = re.compile(
    r"\b(?:do|would|will|did|have|are|were|can|could|should) (?:you|u) (?:like|love|hate|enjoy|prefer|feel|want|wish|"
    r"think of|believe|remember|ever|usually|often)\b|\byour (?:favou?rite|family|friends?|parents?|mother|father|"
    r"brother|sister|girlfriend|boyfriend|wife|husband|childhood|hobby|hobbies|name|age|birthday|home|address|"
    r"phone|passport|salary|feelings?|opinion of)\b|\bhow (?:do|did|would) you feel\b|\bwhat (?:do|did) you (?:do|eat|like)\b"
    r"|\bwho are you\b|\babout yourself\b", re.I)


def path(folder):
    return Path(folder) / WORK_FILE


def load(folder):
    try:
        w = json.loads(path(folder).read_text(encoding="utf-8"))
        return w if (w.get("title") or "").strip() else None
    except (OSError, ValueError):
        return None


def save(folder, w):
    Path(folder).mkdir(parents=True, exist_ok=True)
    path(folder).write_text(json.dumps(w, ensure_ascii=False, indent=1), encoding="utf-8")


def clean(d):
    title = re.sub(r"\s+", " ", str(d.get("title") or "")).strip()[:80]
    skills = d.get("skills") or []
    if isinstance(skills, str):
        skills = re.split(r"[,\n;]+", skills)
    skills = [re.sub(r"\s+", " ", s).strip()[:60] for s in skills if s and s.strip()][:30]
    about = str(d.get("about") or "").strip()[:ABOUT_MAX]
    return {"title": title, "skills": skills, "about": about}


TOPICS_ASK = """List 70 short subjects (2 to 6 words each) that a {title} works with or is often asked about{skills}.
About half should be everyday problems ordinary people bring to a {title}, in plain words, and the rest the core
technical concepts of the field (include common abbreviations in brackets). One per line, no numbering, nothing else."""


def make_topics(w, generate):
    """generate(system, prompt) -> text. Falls back to the title and skills alone."""
    base = [w["title"]] + w["skills"]
    try:
        txt = generate("You list subjects concisely.",
                       TOPICS_ASK.format(title=w["title"], skills=(" (skills: " + ", ".join(w["skills"]) + ")") if w["skills"] else ""))
        got = [re.sub(r"^[\s\-*•\d.)]+", "", l).strip() for l in (txt or "").splitlines()]
        got = [g for g in got if 2 <= len(g) <= 80 and not g.lower().startswith(("here", "sure"))]
    except Exception:
        got = []
    seen, out = set(), []
    for t in base + got:
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            out.append(t)
    return out[:90]


class Matcher:
    """Recognises field questions: embedding similarity to the topics, the title and the skills."""

    def __init__(self, embedder):
        self.embedder, self.key, self.vecs, self.topics = embedder, None, None, []

    def _ready(self, w):
        key = (w["title"], tuple(w["skills"]), tuple(w.get("topics") or []))
        if key != self.key:
            self.topics = list(dict.fromkeys([w["title"]] + w["skills"] + (w.get("topics") or [])))
            self.vecs = self.embedder.encode(self.topics, normalize_embeddings=True)
            self.key = key

    def score(self, q, w):
        """(best cosine, best topic). Questions about the person score 0."""
        if not w or PERSONAL_RE.search(q):
            return 0.0, None
        self._ready(w)
        import numpy as np
        v = self.embedder.encode([q], normalize_embeddings=True)[0]
        sims = np.asarray(self.vecs) @ np.asarray(v)
        i = int(sims.argmax())
        best = float(sims[i])
        words = set(re.findall(r"[a-z]{4,}", q.lower()))
        if any(set(re.findall(r"[a-z]{4,}", t.lower())) & words for t in [w["title"]] + w["skills"]):
            best += 0.08                                   # the question names their job or a skill outright
        return best, self.topics[i]


def is_field_ask(q, w):
    return (f"Is this question asking for knowledge from the field of a {w['title']}"
            + (f" (skills: {', '.join(w['skills'])})" if w["skills"] else "") +
            f", rather than about the person's own life, opinions or habits?\nQuestion: {q}\nAnswer only yes or no.")


def system_prompt(name, persona_rules, w, now):
    skills = (" My skills: " + ", ".join(w["skills"]) + ".") if w["skills"] else ""
    about = ("\n\nFrom my CV / job report:\n" + w["about"][:3000]) if w.get("about") else ""
    return (f"You are {name}'s digital twin, answering a question about {name}'s field of work." + persona_rules +
            f"\n{name} works as a {w['title']}.{skills} Answer from sound, general professional knowledge of a "
            f"{w['title']}, practically and correctly, the way {name} would explain it, in first person and their tone. "
            "Give a complete answer in about 3 to 6 sentences, or a few numbered steps. Be accurate: give standard values, codes or rules of thumb only when "
            "you are confident, and say when something needs a site visit, a calculation or a licensed professional's sign-off. "
            f"Don't invent personal experiences, projects, employers or numbers about {name}'s own life. It is now {now}." + about)
