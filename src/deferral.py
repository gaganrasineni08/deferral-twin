"""
The deferral mechanism -- this is your project's core novelty.

Two-tier logic instead of one hard cutoff:
  - confidence < hard_floor           -> always defer, nothing relevant at all
  - hard_floor <= confidence < threshold -> "soft" answer: attempt a grounded
                                             guess using the persona summary +
                                             whatever weak matches exist, with
                                             an explicit uncertainty caveat
  - confidence >= threshold           -> normal confident answer, grounded in
                                          the specific retrieved facts

This lets the twin "think and talk like the person" on a broader range of
questions -- falling back on their general demographics/personality/values
when there's no exact topical match -- while still refusing outright when
there's truly nothing relevant, rather than treating every non-exact-match
question as a flat "I don't know."
"""
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from rag_pipeline import RagStore, load_config

DEFERRAL_RESPONSES = [
    "I don't think I've shared my view on that, so I can't answer as myself here.",
    "That's not something I've talked about, so I'd rather not guess what I'd say.",
    "I honestly don't know how I'd answer that one -- it's outside what I've told this system.",
]


SOURCE_TAG = {"user_correction": "[CORRECTION] ", "user_note": "[STATED DIRECTLY] "}

# Questions about the twin itself, and about the clock. Neither is a question about the
# person, so neither goes through the retrieval gate: the answer is true by construction
# and invents nothing about them. Only for a real subject (one with a profile); Phase 1
# participants keep the original behaviour.
END = r"\s*[?.!]*\s*$"
SELF_RE = re.compile(
    r"^\s*(hi|hello|hey)?[\s,]*(who|what)\s+(are|r)\s+(you|u)" + END +
    r"|^\s*what\s+can\s+(you|u)\s+do(\s+for\s+me)?" + END +
    r"|^\s*(what|how)\s+(can|could)\s+(you|u)\s+help(\s+me)?(\s+with)?" + END +
    r"|^\s*(please\s+)?introduce\s+yourself" + END +
    r"|^\s*are\s+(you|u)\s+(an?\s+)?(ai|a\.i\.|bot|chat\s*bot|robot|human|real|machine|computer|program|real\s+person)" + END +
    r"|^\s*how\s+do\s+(you|u)\s+work" + END +
    r"|^\s*what('?s|\s+is)\s+your\s+(purpose|job\s+here)" + END, re.I)
GREET_RE = re.compile(r"^\s*(?:hi+|hello+|hey+|hiya|yo|namaste|good (?:morning|afternoon|evening))\b[\s,!.]*"
                      r"(?:there|twin|buddy|bro)?[\s,!.?]*$", re.I)
CLOCK_RE = re.compile(
    r"^\s*(what('?s|\s+is)\s+the\s+(current\s+)?time(\s+now)?|what\s+time\s+is\s+it(\s+now)?|time\s+now"
    r"|what('?s|\s+is)\s+(the\s+)?(date|day)(\s+today)?|what('?s|\s+is)\s+today'?s\s+date|today'?s\s+date"
    r"|what\s+day\s+is\s+(it|today)(\s+today)?|which\s+day\s+is\s+(it|today))" + END, re.I)
# Self-descriptions a small model slips into; never in character for a digital twin.
AI_TALK_RE = re.compile(r"\b(as an ai|i(?:'m| am) (?:just |only )?an ai\b|i(?:'m| am) not an ai|"
                        r"ai (?:language )?model|large language model|language model|ai assistant|"
                        r"virtual assistant|i don'?t have (?:personal )?(?:feelings|experiences|opinions)|"
                        r"(?:can(?:no|')t|cannot) (?:assist|help) with (?:that|this))", re.I)

# Answering in the person's own format. Interviews mix free text, 1-to-5 ratings of statements
# ("Agree (4 of 5)") and either-or choices ("Generalist"). A twin that answers a statement with a
# paragraph does not sound like the person, so the format follows the question.
LIKERT = ("Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree")
RATED_RE = re.compile(r"\((\d) of 5\)")
CHOICE_RE = re.compile(r"^\s*(?!(?:what|how|why|when|where|who|which|whose)\b)"
                       r"(?:(?:do|would|are|is|will|should|could|did) (?:you|u) (?:rather |prefer |like |be )*)?"
                       r"(?P<a>[^?]{2,80}?),?\s+or\s+(?P<b>[^?]{2,80}?)\s*\?+\s*$", re.I)
