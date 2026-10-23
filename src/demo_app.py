"""
Live demo interface for the Deferral-Aware Digital Twin (Gradio).

What it shows:
  1. Pick a person -> their twin is built from their data (RAG over their answers).
  2. Ask a question (or pick a held-out one) -> the twin answers, makes a
     best guess, or DEFERS ("I don't know"), with the confidence and the
     evidence it used.
  3. For held-out questions the real answer is revealed with a similarity score.
  4. "Teach the twin": give the answer you expected -> it becomes live memory
     (self-learning loop). Ask again to see the change.
  5. A Results tab with the benchmark numbers and charts.

Usage (Colab, after the model + `real_llm_generate_batch` are loaded):
  import demo_app
  demo = demo_app.build_app("data/benchmark/twin2k500",
                            lambda sp, q, ctx: real_llm_generate_batch([sp], [q], max_new_tokens=64)[0])
  demo.launch(share=True)
"""
import json
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from rag_pipeline import RagStore, load_config      # noqa: E402
from deferral import DeferralAwareTwin               # noqa: E402
from persona import load_profile                     # noqa: E402

TIER_LABEL = {
    "confident_answer": "🟢 Confident answer",
    "soft_answer": "🟡 Inferred from how they usually are (not something they stated)",
    "hard_defer": "🔴 Deferred: \"I don't know\"",
}


def _load_jsonl(p: Path):
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


class TwinSession:
    """One twin = one person's RAG store + deferral logic. UI-independent."""

    def __init__(self, bench_dir, pid, llm_fn):
        pdir = Path(bench_dir) / pid
        train = _load_jsonl(pdir / "train.jsonl")
        self.test = _load_jsonl(pdir / "test.jsonl")
        facts = pdir / "facts_demo.jsonl"
        with open(facts, "w", encoding="utf-8") as f:
            for r in train:
                f.write(json.dumps({"text": f"Q: {r['question']}\nA: {r['answer']}",
                                    "source": "twin2k500_train"}, ensure_ascii=False) + "\n")
        cfg = dict(load_config())
        cfg["paths"] = dict(cfg["paths"], qa_facts_file=str(facts),
                            feedback_log=str(pdir / "feedback_demo.jsonl"),
                            note_log=str(pdir / "notes.jsonl"))
        cfg["rag"] = dict(cfg["rag"], index_path=str(pdir / "faiss_demo.bin"))
        self.store = RagStore(cfg)
        self.store.build()
        self.store.load()

        # A real subject has a behaviour profile; benchmark participants do not,
        # so Phase 1 keeps its original behaviour and numbers.
        prof_path = pdir / "profile.json"
        self.profile = load_profile(prof_path) if prof_path.exists() else None
        person = (self.profile.name if self.profile
                  else f"survey participant {pid.split('_')[-1]}")
        self.twin = DeferralAwareTwin(llm_generate_fn=llm_fn, rag=self.store, cfg=cfg,
                                      person_name=person, profile=self.profile)

        # Replay every correction this person has ever made, so the twin does
        # not forget what it was taught when the runtime restarts.
        self.n_taught = self.n_noted = 0
        self.feedback_log = pdir / "feedback_demo.jsonl"
        self.note_log = pdir / "notes.jsonl"
        for log, src, render in ((self.feedback_log, "user_correction",
                                  lambda r: f"Q: {r['question']}\nA: {r['expected_answer']}"),
                                 (self.note_log, "user_note", lambda r: r["text"])):
            if not log.exists():
                continue
            with open(log, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, KeyError):
                        continue
                    self.store.add_fact(render(rec), source=src, persist=False)
                    if src == "user_correction":
                        self.n_taught += 1
                    else:
                        self.n_noted += 1

    def ask(self, question):
        r = self.twin.respond(question)
        evidence = [[round(x["score"], 3), x["source"], x["text"].replace("\n", "  ")[:220]]
                    for x in r.retrieved_context]
        return {"answer": r.answer, "tier": r.tier, "confidence": r.confidence,
                "evidence": evidence}

    def similarity(self, a, b):
        from sentence_transformers import util
        e = self.store.embedder.encode([a, b], normalize_embeddings=True)
        return float(util.cos_sim(e[0], e[1]))

    def teach(self, question, twin_answer, expected, tier, conf):
        self.twin.give_feedback(question, twin_answer, expected, tier=tier, confidence=conf)
        self.n_taught += 1

    def note(self, text):
        """Free-form knowledge the person volunteers, outside any question."""
        added = self.twin.add_note(text)
        self.n_noted += len(added)
        return added


