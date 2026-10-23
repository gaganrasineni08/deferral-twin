"""
Embeds the RAG fact store, builds a FAISS index, and exposes retrieve()
with a confidence score, plus get_persona_summary() -- a short, always-on
snapshot of the person (demographics/personality/values, whichever facts
come first in their data) used as fallback grounding when retrieval
confidence is only moderate, so the twin can still attempt a reasoned
guess instead of always defaulting to "I don't know."

Run once to build the index:
  python src/rag_pipeline.py --build

Then import RagStore from elsewhere at inference time.
"""
import json
import argparse
from pathlib import Path

import yaml
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "config.yaml"

# Facts the person stated directly -- either correcting the twin, or volunteering
# something unprompted. Both outrank facts merely drawn from the interview, so
# both get the ranking boost and both are labelled in the prompt.
AUTHORITATIVE_SOURCES = ("user_correction", "user_note")


def _question_key(text):
    """'Q: What can you do?\nA: ...' -> 'what can you do', for matching corrections to the same question."""
    q = (text or "").split("\nA:", 1)[0].replace("Q:", "", 1)
    return " ".join(q.lower().split()).rstrip("?.! ")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


class RagStore:
    def __init__(self, cfg):
        self.cfg = cfg
        self.embedder = SentenceTransformer(cfg["rag"]["embedding_model"])
        self.index_path = Path(cfg["rag"]["index_path"])
        self.facts_path = Path(cfg["paths"]["qa_facts_file"])
        self.facts = []
        self.index = None

    def build(self):
        """Embed all facts and build a FAISS index (cosine similarity via normalized inner product)."""
        self.facts = []
        with open(self.facts_path, encoding="utf-8") as f:
            for line in f:
                self.facts.append(json.loads(line))

        texts = [f["text"] for f in self.facts]
        embeddings = self.embedder.encode(texts, normalize_embeddings=True, show_progress_bar=True)
        embeddings = np.array(embeddings, dtype="float32")

        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self.index_path))

        with open(self.index_path.with_suffix(".facts.json"), "w", encoding="utf-8") as f:
            json.dump(self.facts, f, ensure_ascii=False)

        print(f"Indexed {len(self.facts)} facts -> {self.index_path}")

    def load(self):
        self.index = faiss.read_index(str(self.index_path))
        with open(self.index_path.with_suffix(".facts.json"), encoding="utf-8") as f:
            self.facts = json.load(f)

    def add_fact(self, text: str, source: str = "user_correction", persist: bool = True):
        """
        Add one fact to the live index without rebuilding (used by the
        feedback loop: when the person corrects the twin, the correction
        becomes a new retrievable fact immediately -- no training needed).
        """
        if self.index is None:
            self.load()
        if source == "user_correction":                 # a newer correction to the same question replaces the old one
            q = _question_key(text)
            for f in self.facts:
                if f["source"] == "user_correction" and _question_key(f["text"]) == q:
                    f["superseded"] = True
        emb = self.embedder.encode([text], normalize_embeddings=True)
        self.index.add(np.array(emb, dtype="float32"))
        self.facts.append({"text": text, "source": source})
        if persist:
            faiss.write_index(self.index, str(self.index_path))
            with open(self.index_path.with_suffix(".facts.json"), "w", encoding="utf-8") as f:
                json.dump(self.facts, f, ensure_ascii=False)

    def retrieve(self, query: str, top_k: int = None):
        """
        Returns (results, confidence). Results are ranked by cosine score,
        with a small boost for facts the person stated directly -- a correction
        or a volunteered note (see AUTHORITATIVE_SOURCES). 'score' in each result and
        the returned confidence are always the RAW cosine similarity, so the
        deferral gate is unaffected by the boost.
        """
        if self.index is None:
            self.load()
        top_k = top_k or self.cfg["rag"]["top_k"]
        boost = self.cfg["rag"].get("correction_boost", 0.05)

        q_emb = self.embedder.encode([query], normalize_embeddings=True)
        q_emb = np.array(q_emb, dtype="float32")

        fetch = min(top_k * 2, self.index.ntotal)
        scores, idxs = self.index.search(q_emb, fetch)
        scores, idxs = scores[0], idxs[0]

        cands = []
        for s_, i in zip(scores, idxs):
            if i == -1 or self.facts[i].get("superseded"):
                continue
            bonus = boost if self.facts[i]["source"] in AUTHORITATIVE_SOURCES else 0.0
            cands.append((float(s_) + bonus, float(s_), int(i)))
        cands.sort(key=lambda c: c[0], reverse=True)

        results = [
            {"text": self.facts[i]["text"], "source": self.facts[i]["source"], "score": raw}
            for _, raw, i in cands[:top_k]
        ]
        confidence = max((r["score"] for r in results), default=0.0)
        return results, confidence

    def get_persona_summary(self, n: int = None) -> str:
        """
        Returns a short always-on snapshot of this person, built from the
        first n facts (demographics/personality tend to come first, since
        that's how the source surveys and interviews are structured).
        Used as fallback grounding on moderate-confidence questions.
        """
        if not self.facts:
            self.load()
        n = n or self.cfg["rag"].get("persona_summary_facts", 12)
        snippet_facts = self.facts[:n]
        return " | ".join(f["text"].replace("\n", " ")[:150] for f in snippet_facts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="Build the FAISS index from qa_facts.jsonl")
    parser.add_argument("--query", type=str, help="Test a retrieval query after building")
    args = parser.parse_args()

    cfg = load_config()
    store = RagStore(cfg)

    if args.build:
        store.build()

    if args.query:
        store.load()
        results, confidence = store.retrieve(args.query)
        print(f"Confidence: {confidence:.3f}")
        for r in results:
            print(f"  [{r['score']:.3f}] {r['text'][:100]}")
        print(f"\nPersona summary: {store.get_persona_summary()}")