ASKED_RE = re.compile(r"^\s*(?:(?:what|how|why|when|where|who|which|whose)\b|(?:do|does|did|can|could|will|would|should|"
                      r"have|has|are|were|may|shall)\s+(?:you|u|i|we)\b|"
                      r".*\b(?:what|how|why|where|which)\s+(?:will|would|do|did|should|can|could)\s+(?:you|u)\b)",
                      re.I)                                                    # a question typed without its "?"
NEAR_SAME = 0.5          # raw similarity at which a stored answer is to almost the same question
DONT_KNOW_RE = re.compile(r"^\W*(?:i'?m sorry,?\s*)?(?:i\s+)?(?:don'?t|do not|dont)\s+know\b|^\W*(?:i'?m\s+)?not sure\W*$", re.I)
CHAT_RE = re.compile(r"^\s*(?:hi|hello|hey|thanks|thank you|ok|okay|bye|good (?:morning|night|evening)|"
                     r"(?:i am|i'm|im) (?:feeling|so|very|really|tired|sad|happy|bored|angry|back|here))\b", re.I)
LEAD_RE = re.compile(r"^\s*(?:oh|well|hmm|ah|haha|hey|honestly|so)\b[,!.]*\s*", re.I)


def qa_parts(text):
    """'Q: ...\nA: ...' -> (question, answer)."""
    q, _, a = (text or "").partition("\nA:")
    return q.replace("Q:", "", 1).strip(), a.strip()