def respond_stream(sess, mm, question, hidden, speak=False, video=False, lang="en"):
    """Generator yielding (answer_md, truth_md, evidence, teach_msg, last, audio, video).
    Text appears first; voice and avatar video are added as they finish."""
    question = (question or "").strip()
    if not question:
        yield "Type a question first.", "", [], "", None, None, None
        return
    out = sess.ask(question)
    head = (f"**{TIER_LABEL[out['tier']]}**  ·  confidence **{out['confidence']:.2f}**\n\n"
            f"> {out['answer']}")
    truth_md = ""
    if hidden and hidden.get("q") == question:
        sim = sess.similarity(out["answer"], hidden["truth"]) if out["tier"] != "hard_defer" else None
        truth_md = f"**Real answer from this person:** {hidden['truth']}"
        if sim is not None:
            truth_md += f"\n\nSimilarity of twin answer to real answer: **{sim:.2f}**"
    last = {"q": question, "a": out["answer"], "tier": out["tier"], "conf": out["confidence"]}
    ev = out["evidence"]
    if mm is None or not (speak or video):
        yield head, truth_md, ev, "", last, None, None
        return

    yield head + "\n\n*🔊 generating voice...*", truth_md, ev, "", last, None, None
    audio = None
    try:
        audio = mm.speak(out["answer"], lang=lang)
    except Exception as e:                                   # keep the demo alive
        yield head + f"\n\n*Voice failed: {type(e).__name__}: {e}*", truth_md, ev, "", last, None, None
        return
    if not video:
        yield head, truth_md, ev, "", last, audio, None
        return
    yield head + "\n\n*🎬 animating avatar (about a minute)...*", truth_md, ev, "", last, audio, None
    try:
        vid = mm.animate(audio)
    except Exception as e:
        yield head + f"\n\n*Avatar failed: {type(e).__name__}: {e}*", truth_md, ev, "", last, audio, None
        return
    yield head, truth_md, ev, "", last, audio, vid


RESULTS_MD = """
### Phase 1: benchmark on Twin-2K-500 (100 participants, 9,564 held-out questions)

| | Twin | Human re-test (ceiling) |
|---|---|---|
| Mean semantic similarity to true answer | **0.332** (95% CI 0.325 to 0.339) | **0.892** |

| Deferral tier | Share of questions | Twin score |
|---|---|---|
| Hard defer ("I don't know") | 1.0% | n/a |
| Best guess (low confidence) | 19.4% | 0.160 |
| Confident answer | 79.6% | 0.373 |

Treating low-confidence guesses as deferrals raises accuracy to **0.373 at 80% coverage**,
an improvement for all 100 participants (mean +0.042).

### Self-learning loop (9 participants)
Five corrections lift similarity from **0.350 to 0.563**. A control that used *another
person's* answers gives the same lift (0.570), so most of this gain is the twin learning the
**answer format**; a small personal effect appears by 20 corrections (+0.023, not yet significant).

*Caveat: the metric compares embeddings, so a sentence scored against a one-word survey label
is penalised even when it is right. Scores are conservative.*
"""


