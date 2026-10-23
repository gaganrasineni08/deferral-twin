"""
Learning-curve experiment for the self-learning (feedback) loop, on Twin-2K-500.

Question it answers: does the twin get more accurate as the person corrects it?

For each participant:
  1. Split their test questions (fixed seed) into a FEEDBACK POOL and a
     HELD-OUT EVAL set. The eval set is never used as feedback.
  2. For k in k_list (default 0, 5, 10, 20, 40): simulate the person giving
     feedback on k pool questions ("this is what I expected" = their true
     answer), add those corrections to the twin's live memory, then answer
     the held-out set and score it.
  3. k = 0 is the no-feedback baseline, on exactly the same held-out questions.

Corrections are cumulative (the k=10 run contains the k=5 corrections), so the
curve shows accuracy as a function of how much feedback the twin has received.

Usage (after the model and batch function are loaded, as in the Phase 1 run):
  import feedback_learning_curve as flc
  flc.main("data/benchmark/twin2k500", real_llm_generate_batch, limit=10)
"""
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer, util

sys.path.append(str(Path(__file__).resolve().parent))
from rag_pipeline import RagStore, load_config          # noqa: E402
from deferral import format_context, CORRECTION_NOTE    # noqa: E402


def load_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _build_prompt(confidence, hard_floor, threshold, persona_summary, context_text):
    """Same two-tier prompts as eval_benchmark.py (plus the correction note)."""
    if confidence < hard_floor:
        return None, "hard_defer"
    if confidence < threshold:
        return (
            "You are a digital twin of this survey participant. You don't have "
            "an exact match for this question, but here is a general summary "
            "of who they are, plus some loosely related facts. Give your best "
            "reasoned guess, staying consistent with their general profile. "
            "If you genuinely can't infer anything reasonable, say so honestly. "
            "Answer briefly." + CORRECTION_NOTE + "\n\n"
            f"Persona summary: {persona_summary}\n\n"
            f"Loosely related facts:\n{context_text}"
        ), "soft_answer"
    return (
        "Answer as this participant would, based only on the context below. "
        "Answer briefly." + CORRECTION_NOTE + "\n\n"
        f"Context:\n{context_text}"
    ), "confident_answer"


def answer_and_score(store, rows, persona_summary, embedder, llm_batch_fn,
                     hard_floor, threshold, batch_size):
    items = []
    for row in rows:
        retrieved, confidence = store.retrieve(row["question"])
        prompt, tier = _build_prompt(confidence, hard_floor, threshold,
                                     persona_summary, format_context(retrieved))
        items.append({"q": row["question"], "gt": row["answer"], "prompt": prompt,
                      "tier": tier, "conf": confidence, "pred": None})

    todo = [it for it in items if it["prompt"] is not None]
    for i in range(0, len(todo), batch_size):
        chunk = todo[i:i + batch_size]
        outs = llm_batch_fn([c["prompt"] for c in chunk], [c["q"] for c in chunk])
        for c, o in zip(chunk, outs):
            c["pred"] = o

    scores = []
    n_correction_hits = 0
    for it in items:
        if it["pred"] is None:
            continue
        emb = embedder.encode([it["pred"], it["gt"]], normalize_embeddings=True)
        it["score"] = float(util.cos_sim(emb[0], emb[1]))
        scores.append(it["score"])
    return {
        "mean_similarity": float(np.mean(scores)) if scores else None,
        "coverage": len(scores) / len(items),
        "n_answered": len(scores),
        "n_questions": len(items),
    }


def _make_store(pdir, train_rows, tag):
    facts_path = pdir / f"facts_{tag}.jsonl"
    with open(facts_path, "w", encoding="utf-8") as f:
        for r in train_rows:
            f.write(json.dumps({"text": f"Q: {r['question']}\nA: {r['answer']}",
                                "source": "twin2k500_train"}, ensure_ascii=False) + "\n")
    cfg = dict(load_config())
    cfg["paths"] = dict(cfg["paths"], qa_facts_file=str(facts_path))
    cfg["rag"] = dict(cfg["rag"], index_path=str(pdir / f"faiss_{tag}.bin"))
    store = RagStore(cfg)
    store.build()
    return store


def _run_curve(store, corrections, k_list, eval_rows, persona_summary, embedder,
               llm_batch_fn, hard_floor, threshold, batch_size):
    curve, added = [], 0
    for k in sorted(k_list):
        for r in corrections[added:k]:
            store.add_fact(f"Q: {r['question']}\nA: {r['answer']}",
                           source="user_correction", persist=False)
        added = k
        res = answer_and_score(store, eval_rows, persona_summary, embedder, llm_batch_fn,
                               hard_floor, threshold, batch_size)
        res["k"] = k
        curve.append(res)
    return curve


