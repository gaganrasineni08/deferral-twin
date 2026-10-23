"""
Phase 2 evaluation: the twin of a real, consenting subject.

Deliberately separate from eval_benchmark.py. That script inlines its own
prompts and produced the published Phase 1 numbers, so it is left untouched
and stays reproducible. This one drives the *actual deployed* twin
(DeferralAwareTwin + behaviour profile), which is what the demo runs, so the
reported Phase 2 numbers describe the system the examiners will see.

Three numbers come out of it:
  twin score      mean similarity of the twin's answer to the person's real
                  held-out answer
  human ceiling   the same person answering their own questions reworded --
                  the best score any twin could possibly get
  % of ceiling    twin score as a fraction of that ceiling, which is the
                  honest way to report it

  from eval_phase2 import main
  main("data/benchmark/phase2", "phase2_me", llm_batch_fn=real_llm_generate_batch)
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.append(str(Path(__file__).resolve().parent))

TIERS = ("hard_defer", "soft_answer", "confident_answer")


def _jsonl(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def main(bench_root="data/benchmark/phase2", pid="phase2_me",
         llm_fn=None, llm_batch_fn=None, batch_size=8, out_name="phase2_results.json"):
    from sentence_transformers import util
    import demo_app

    pdir = Path(bench_root) / pid
    test = _jsonl(pdir / "test.jsonl")
    baseline = _jsonl(pdir / "baseline.jsonl") if (pdir / "baseline.jsonl").exists() else []
    if not test:
        raise SystemExit(f"No held-out questions in {pdir}")

    if llm_fn is None and llm_batch_fn is not None:
        def llm_fn(sp, q, ctx):
            return llm_batch_fn([sp], [q])[0]
    if llm_fn is None:
        raise SystemExit("Pass llm_fn or llm_batch_fn (your loaded model's generate call).")

    sess = demo_app.TwinSession(bench_root, pid, llm_fn)
    emb = sess.store.embedder

    def sim(a, b):
        e = emb.encode([a, b], normalize_embeddings=True)
        return float(util.cos_sim(e[0], e[1]))

    rows = []
    for i, r in enumerate(test, 1):
        out = sess.twin.respond(r["question"])
        score = None if out.tier == "hard_defer" else sim(out.answer, r["answer"])
        rows.append({"question": r["question"], "section": r.get("section", ""),
                     "truth": r["answer"], "answer": out.answer, "tier": out.tier,
                     "confidence": out.confidence, "score": score})
        print(f"  [{i}/{len(test)}] {out.tier:17s} "
              f"{'   n/a' if score is None else f'{score:.3f}'}  {r['question'][:58]}", flush=True)

    # Human self-consistency: same person, same question, different wording.
    ceiling = [sim(b["answer"], b["reworded_answer"]) for b in baseline] if baseline else []

    scored = [r["score"] for r in rows if r["score"] is not None]
    by_tier = defaultdict(list)
    for r in rows:
        by_tier[r["tier"]].append(r["score"])

    confident = [s for s in by_tier["confident_answer"] if s is not None]
    coverage = len(confident) / len(rows) if rows else 0.0

    summary = {
        "pid": pid, "n_test": len(rows),
        "twin_mean": round(mean(scored), 4) if scored else None,
        "human_ceiling": round(mean(ceiling), 4) if ceiling else None,
        "pct_of_ceiling": (round(100 * mean(scored) / mean(ceiling), 1)
                           if scored and ceiling and mean(ceiling) else None),
        "confident_only_mean": round(mean(confident), 4) if confident else None,
        "confident_coverage": round(coverage, 4),
        "tiers": {t: {"n": len(by_tier[t]),
                      "share": round(len(by_tier[t]) / len(rows), 4) if rows else 0.0,
                      "mean": (round(mean([s for s in by_tier[t] if s is not None]), 4)
                               if any(s is not None for s in by_tier[t]) else None)}
                  for t in TIERS},
        "n_corrections_in_memory": sess.n_taught,
    }
    with open(pdir / out_name, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 62)
    print(f"Phase 2 — {pid}   ({len(rows)} held-out questions never shown to the twin)")
    print("=" * 62)
    for t in TIERS:
        d = summary["tiers"][t]
        m = "n/a" if d["mean"] is None else f"{d['mean']:.3f}"
        print(f"  {t:17s} {d['n']:3d}  ({d['share']*100:4.1f}%)   mean {m}")
    print(f"\n  twin, all answered          {summary['twin_mean']}")
    print(f"  twin, confident only        {summary['confident_only_mean']} "
          f"at {coverage*100:.0f}% coverage")
    print(f"  human ceiling (self-retest) {summary['human_ceiling']}")
    if summary["pct_of_ceiling"] is not None:
        print(f"  twin reaches                {summary['pct_of_ceiling']}% of the human ceiling")
    print(f"\n  saved -> {pdir / out_name}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench_root", default="data/benchmark/phase2")
    ap.add_argument("--pid", default="phase2_me")
    a = ap.parse_args()

    def stub(sp, q, ctx):
        return f"[STUB] {q}"
    main(a.bench_root, a.pid, llm_fn=stub)
