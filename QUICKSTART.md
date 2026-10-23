# Quickstart

On a Mac (Apple Silicon, 16 GB): open `DeferralTwin.app`. It sets itself up,
then walks you through consent, the interview, your voice and photo, and the
build. See `DESKTOP.md` for how it works, and how to build and test it.

The Colab notebook (`notebooks/twin_demo.ipynb`) still works for developers
with a GPU, but it is no longer the way a person uses the twin.

## What a person has to supply

1. **Interview answers** — the Twin Interview page, saved as `phase2_answers.json`
2. **A voice clip** — 30–60 seconds of them speaking, any format
3. **A photo** — front-facing, whole face in frame, mouth closed, good light

Nothing else. Everything below is automatic.

## What happens to it

| Step | File | What it does |
|---|---|---|
| Dataset | `src/load_phase2.py` | splits the answers into facts the twin may use, held-out questions it is tested on, and the reworded pairs that set the human consistency ceiling |
| Behaviour profile | `src/persona.py` | computes trait scores, decision style and reaction precedents from the answers — deterministic, not guessed by a model |
| Retrieval | `src/rag_pipeline.py` | embeds the facts, retrieves by cosine similarity |
| Answering | `src/deferral.py` | three tiers: grounded answer, inference from the profile (labelled as such), or "I don't know" |
| Correction | `give_feedback` → `add_fact` | fixes an answer the twin got wrong; becomes retrievable memory immediately |
| Volunteered input | `add_note` → `add_fact` | anything the person adds unprompted, any time; each line becomes its own fact |
| Persistence | `notes.jsonl`, `feedback_demo.jsonl` | both logs are replayed on startup, so the twin never forgets what it was told |
| Voice | `src/multimodal.py` (XTTS-v2) | the person's cloned voice, English or Hindi |
| Avatar | Wav2Lip + GFPGAN | lip-synced talking head |
| One call | `src/build_twin.py` | all of the above, plus a Drive backup |

## Evaluation

- Phase 1 (`src/eval_benchmark.py`): 100 Twin-2K-500 participants. **Unchanged** — do not
  edit this file, the published numbers come from it.
- Phase 2 (`src/eval_phase2.py`): the real subject, scored against their own consistency ceiling.

## Consent

Voice and face belong only to the subject who consented on the interview page.
Benchmark participants are anonymous, so `build_twin` enables voice and avatar
for the subject's own twin only (`mm_pids`).
