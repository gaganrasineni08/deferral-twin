"""
Downloads Twin-2K-500 and converts it into train/test/baseline JSONL per participant.

Usage:
  python src/load_twin2k500.py --n_participants 100
"""
import argparse
import json
from pathlib import Path

import pandas as pd


def load_raw_dataset(config_name: str = "wave_split"):
    from datasets import load_dataset
    return load_dataset("LLM-Digital-Twin/Twin-2K-500", config_name)


def _stringify_answer(answers: dict) -> str:
    if not isinstance(answers, dict):
        return str(answers)
    if "SelectedText" in answers:
        val = answers["SelectedText"]
        return "; ".join(str(v) for v in val) if isinstance(val, list) else str(val)
    if "Text" in answers:
        val = answers["Text"]
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, dict):
                    parts.extend(f"{k}: {v}" for k, v in item.items())
                else:
                    parts.append(str(item))
            return "; ".join(parts)
        return str(val)
    if "Values" in answers:
        val = answers["Values"]
        return "; ".join(str(v) for v in val) if isinstance(val, list) else str(val)
    return json.dumps(answers, ensure_ascii=False)


def parse_persona_json(json_str: str) -> list:
    try:
        blocks = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return []

    qa_pairs = []
    for block in blocks:
        for q in block.get("Questions", []):
            if q.get("is_descriptive"):
                continue
            qtype = q.get("QuestionType", "")
            qtext = q.get("QuestionText", "").strip()
            answers = q.get("Answers", {})

            if qtype == "Matrix" and "Rows" in q:
                rows = q["Rows"]
                selected = answers.get("SelectedText", [])
                if not isinstance(selected, list):
                    selected = [selected]
                for row, ans in zip(rows, selected):
                    if qtext and row:
                        qa_pairs.append({"question": f"{qtext} -- {row}", "answer": str(ans)})
            elif qtype == "Slider" and "Statements" in q:
                statements = q["Statements"]
                values = answers.get("Values", [])
                if not isinstance(values, list):
                    values = [values]
                for stmt, val in zip(statements, values):
                    label = f"{qtext} -- {stmt}" if stmt else qtext
                    qa_pairs.append({"question": label, "answer": str(val)})
            else:
                ans_str = _stringify_answer(answers)
                if qtext and ans_str:
                    qa_pairs.append({"question": qtext, "answer": ans_str})
    return qa_pairs


def build_participant_splits(df: pd.DataFrame, n_participants: int, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for _, row in df.head(n_participants).iterrows():
        pid = row["pid"]
        train_qa = parse_persona_json(row["wave1_3_persona_json"])
        test_qa = parse_persona_json(row["wave4_Q_wave4_A"])
        baseline_qa = parse_persona_json(row["wave4_Q_wave1_3_A"])

        pdir = out_dir / f"participant_{pid}"
        pdir.mkdir(parents=True, exist_ok=True)

        for fname, data in [("train.jsonl", train_qa), ("test.jsonl", test_qa), ("baseline.jsonl", baseline_qa)]:
            with open(pdir / fname, "w", encoding="utf-8") as f:
                for item in data:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")

        summary.append({"participant_id": str(pid), "n_train": len(train_qa), "n_test": len(test_qa), "n_baseline": len(baseline_qa)})

    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Built splits for {len(summary)} participants -> {out_dir}")
    if summary:
        print(f"Example: {summary[0]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n_participants", type=int, default=100)
    parser.add_argument("--out_dir", default="data/benchmark/twin2k500")
    parser.add_argument("--config", default="wave_split", choices=["wave_split", "full_persona"])
    args = parser.parse_args()

    print("Loading Twin-2K-500 (requires internet access to huggingface.co)...")
    ds = load_raw_dataset(args.config)
    df = ds["data"].to_pandas()
    print(f"Loaded {len(df)} participants. Columns: {list(df.columns)}")

    build_participant_splits(df, args.n_participants, Path(args.out_dir))
