"""
Phase 1 evaluation: for each participant, builds a RAG index from their
train.jsonl, then asks the model each question in test.jsonl and scores
the predicted answer against the real held-out answer.

Uses the same two-tier deferral logic as deferral.py:
  - confidence < hard_floor   -> hard defer, no LLM call
  - hard_floor <= confidence < threshold -> soft answer (persona summary +
                                              weak matches, flagged uncertain)
  - confidence >= threshold  -> confident answer (specific retrieved facts)

Usage:
  python src/eval_benchmark.py --benchmark_dir data/benchmark/twin2k500
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer, util


def load_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def build_temp_facts_file(train_rows, out_path: Path):
    with open(out_path, "w", encoding="utf-8") as f:
        for row in train_rows:
            f.write(json.dumps({
                "text": f"Q: {row['question']}\nA: {row['answer']}",
                "source": "twin2k500_train",
            }, ensure_ascii=False) + "\n")


def evaluate_participant(participant_dir: Path, embedder, llm_generate_fn,
                          threshold: float, hard_floor: float,
                          llm_batch_fn=None, batch_size: int = 16):
    """
    Returns per-question results, mean model similarity, mean human
    test-retest baseline similarity, and a breakdown of how many questions
    fell into each deferral tier.
    """
    import sys
    sys.path.append(str(Path(__file__).resolve().parent))
    from rag_pipeline import RagStore, load_config

    train_rows = load_jsonl(participant_dir / "train.jsonl")
    test_rows = load_jsonl(participant_dir / "test.jsonl")
    baseline_path = participant_dir / "baseline.jsonl"
    baseline_rows = load_jsonl(baseline_path) if baseline_path.exists() else []
    baseline_by_q = {r["question"]: r["answer"] for r in baseline_rows}

    if not train_rows or not test_rows:
        return None

    facts_path = participant_dir / "facts.jsonl"
    index_path = participant_dir / "faiss_index.bin"
    build_temp_facts_file(train_rows, facts_path)

    cfg = load_config()
    cfg = dict(cfg)
    cfg["paths"] = dict(cfg["paths"], qa_facts_file=str(facts_path))
    cfg["rag"] = dict(cfg["rag"], index_path=str(index_path))

    store = RagStore(cfg)
    store.build()
    persona_summary = store.get_persona_summary()

    # Pass 1: retrieve + build the prompt for every question
    items = []
    for row in test_rows:
        question, ground_truth = row["question"], row["answer"]
        retrieved, confidence = store.retrieve(question)
        if confidence < hard_floor:
            items.append({"q": question, "gt": ground_truth, "conf": confidence,
                          "tier": "hard_defer", "prompt": None, "retrieved": retrieved})
            continue
        context_text = "\n".join(r["text"] for r in retrieved)
        if confidence < threshold:
            system_prompt = (
                "You are a digital twin of this survey participant. You don't have "
                "an exact match for this question, but here is a general summary "
                "of who they are, plus some loosely related facts. Give your best "
                "reasoned guess, staying consistent with their general profile. "
                "If you genuinely can't infer anything reasonable, say so honestly. "
                "Answer briefly.\n\n"
                f"Persona summary: {persona_summary}\n\n"
                f"Loosely related facts:\n{context_text}"
            )
            tier = "soft_answer"
        else:
            system_prompt = (
                "Answer as this participant would, based only on the context below. "
                "Answer briefly.\n\n"
                f"Context:\n{context_text}"
            )
            tier = "confident_answer"
        items.append({"q": question, "gt": ground_truth, "conf": confidence,
                      "tier": tier, "prompt": system_prompt, "retrieved": retrieved})

    # Pass 2: generate (batched if a batch function was supplied)
    to_gen = [it for it in items if it["prompt"] is not None]
    if llm_batch_fn is not None:
        for i in range(0, len(to_gen), batch_size):
            chunk = to_gen[i:i + batch_size]
            outs = llm_batch_fn([c["prompt"] for c in chunk], [c["q"] for c in chunk])
            for c, o in zip(chunk, outs):
                c["pred"] = o
    else:
        for it in to_gen:
            it["pred"] = llm_generate_fn(it["prompt"], it["q"], it["retrieved"])

    # Pass 3: score
    results = []
    for it in items:
        predicted = it.get("pred")
        deferred = it["prompt"] is None
        score = None
        if predicted is not None:
            emb = embedder.encode([predicted, it["gt"]], normalize_embeddings=True)
            score = float(util.cos_sim(emb[0], emb[1]))
        baseline_score = None
        baseline_answer = baseline_by_q.get(it["q"])
        if baseline_answer is not None:
            emb_b = embedder.encode([baseline_answer, it["gt"]], normalize_embeddings=True)
            baseline_score = float(util.cos_sim(emb_b[0], emb_b[1]))
        results.append({
            "question": it["q"],
            "ground_truth": it["gt"],
            "predicted": predicted,
            "deferred": deferred,
            "tier": it["tier"],
            "confidence": float(it["conf"]),
            "similarity_score": score,
            "human_baseline_answer": baseline_answer,
            "human_baseline_score": baseline_score,
        })

    scored = [r["similarity_score"] for r in results if r["similarity_score"] is not None]
    mean_score = float(np.mean(scored)) if scored else None
    baseline_scored = [r["human_baseline_score"] for r in results if r["human_baseline_score"] is not None]
    mean_baseline_score = float(np.mean(baseline_scored)) if baseline_scored else None
    deferral_rate = sum(r["deferred"] for r in results) / len(results)

    tier_counts = {"hard_defer": 0, "soft_answer": 0, "confident_answer": 0}
    for r in results:
        tier_counts[r["tier"]] += 1

    return {
        "results": results,
        "mean_similarity": mean_score,
        "mean_human_baseline_similarity": mean_baseline_score,
        "deferral_rate": deferral_rate,
        "tier_counts": tier_counts,
    }


def main(benchmark_dir: str, llm_generate_fn, threshold: float = None, hard_floor: float = None,
         limit: int = None, resume: bool = True,
         llm_batch_fn=None, batch_size: int = 16):
    import sys
    sys.path.append(str(Path(__file__).resolve().parent))
    from rag_pipeline import load_config

    cfg = load_config()
    threshold = threshold if threshold is not None else cfg["rag"]["deferral_similarity_threshold"]
    hard_floor = hard_floor if hard_floor is not None else cfg["rag"]["deferral_hard_floor"]

    embedder = SentenceTransformer(cfg["rag"]["embedding_model"])
    benchmark_dir = Path(benchmark_dir)

    all_scores = []
    all_baseline_scores = []
    per_participant_summary = []
    total_tier_counts = {"hard_defer": 0, "soft_answer": 0, "confident_answer": 0}

    pdirs = sorted(benchmark_dir.glob("participant_*"))
    if limit:
        pdirs = pdirs[:limit]
    t_start = time.time()
    for i, pdir in enumerate(pdirs, 1):
        done_path = pdir / "eval_results.json"
        if resume and done_path.exists():
            with open(done_path) as f:
                result = json.load(f)
            print(f"[{i}/{len(pdirs)}] {pdir.name}: loaded saved result (resume)", flush=True)
        else:
            t0 = time.time()
            result = evaluate_participant(pdir, embedder, llm_generate_fn, threshold, hard_floor,
                                          llm_batch_fn, batch_size)
            if result is None:
                continue
            with open(done_path, "w") as f:
                json.dump(result, f, indent=2)
            import gc
            gc.collect()
            try:
                import torch; torch.cuda.empty_cache()
            except Exception:
                pass
            elapsed = time.time() - t_start
            print(f"[{i}/{len(pdirs)}] {pdir.name}: {time.time()-t0:.0f}s, "
                  f"score={result['mean_similarity']}, deferral={result['deferral_rate']:.0%}, "
                  f"total elapsed {elapsed/60:.1f} min", flush=True)
        per_participant_summary.append({
            "participant": pdir.name,
            "mean_similarity": result["mean_similarity"],
            "mean_human_baseline_similarity": result["mean_human_baseline_similarity"],
            "deferral_rate": result["deferral_rate"],
            "tier_counts": result["tier_counts"],
        })
        if result["mean_similarity"] is not None:
            all_scores.append(result["mean_similarity"])
        if result["mean_human_baseline_similarity"] is not None:
            all_baseline_scores.append(result["mean_human_baseline_similarity"])
        for k in total_tier_counts:
            total_tier_counts[k] += result["tier_counts"][k]

    overall = float(np.mean(all_scores)) if all_scores else None
    overall_baseline = float(np.mean(all_baseline_scores)) if all_baseline_scores else None
    total_questions = sum(total_tier_counts.values())

    print(f"\n=== Benchmark accuracy across {len(per_participant_summary)} participants ===")
    print(f"Thresholds used: hard_floor={hard_floor}, threshold={threshold}")
    print(f"Mean semantic similarity to ground truth (model):        {overall}")
    print(f"Mean semantic similarity to ground truth (human retest):  {overall_baseline}")
    print(f"\nTier breakdown across {total_questions} total questions:")
    for tier, count in total_tier_counts.items():
        pct = 100 * count / total_questions if total_questions else 0
        print(f"  {tier}: {count} ({pct:.1f}%)")

    with open(benchmark_dir / "overall_eval_summary.json", "w") as f:
        json.dump({
            "thresholds": {"hard_floor": hard_floor, "threshold": threshold},
            "overall_mean_similarity_model": overall,
            "overall_mean_similarity_human_baseline": overall_baseline,
            "total_tier_counts": total_tier_counts,
            "per_participant": per_participant_summary,
        }, f, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark_dir", default="data/benchmark/twin2k500")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--hard_floor", type=float, default=None)
    args = parser.parse_args()

    def stub_llm(system_prompt, query, context):
        return context[0]["text"].split("A:")[-1].strip() if context else ""

    main(args.benchmark_dir, stub_llm, args.threshold, args.hard_floor)