def build_app(bench_dir, llm_fn, default_pid=None, figures_dir=None, mm=None, mm_pids=None):
    """mm: optional multimodal.MultiModal (cloned voice + avatar of the CONSENTING subject).
    mm_pids: personas allowed to use voice/avatar (default: all; for the final demo pass only
    the subject's own twin, e.g. ("phase2_me",), since the survey participants are anonymous)."""
    import gradio as gr
    bench = Path(bench_dir)
    pids = sorted(p.name for p in bench.iterdir()
                  if p.is_dir() and (p / "train.jsonl").exists())
    if not pids:
        raise RuntimeError(f"No participants found in {bench_dir}")
    figures = Path(figures_dir) if figures_dir else Path(__file__).resolve().parent.parent / "docs" / "figures"
    cache = {}

    def _memory_md(p):
        """Everything the person has added by hand, newest first."""
        if p not in cache:
            return "*Nothing added yet in this session.*"
        st = cache[p].store
        mine = [f for f in st.facts if f["source"] in ("user_correction", "user_note")]
        if not mine:
            return "*The twin has no hand-added facts yet — only the interview answers.*"
        lines = [f"**{len(mine)}** fact(s) you added, on top of the interview:", ""]
        for f in reversed(mine[-25:]):
            kind = "correction" if f["source"] == "user_correction" else "your input"
            lines.append(f"- `{kind}` {f['text'].replace(chr(10), ' — ')[:200]}")
        return "\n".join(lines)

    def _profile_md(p):
        """Read profile.json straight from disk -- no embedding model needed."""
        f = bench / p / "profile.json"
        if not f.exists():
            return ("*No behaviour profile for this persona.* Benchmark participants are "
                    "anonymous survey respondents, so the twin answers from their stored "
                    "answers only. A profile is built for a consenting subject who has "
                    "completed the interview.")
        return load_profile(f).as_markdown()

    def sess(pid):
        if pid not in cache:
            cache[pid] = TwinSession(bench_dir, pid, llm_fn)
        return cache[pid]

    def pick(pid):
        s = sess(pid)
        row = random.choice(s.test)
        return row["question"], {"q": row["question"], "truth": row["answer"]}

    def ask(pid, question, hidden, speak, video, lang):
        allowed = mm is not None and (mm_pids is None or pid in mm_pids)
        yield from respond_stream(sess(pid), mm if allowed else None, question, hidden,
                                  bool(speak), bool(video), "hi" if lang == "Hindi" else "en")

    def teach(pid, expected, last):
        if not last:
            return "Ask a question first."
        expected = (expected or "").strip()
        if not expected:
            return "Type what you expected the twin to say."
        s = sess(pid)
        s.teach(last["q"], last["a"], expected, last["tier"], last["conf"])
        return (f"Learned. The twin now has **{s.n_taught}** correction(s) in memory, saved to "
                f"disk — it keeps them after a restart. Ask the same question again to see the change.")

    def add_note(p, text):
        if not (text or "").strip():
            return "Type something first.", _memory_md(p), text
        s = sess(p)
        added = s.note(text)
        return (f"Added **{len(added)}** fact(s) to the twin's memory. It has **{s.n_noted}** "
                f"from you in total, plus **{s.n_taught}** correction(s). Ask it something "
                f"related and it will use them.", _memory_md(p), "")

    with gr.Blocks(title="Deferral-Aware Digital Twin") as demo:
        gr.Markdown("# Deferral-Aware Digital Twin\n"
                    "A consent-based twin that answers like a person, and **says \"I don't know\" "
                    "instead of making up an opinion.**")
        with gr.Tab("Talk to the twin"):
            pid = gr.Dropdown(pids, value=default_pid or pids[0], label="Whose twin?")
            with gr.Row():
                q = gr.Textbox(label="Ask the twin", scale=4)
                pickb = gr.Button("🎲 Pick a held-out question", scale=1)
            if mm is not None:
                with gr.Row():
                    speak = gr.Checkbox(value=True, label="🔊 Speak in cloned voice")
                    video = gr.Checkbox(value=False, label="🎬 Avatar video (slower)")
                    lang = gr.Radio(["English", "Hindi"], value="English", label="Voice language")
            else:
                speak, video, lang = gr.State(False), gr.State(False), gr.State("English")
            askb = gr.Button("Ask", variant="primary")
            answer = gr.Markdown()
            audio_out = gr.Audio(label="Twin voice", autoplay=True, type="filepath",
                                 interactive=False, visible=mm is not None)
            video_out = gr.Video(label="Twin avatar", interactive=False, visible=mm is not None)
            truth = gr.Markdown()
            evidence = gr.Dataframe(headers=["similarity", "source", "evidence used"],
                                    label="Evidence the twin retrieved", wrap=True)
            gr.Markdown("#### Teach the twin (self-learning)")
            with gr.Row():
                expected = gr.Textbox(label="What did you expect the twin to say?", scale=4)
                teachb = gr.Button("Teach the twin", scale=1)
            teach_msg = gr.Markdown()
            hidden = gr.State(None)
            last = gr.State(None)
            pickb.click(pick, [pid], [q, hidden])
            ask_in = [pid, q, hidden, speak, video, lang]
            ask_out = [answer, truth, evidence, teach_msg, last, audio_out, video_out]
            askb.click(ask, ask_in, ask_out)
            q.submit(ask, ask_in, ask_out)
            teachb.click(teach, [pid, expected, last], [teach_msg])
        with gr.Tab("Tell it about you"):
            gr.Markdown(
                "The interview is a snapshot. Anything you add here goes straight into the "
                "twin's memory and is used from your very next question — no retraining, "
                "and it survives a restart.\n\n"
                "Write freely. **Each line becomes a separate fact**, so put one thought per "
                "line rather than a single long paragraph.")
            note_box = gr.Textbox(
                label="Anything the twin should know about you", lines=8,
                placeholder=("I moved to a new lab last month and I'm enjoying it.\n"
                             "I say no to things far more than I used to.\n"
                             "When a plan falls apart I'd rather rebuild it than push through."))
            with gr.Row():
                noteb = gr.Button("Add to the twin's memory", variant="primary", scale=1)
                clearb = gr.Button("Clear box", scale=1)
            note_msg = gr.Markdown()
            gr.Markdown("#### What you have added so far")
            mem_md = gr.Markdown("*Ask the twin something first, then this fills in.*")
            refreshb = gr.Button("Refresh")
            noteb.click(add_note, [pid, note_box], [note_msg, mem_md, note_box])
            clearb.click(lambda: "", None, [note_box])
            refreshb.click(_memory_md, [pid], [mem_md])
            pid.change(_memory_md, [pid], [mem_md])
        with gr.Tab("Behaviour profile"):
            gr.Markdown(
                "What the twin knows about *how this person is*, computed from their own "
                "answers. On a question they never answered, the twin reasons from this "
                "instead of guessing blindly — and says that is what it is doing.")
            prof_md = gr.Markdown(_profile_md(default_pid or pids[0]))
            pid.change(_profile_md, [pid], [prof_md])
        with gr.Tab("Results"):
            gr.Markdown(RESULTS_MD)
            for fn in ("risk_coverage_curve.png", "feedback_curve_control.png"):
                if (figures / fn).exists():
                    gr.Image(str(figures / fn), label=fn.replace("_", " ").replace(".png", ""),
                             show_label=True, interactive=False)
    return demo