def strip_ai_talk(text):
    """Drop sentences where the model calls itself an AI. Returns '' if nothing is left."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(x for x in parts if x and not AI_TALK_RE.search(x)).strip()


def format_context(results) -> str:
    """Retrieved facts as prompt text; anything the person stated directly is labelled."""
    return "\n".join(SOURCE_TAG.get(r.get("source"), "") + r["text"] for r in results)


CORRECTION_NOTE = (" Facts marked [CORRECTION] or [STATED DIRECTLY] came from the person "
                   "themselves and override anything else.")


@dataclass
class TwinResponse:
    answer: str
    deferred: bool
    confidence: float
    tier: str                # "hard_defer" | "soft_answer" | "confident_answer"
    retrieved_context: list


class DeferralAwareTwin:
    def __init__(self, config_path=None, llm_generate_fn=None, rag=None, cfg=None,
                 person_name="this person", profile=None):
        """
        llm_generate_fn: a callable(system_prompt, user_query, context) -> str
        rag / cfg: optional pre-built RagStore and config (used by the demo app
        to run one twin per participant); default is the project-level store.
        profile: optional persona.Profile. When present it replaces the crude
        "first N facts" summary on the middle tier, so the twin can reason from
        the person's disposition and past reactions on a question they have
        never been asked. Absent (e.g. all Phase 1 benchmark runs), behaviour
        is unchanged.
        """
        self.cfg = cfg or load_config()
        self.person_name = person_name
        if rag is not None:
            self.rag = rag
        else:
            self.rag = RagStore(self.cfg)
            self.rag.load()
        self.threshold = self.cfg["rag"]["deferral_similarity_threshold"]
        self.hard_floor = self.cfg["rag"]["deferral_hard_floor"]
        self.llm_generate_fn = llm_generate_fn
        self.profile = profile
        self._persona_summary = None  # lazy-built, cached

    @property
    def persona_summary(self):
        if self._persona_summary is None:
            self._persona_summary = (self.profile.narrative() if self.profile is not None
                                     else self.rag.get_persona_summary())
        return self._persona_summary

    def give_feedback(self, question: str, twin_answer: str, expected_answer: str,
                      tier: str = None, confidence: float = None):
        """
        The self-learning step. The person says "this is what I expected":
        the correction is added to the live memory as a high-priority fact
        (used the next time a related question comes up) and logged so it
        can later be used to calibrate deferral or to fine-tune.
        """
        self.rag.add_fact(f"Q: {question}\nA: {expected_answer}", source="user_correction")
        log_path = Path(self.cfg["paths"].get("feedback_log", "data/processed/feedback.jsonl"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": time.time(), "question": question, "twin_answer": twin_answer,
                "expected_answer": expected_answer, "tier": tier, "confidence": confidence,
            }, ensure_ascii=False) + "\n")

    def add_note(self, text: str, max_chunk: int = 400):
        """
        Something the person volunteered, unprompted -- "I switched labs last
        month", "I actually hate crowded places". Split into separate facts so
        each can be retrieved on its own, then added to live memory with the
        same authority as a correction. Returns the facts added.

        This is the other half of the feedback loop: give_feedback fixes an
        answer the twin got wrong, add_note adds knowledge before it is ever
        asked.
        """
        chunks = []
        for block in re.split(r"\n\s*\n|\r\n\s*\r\n", (text or "").strip()):
            for line in block.splitlines():
                line = line.strip(" -*\u2022\t")
                if not line:
                    continue
                while len(line) > max_chunk:                 # keep facts retrievable
                    cut = line.rfind(". ", 0, max_chunk)
                    cut = cut + 1 if cut > 80 else max_chunk
                    chunks.append(line[:cut].strip())
                    line = line[cut:].strip()
                if line:
                    chunks.append(line)
        if not chunks:
            return []
        for c in chunks:
            self.rag.add_fact(c, source="user_note")
        log_path = Path(self.cfg["paths"].get("note_log", "data/processed/notes.jsonl"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps({"time": time.time(), "text": c}, ensure_ascii=False) + "\n")
        return chunks

    def _style(self):
        """How this person writes, learned from their own free-text answers: typical length,
        lowercase or not, and a few real examples. Recomputed as corrections and notes arrive."""
        own = [qa_parts(f["text"])[1] for f in getattr(self.rag, "facts", [])
               if f.get("source") in ("twin2k500_train", "interview", "user_correction")]
        own = [a for a in own if a and not RATED_RE.search(a) and len(a.split()) >= 2]
        if not own:
            return None
        key = len(own)
        if getattr(self, "_style_cache", (None,))[0] == key:
            return self._style_cache[1]
        words = sorted(len(a.split()) for a in own)
        median = words[len(words) // 2]
        lower = sum(a[:1].islower() for a in own) / len(own) > 0.5
        ex = sorted(own, key=lambda a: abs(len(a.split()) - median))[:4]
        style = {"words": max(3, min(60, median)), "lower": lower, "examples": ex}
        self._style_cache = (key, style)
        return style

    def _persona_rule(self):
        if self.profile is None:                        # Phase 1: original prompts
            return ""
        n = self.person_name
        rule = (f" Every question is put to {n} about {n}'s own life and views: answer with what {n} would say, "
                "do or think. Never console, thank, advise or question the person asking."
                f" Speak as {n} in the first person (I, my). Never say you are an AI, a language model "
                "or an assistant, and never mention this system or the context you were given; if asked "
                f"what you are, you are {n}'s digital twin.")
        st = self._style()
        if st:
            rule += (f" Write the way {n} actually writes: usually about {st['words']} words"
                     + (", mostly in lowercase" if st["lower"] else "") +
                     ", direct and casual. No greetings, no 'Oh', no sympathy, no advice, no explanation of the "
                     "question: just their answer. Real examples of how they answer: "
                     + "; ".join(f'"{e}"' for e in st["examples"]) + ".")
        rule += (" If what they said above answers the question, even partly (a film they named, a habit, a "
                 "choice), answer with it. Only when the question needs a specific fact that appears nowhere "
                 "above (what they ate or did on a given day, something they own, any name, number, date or "
                 "document) answer \"I don't know\" instead of making one up.")
        return rule

    def _rated(self, query, results):
        """A statement to rate, answered on their own 1-to-5 scale from how they rated similar ones."""
        near = [(r["score"], *qa_parts(r["text"])) for r in self.rag.retrieve(query, top_k=25)[0]]
        near = [(sc, q, a) for sc, q, a in near if RATED_RE.search(a)][:8]
        if not near:
            return None
        allr = [int(RATED_RE.search(qa_parts(f["text"])[1]).group(1)) for f in getattr(self.rag, "facts", [])
                if RATED_RE.search(qa_parts(f["text"])[1])]
        habit = ", ".join(f"{LIKERT[v - 1]} {allr.count(v)} times" for v in range(1, 6) if allr.count(v))
        sp = (f"You are {self.person_name}'s digital twin. Below is how {self.person_name} rated statements "
              "like the new one. Decide how they would rate the new statement; watch for statements that "
              "mean the opposite of theirs. People keep their habitual way of rating: across all "
              f"{len(allr)} statements they rated, they answered {habit}. Reply with exactly one of: "
              + ", ".join(f"{l} ({i} of 5)" for i, l in enumerate(LIKERT, 1)) + ". Nothing else.\n\n"
              + "\n".join(f"- {q} -> {a}" for _, q, a in near))
        out = self.llm_generate_fn(sp, query, results) or ""
        m = RATED_RE.search(out)
        if m and 1 <= int(m.group(1)) <= 5:
            v = int(m.group(1))
        else:
            hit = next((i for i, l in enumerate(LIKERT, 1) if re.search(rf"\b{l}\b", out, re.I)), None)
            v = hit or int(RATED_RE.search(near[0][2]).group(1))          # fall back to their nearest rating
        return f"{LIKERT[v - 1]} ({v} of 5)"

    def _choice(self, query, m, results, context_text):
        """An either-or question, answered by naming one option in a few words, as they do."""
        a, b = m.group("a").strip(), m.group("b").strip()
        past = [qa_parts(f["text"]) for f in getattr(self.rag, "facts", [])]
        past = [(q, ans) for q, ans in past if " or " in q.lower() and 0 < len(ans.split()) <= 5][:6]
        sp = (f"You are {self.person_name}'s digital twin. Choose what {self.person_name} would choose: "
              f'"{a}" or "{b}". Reply with only the choice, in one to four words, the way they answer such '
              "questions. No explanation." + self._persona_rule() + "\n\n"
              + ("Their past either-or answers:\n" + "\n".join(f"- {q} -> {ans}" for q, ans in past) + "\n\n" if past else "")
              + f"How they tend to behave:\n{self.persona_summary}\n\n"
              + f"Things they have said that may help:\n{context_text}")
        out = self._clean(self.llm_generate_fn(sp, query, results))
        return out.split("\n")[0].strip().rstrip(".") if out else None

    def _shape(self, query, results, context_text):
        """For a real subject: an answer in the question's own format, or None for free text."""
        if self.profile is None:
            return None
        words = len(query.split())
        statement = "?" not in query and 3 <= words <= 24 and not CHAT_RE.match(query) and not ASKED_RE.match(query)
        if statement and any(RATED_RE.search(r["text"]) for r in results[:5]):   # a statement to rate, like the interview's
            return self._rated(query, results)
        m = CHOICE_RE.match(query)
        if m:
            return self._choice(query, m, results, context_text)
        return None

    def _first_name(self):
        from persona import first_name
        return first_name(self.person_name)

    def _about_twin(self, query):
        n, first = self.person_name, self._first_name()
        results, confidence = self.rag.retrieve(query)
        said = ""
        key = " ".join(query.lower().split()).rstrip("?.! ")
        same_q = [r["text"] for r in results if r.get("source") == "user_correction"
                  and " ".join(qa_parts(r["text"])[0].lower().split()).rstrip("?.! ") == key]
        if same_q or confidence >= self.threshold:      # something they taught about themselves: the latest wins
            taught = same_q or [r["text"] for r in results if r.get("source") in SOURCE_TAG]
            facts = [f["text"] for f in getattr(self.rag, "facts", [])]
            taught.sort(key=lambda t: max((i for i, f in enumerate(facts) if f == t), default=-1), reverse=True)
            for t in taught:
                text = t.split("\nA:", 1)[-1].strip()
                if text and not AI_TALK_RE.search(text):
                    said = text.rstrip(". ") + ". "
                    break
        if re.search(r"\b(ai|a\.i\.|bot|robot|human|real|machine|computer|program|person)\W*$", query, re.I):
            answer = (f"I'm the digital twin of {n}. I'm not {first} in person: I answer the way {first} would, "
                      f"using what {first} told me in the interview and anything taught to me since. "
                      "When nothing they've said is close to your question, I'll say I don't know rather than make it up.")
        else:
            answer = (f"I'm the digital twin of {n}. {said}"
                      f"Ask me what I think, what I'd do in a situation, or how I spend my time, and I'll answer the way {first} would, "
                      f"from what {first} told me. If I've never said anything close to your question, I'll tell you I don't know instead of guessing. "
                      "Correct me or tell me something new and I'll remember it straight away, and I can say my answers out loud in my own voice.")
        return TwinResponse(answer=answer, deferred=False, confidence=confidence, tier="about_twin",
                            retrieved_context=results)

    def _clock(self):
        import datetime
        now = datetime.datetime.now()
        hour = now.strftime("%I:%M %p").lstrip("0")
        answer = f"It's {hour} on {now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}."
        return TwinResponse(answer=answer, deferred=False, confidence=0.0, tier="about_twin", retrieved_context=[])

    def _clean(self, answer):
        if self.profile is None:
            return answer
        text = LEAD_RE.sub("", strip_ai_talk(answer)).strip()
        text = text[:1].upper() + text[1:] if text and not (self._style() or {}).get("lower") else text
        end = max(text.rfind(c) for c in ".!?")
        if text and text[-1] not in ".!?\"')" and end > len(text) // 3:    # cut off by the length limit
            text = text[:end + 1]
        return text or DEFERRAL_RESPONSES[2]

    def respond(self, query: str) -> TwinResponse:
        if self.profile is not None:
            if CLOCK_RE.search(query or ""):
                return self._clock()
            if SELF_RE.search(query or ""):
                return self._about_twin(query)
            if GREET_RE.match(query or ""):
                first = self._first_name()
                return TwinResponse(answer=f"Hi! I'm {first}'s digital twin. Ask me what I think, what I'd do, "
                                           f"or how I spend my days, and I'll answer the way {first} would.",
                                    deferred=False, confidence=0.0, tier="about_twin", retrieved_context=[])
        results, confidence = self.rag.retrieve(query)

        # Tier 1: nothing relevant at all -- hard defer, no LLM call
        if confidence < self.hard_floor:
            return TwinResponse(
                answer=DEFERRAL_RESPONSES[0],
                deferred=True,
                confidence=confidence,
                tier="hard_defer",
                retrieved_context=results,
            )

        if self.llm_generate_fn is None:
            raise RuntimeError("No llm_generate_fn provided -- plug in your model's generate call.")

        context_text = format_context(results)
        shaped = self._shape(query, results, context_text)

        # Tier 2: moderate confidence -- fall back on persona summary,
        # attempt a reasoned guess, flag the uncertainty explicitly
        if confidence < self.threshold:
            system_prompt = (
                f"You are a digital twin of {self.person_name}. They have never answered "
                "this exact question. Below is their behaviour profile -- their "
                "disposition and how they handled comparable situations -- plus some "
                "loosely related facts they did state.\n"
                "Reason by analogy: decide what this person would most likely do, and "
                "answer in the first person, in their tone, in two or three sentences.\n"
                "Hard rule: do NOT invent specific facts -- no names, places, dates, "
                "numbers or events that are not given below. If answering would require "
                "a specific fact you have not been given, say you don't know instead."
                + CORRECTION_NOTE + self._persona_rule() + "\n\n"
                f"{self.persona_summary}\n\n"
                f"Loosely related things they have said:\n{context_text}"
            )
            answer = shaped or self._clean(self.llm_generate_fn(system_prompt, query, results))
            return TwinResponse(
                answer=answer,
                deferred=False,
                confidence=confidence,
                tier="soft_answer",
                retrieved_context=results,
            )

        # Tier 3: high confidence -- normal grounded answer
        near = results[0] if results and results[0]["score"] >= NEAR_SAME and results[0].get("source") != "user_note" else None
        if self.profile is not None:
            lead = (f'Their own answer to almost the same question ("{qa_parts(near["text"])[0]}") was: '
                    f'"{qa_parts(near["text"])[1]}". Answer with that, adapted to this question.\n'
                    if near and qa_parts(near["text"])[1] else "")
            system_prompt = (
                f"{lead}You are the digital twin of {self.person_name}. Answer from what {self.person_name} said below, "
                "reusing their own words where they fit; combine their answers when several apply. Do not invent "
                "names, places, dates, numbers or events they did not mention. If nothing below relates to the "
                "question at all, say you don't know." + CORRECTION_NOTE + self._persona_rule() + "\n\n"
                f"What they said:\n{context_text}"
            )
        else:
            system_prompt = (
                f"You are a digital twin of {self.person_name}. Answer using only the "
                "context below, in their tone and style. If the context doesn't "
                "actually answer the question, say you're not sure rather than guessing." + CORRECTION_NOTE + "\n\n"
                f"Context:\n{context_text}"
            )
        answer = shaped or self._clean(self.llm_generate_fn(system_prompt, query, results))
        if self.profile is not None and near and not shaped and DONT_KNOW_RE.match(answer):
            answer = qa_parts(near["text"])[1] or answer       # they did answer this: use their own words
        return TwinResponse(
            answer=answer,
            deferred=False,
            confidence=confidence,
            tier="confident_answer",
            retrieved_context=results,
        )


if __name__ == "__main__":
    def stub_llm(system_prompt, query, context):
        return f"[STUB RESPONSE] Would answer '{query}' using {len(context)} retrieved facts."

    twin = DeferralAwareTwin(llm_generate_fn=stub_llm)
    test_query = input("Ask the twin something: ")
    result = twin.respond(test_query)
    print(f"\nTier: {result.tier} | Confidence: {result.confidence:.3f}")
    print(f"Answer: {result.answer}")
