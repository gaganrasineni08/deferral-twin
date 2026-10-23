"""Stand-in for sentence_transformers so the app can be tested without torch.
Hashed bag-of-words vectors: identical wording scores high, unrelated text near 0."""
import re
import sys
import types
import numpy as np

DIM = 16384
STOP = set("a an the and or but if of to in on at for with is are was were be do does did you your i me my "
           "we it this that what which who how why when where would could should will can have has q".split())


def _vec(t):
    v = np.zeros(DIM, dtype="float32")
    for w in re.findall(r"[a-z0-9]+", t.lower()):
        if w not in STOP:
            v[hash(w) % DIM] += 1
    n = np.linalg.norm(v)
    return v / n if n else v


class SentenceTransformer:
    def __init__(self, name, *a, **kw):
        self.name = name

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        return np.stack([_vec(t) for t in texts])


def cos_sim(a, b):
    return np.array(float(np.dot(a, b)))


def install():
    m = types.ModuleType("sentence_transformers")
    m.SentenceTransformer = SentenceTransformer
    m.util = types.SimpleNamespace(cos_sim=cos_sim)
    sys.modules["sentence_transformers"] = m
    sys.modules["sentence_transformers.util"] = m.util