def run_participant(pdir: Path, embedder, llm_batch_fn, k_list, threshold, hard_floor,
                    batch_size, seed, foreign_rows=None):
    train_rows = load_jsonl(pdir / "train.jsonl")
    test_rows = load_jsonl(pdir / "test.jsonl")
    if not train_rows or len(test_rows) < 20:
        return None

    rng = random.Random(seed)
    rows = list(test_rows)
    rng.shuffle(rows)
    n_eval = max(10, int(0.4 * len(rows)))
    eval_rows, pool = rows[:n_eval], rows[n_eval:]
    k_list = [k for k in k_list if k <= len(pool)]

    # Personal condition: corrections are THIS person's true answers
    store = _make_store(pdir, train_rows, "curve")
    persona_summary = store.get_persona_summary()
    curve = _run_curve(store, pool, k_list, eval_rows, persona_summary, embedder,
                       llm_batch_fn, hard_floor, threshold, batch_size)
    out = {"curve": curve, "n_eval": len(eval_rows), "pool_size": len(pool)}

    # Control condition: same number of corrections, but real answers from a
    # DIFFERENT person. Isolates format-learning from personalisation.
    if foreign_rows:
        frng = random.Random(seed + 1)
        foreign = list(foreign_rows)
        frng.shuffle(foreign)
        cstore = _make_store(pdir, train_rows, "control")
        out["control_curve"] = _run_curve(cstore, foreign, k_list, eval_rows, persona_summary,
                                          embedder, llm_batch_fn, hard_floor, threshold,
                                          batch_size)
    return out


def main(benchmark_dir, llm_batch_fn, k_list=(0, 5, 10, 20), limit=10,
         batch_size=2, seed=0, threshold=None, hard_floor=None, backup_dir=None):
    cfg = load_config()
    threshold = threshold if threshold is not None else cfg["rag"]["deferral_similarity_threshold"]
    hard_floor = hard_floor if hard_floor is not None else cfg["rag"]["deferral_hard_floor"]
    embedder = SentenceTransformer(cfg["rag"]["embedding_model"])

    pdirs = sorted(Path(benchmark_dir).glob("participant_*"))
    if limit:
        pdirs = pdirs[:limit]

    if backup_dir:
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
    # foreign rows for the control: the next participant's test answers
    test_by_pdir = [load_jsonl(p / "test.jsonl") for p in pdirs]

    per_participant = []
    t0 = time.time()
    for i, pdir in enumerate(pdirs, 1):
        out_path = pdir / "feedback_curve.json"
        if out_path.exists():
            result = json.load(open(out_path))
            print(f"[{i}/{len(pdirs)}] {pdir.name}: loaded saved result (resume)", flush=True)
        else:
            foreign = test_by_pdir[i % len(pdirs)] if len(pdirs) > 1 else None
            result = run_participant(pdir, embedder, llm_batch_fn, list(k_list),
                                     threshold, hard_floor, batch_size, seed, foreign)
            if result is None:
                continue
            json.dump(result, open(out_path, "w"), indent=2)
            if backup_dir:
                json.dump(result, open(Path(backup_dir) / f"{pdir.name}_feedback_curve.json", "w"), indent=2)
            line = ", ".join(f"k={c['k']}: {c['mean_similarity']:.3f}" for c in result["curve"]
                             if c["mean_similarity"] is not None)
            print(f"[{i}/{len(pdirs)}] {pdir.name}: {line} | {(time.time()-t0)/60:.1f} min",
                  flush=True)
        per_participant.append(result)

    def agg(key):
        ks = sorted({c["k"] for r in per_participant if key in r for c in r[key]})
        rows_ = []
        for k in ks:
            sims = [c["mean_similarity"] for r in per_participant if key in r
                    for c in r[key] if c["k"] == k and c["mean_similarity"] is not None]
            rows_.append({"k": k, "mean_similarity": float(np.mean(sims)),
                          "n_participants": len(sims)})
        return rows_

    summary = {"personal": agg("curve"), "control_other_person": agg("control_curve")}
    print(f"\n=== Feedback learning curve ({len(per_participant)} participants) ===")
    print("k | personal corrections | control (another person's corrections)")
    ctrl = {r["k"]: r for r in summary["control_other_person"]}
    for r in summary["personal"]:
        c = ctrl.get(r["k"])
        print(f"{r['k']:>3} | {r['mean_similarity']:.3f}               | "
              f"{c['mean_similarity']:.3f}" if c else f"{r['k']:>3} | {r['mean_similarity']:.3f}")
    with open(Path(benchmark_dir) / "feedback_curve_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    if backup_dir:
        json.dump(per_participant, open(Path(backup_dir) / "ALL_feedback_curves.json", "w"), indent=2)
    return summary
