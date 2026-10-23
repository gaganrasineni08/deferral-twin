"""
Phase 2: turn one person's interview answers into a twin dataset.

Input   phase2_answers.json  (saved by the Twin Interview page)
Output  data/benchmark/phase2/<pid>/
          train.jsonl     facts the twin may use
          test.jsonl      held-out questions it has never seen (the real test)
          baseline.jsonl  the person's own reworded-question pairs -> human
                          consistency ceiling, i.e. the best any twin could score
          profile.json    behaviour profile (see persona.py)
          consent.json    who consented, to what, when

Same layout as the Twin-2K-500 participants, so the demo and the evaluation
scripts treat a real person exactly like a benchmark participant.

  python src/load_phase2.py --answers phase2_answers.json
"""
import argparse
import json
import random
from pathlib import Path

from persona import build_profile, save_profile, first_name

TEST_FRACTION = 0.25
# Sections worth holding out: ones with real informational content. Identity
# questions ("which city do you call home") are kept in train -- holding those
# out tests nothing except whether the twin can hallucinate a city.
HOLDOUT_SECTIONS = ("What would you do?", "What you believe", "How you tend to be",
                    "Either or", "In your own words", "Everyday preferences")


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def convert(answers_path, out_root="data/benchmark/phase2", pid="phase2_me",
            test_fraction=TEST_FRACTION, seed=0):
    with open(answers_path, encoding="utf-8") as f:
        blob = json.load(f)

    consent = blob.get("consent") or {}
    if not consent.get("name"):
        raise SystemExit("This answers file has no consent record -- refusing to build a twin from it.")

    answers = [a for a in blob.get("answers", []) if str(a.get("answer", "")).strip()]
    if len(answers) < 20:
        raise SystemExit(f"Only {len(answers)} answered questions. Fill in more of the interview first.")

    by_id = {a["id"]: a for a in answers}
    repeats = [a for a in answers if a.get("repeat_of")]
    originals = [a for a in answers if not a.get("repeat_of")]

    # Human consistency ceiling: same person, same question, different wording.
    baseline = []
    for r in repeats:
        src = by_id.get(r["repeat_of"])
        if src:
            baseline.append({"question": src["question"], "answer": src["answer"],
                             "reworded_question": r["question"], "reworded_answer": r["answer"],
                             "section": src.get("section", "")})
    repeated_ids = {r["repeat_of"] for r in repeats}

    # Seeded stratified split, per section, over questions with no repeat twin.
    rng = random.Random(seed)
    train, test = [], []
    by_section = {}
    for a in originals:
        by_section.setdefault(a.get("section", ""), []).append(a)
    for sec, rows in by_section.items():
        rows = sorted(rows, key=lambda r: r["id"])
        eligible = [r for r in rows if sec in HOLDOUT_SECTIONS and r["id"] not in repeated_ids]
        rng.shuffle(eligible)
        n_test = int(round(len(eligible) * test_fraction))
        held = {r["id"] for r in eligible[:n_test]}
        for r in rows:
            rec = {"question": r["question"], "answer": r["answer"],
                   "section": sec, "type": r.get("type", "text"), "id": r["id"]}
            (test if r["id"] in held else train).append(rec)

    out = Path(out_root) / pid
    out.mkdir(parents=True, exist_ok=True)
    n_tr = _write(out / "train.jsonl", train)
    n_te = _write(out / "test.jsonl", test)
    n_bl = _write(out / "baseline.jsonl", baseline)

    profile = build_profile(answers, name=first_name(consent["name"]))
    save_profile(profile, out / "profile.json")
    with open(out / "consent.json", "w", encoding="utf-8") as f:
        json.dump(consent, f, ensure_ascii=False, indent=2)

    print(f"{out}\n  train {n_tr}   held-out test {n_te}   consistency pairs {n_bl}")
    print(f"  profile: {len(profile.traits)} traits, {len(profile.precedents)} reaction precedents")
    for line in profile.trait_lines():
        print("    " + line)
    return {"dir": str(out), "train": n_tr, "test": n_te, "baseline": n_bl, "profile": profile}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True, help="phase2_answers.json from the interview page")
    ap.add_argument("--out_root", default="data/benchmark/phase2")
    ap.add_argument("--pid", default="phase2_me")
    ap.add_argument("--test_fraction", type=float, default=TEST_FRACTION)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    convert(a.answers, a.out_root, a.pid, a.test_fraction, a.seed)
