# Build brief — Deferral-Aware Digital Twin

You are taking over a working research project and turning it into a single
self-service application. **Most of the backend already exists and is tested.**
Read this whole file before writing code. The biggest risk is rebuilding things
that already work, or adding model training, which this project deliberately
does not do.

---

## 1. What the project is

A digital twin of a consenting person that answers questions the way they would,
and **says "I don't know" instead of inventing an answer** when it has no
grounds. The deferral behaviour is the research contribution, not a side feature.
Nothing you build may undermine it.

Three answer tiers, decided by retrieval confidence (raw cosine similarity):

| Confidence | Tier | Behaviour |
|---|---|---|
| `>= 0.35` | `confident_answer` | answers from the person's own stored answers |
| `0.15 – 0.35` | `soft_answer` | reasons from the behaviour profile, **labelled as an inference** |
| `< 0.15` | `hard_defer` | refuses: "I don't know" — no model call at all |

Validated in two phases:
- **Phase 1** — 100 participants from the public Twin-2K-500 dataset. Numbers are published in the report. **Frozen.**
- **Phase 2** — one real consenting subject (the project author), which is what the app serves.

---

## 2. Hard constraints — do not break these

1. **There is no model training.** No fine-tuning, no LoRA, no gradient updates.
   The language model is frozen and used only for generation. The twin learns by
   adding facts to a retrieval index, which is why corrections take effect
   instantly. If a spec or a user says "train the model", it means "add facts to
   memory". Do not build a training loop.
2. **Do not modify `src/eval_benchmark.py` or anything under
   `data/benchmark/twin2k500/`.** The published Phase 1 numbers come from that
   script and must stay reproducible. `src/eval_phase2.py` is the one you may
   touch.
3. **Deferral must stay honest.** The confidence that drives the gate is the
   **raw** cosine similarity. Person-stated facts get a ranking boost, but the
   boost must never reach the gate — otherwise adding facts would fake
   confidence. There is a test for this; keep it passing.
4. **Consent boundary.** Voice cloning and the avatar are enabled **only** for
   the person who consented on the interview page. Twin-2K-500 participants are
   anonymous survey respondents and must never be given a voice or a face. This
   is enforced by `mm_pids` in `demo_app.build_app`. Keep that enforcement.
5. **Runs entirely on the developer's Mac — Apple Silicon, 16 GB unified memory.**
   No Colab, no cloud GPU, no notebook in the user's path. The app is a local
   server the user opens in a browser. See §8 for the macOS stack and the memory
   budget; Colab remains optional for Phase 1 re-runs only.
6. **Never commit or display a Hugging Face token or any API key.**

---

## 3. What already exists and is tested — do not rewrite

All under `src/`. Tested with stand-in embedder/FAISS/LLM; the real models have
been verified on Colab separately.

| File | What it does | Status |
|---|---|---|
| `build_twin.py` | **One call does the whole backend.** `build(answers, voice, photo, llm_fn, drive)` → splits the dataset, builds the index, computes the profile, converts audio, fixes photo rotation, wires voice + avatar, backs up to Drive, restores from Drive after a restart. | works |
| `load_phase2.py` | interview JSON → `train.jsonl` / `test.jsonl` / `baseline.jsonl` / `profile.json` / `consent.json`. Seeded stratified 25% held-out. | works |
| `persona.py` | behaviour profile: trait scores, decision style, reaction precedents. Deterministic, computed from Likert items — **not** guessed by a model. | works |
| `rag_pipeline.py` | embeddings (`all-mpnet-base-v2`) + FAISS, `retrieve()` returns raw-cosine confidence, `add_fact()` adds to live memory. | works |
| `deferral.py` | the three tiers, `give_feedback()` (corrections), `add_note()` (volunteered facts). | works |
| `demo_app.py` | the current Gradio UI + `TwinSession`, which replays corrections and notes on startup. | works, **needs replacing — see §4** |
| `multimodal.py` | text → cloned voice (XTTS-v2) → lip-synced video (Wav2Lip + GFPGAN). | works |
| `eval_phase2.py` | scores the twin on held-out questions against the subject's own consistency ceiling. | works |
| `setup_avatar.sh`, `setup_wav2lip.sh` | the three isolated environments these models need. | works |
| `notebooks/twin_demo.ipynb` | the current end-to-end Colab run. | works |

Read `QUICKSTART.md` for how these fit together.

### The existing one-call backend

```python
from build_twin import build
twin = build(answers="phase2_answers.json", voice="voice.m4a", photo="me.jpg",
             llm_fn=my_llm, drive="/content/drive/MyDrive/twin_assets")
twin.app().launch(share=True)        # current Gradio UI
```

`llm_fn(system_prompt, question, context) -> str`. The notebook shows how to
build it from Phi-3-mini in 4-bit.

---

## 4. Your job

**Goal:** a person who has never seen this project goes from nothing to a talking
twin without touching a notebook cell, a file path or a terminal.

Today they must: open a separate questionnaire page, save a JSON, open Colab,
upload a zip, run ten cells, upload three more files. **That is the gap.**
Everything after "they have the three files" is already automatic — your work is
the front of the funnel and the interface.

**It all runs on the Mac.** Not only is Colab out of the user's path, it is out
of yours: no notebook to keep alive, no tunnel, nothing to re-start before a
demo. See §8 for the stack.

### 4.1 Required flow

**Decided: a macOS desktop app that sets itself up. The authoritative
screen-by-screen spec is §9.** This section is the summary.

One application, four onboarding screens:

1. **Consent** — the three agreements and their name. Nothing proceeds without
   it. Record it exactly as `load_phase2.py` expects (see §5.3).
2. **Interview** — the ~110 questions *inside the app*, not a separate page.
   Autosave progress so closing the tab loses nothing. Show progress.
   The question set and its section labels must match §5.1 exactly, because
   `persona.py` maps trait items by question text.
3. **Upload** — voice clip and photo, with live guidance (front-facing, whole
   face, mouth closed, good light; 30–60 s of speech). Accept whatever a phone
   produces; the backend converts.
4. **Build** — one button. Show real progress through the stages, then open the
   chat. Target: under 2 minutes once the models are loaded.

Then the main interface:

- **Ask** — question in, answer out, with the tier shown clearly and differently
  for each of the three (a confident answer, an inference, and a refusal must be
  visually distinct — the refusal is the thing to show off, not hide).
- **Evidence** — what the twin retrieved and the similarity, collapsible.
- **Correct** — "that's not what I'd say" → the right answer → it takes effect
  on the next question. Use `give_feedback()`.
- **Tell it about you** — free-form input any time, each line a separate fact.
  Use `add_note()`.
- **Profile** — the computed behaviour profile. Use `Profile.as_markdown()`.
- **Voice / avatar** — toggles. Stream text first, then audio, then video; the
  video takes 1–2 minutes and must never block the text.
- **Results** — the Phase 1 and Phase 2 numbers, for the examiners.

### 4.2 Interface quality

The current Gradio UI is functional and plain. Make it something that reads as a
product. You may keep Gradio with real styling, or serve a proper frontend with
the Python behind an API. **Decided:** the user opens `DeferralTwin.app` and the
interface appears in the app's own window (§9.2). `./run.sh` stays as the
developer entry point — same server, opened in a browser instead:

```bash
./run.sh           # developers only: starts the server, opens http://localhost:8000
```

A FastAPI backend with a static frontend is the better fit here, because the
build step needs real progress reporting and the avatar needs a background job
queue — both awkward in Gradio. If you keep Gradio, the progress and the
non-blocking avatar still have to work.

### 4.3 Must keep working

- Corrections and volunteered facts persist to disk and are replayed on startup
  (`feedback_demo.jsonl`, `notes.jsonl`).
- Restarting the app loses nothing. `build_twin.build()` with no arguments
  restores answers, voice and photo from its backup directory; point that at a
  local folder on Mac instead of Google Drive.
- `eval_phase2.py` still runs against whatever the app produces.
- First run sets itself up: if a model or a voice/avatar environment is missing,
  the app says so plainly and offers to install it, rather than crashing with a
  stack trace.

---

## 5. Data contracts

### 5.1 Interview question set

97 questions in 7 sections, plus 12 reworded repeats in a "Quick re-check"
section. The section labels are load-bearing:

| Section | n | Type | Used for |
|---|---|---|---|
| `About you` | 12 | free text | background facts |
| `Everyday preferences` | 13 | choice | background, some held out |
| `What would you do?` | 16 | free text | **reaction precedents** |
| `What you believe` | 14 | Likert 1–5 | strongly-held values |
| `How you tend to be` | 20 | Likert 1–5 | **trait scores** (4 items × 5 traits) |
| `Either or` | 12 | choice | decision style |
| `In your own words` | 10 | free text | precedents |
| `Quick re-check` | 12 | mixed | consistency ceiling, via `repeat_of` |

The exact wording is in the existing interview page and in `persona.TRAIT_ITEMS`,
which matches items by a fragment of the question text. **If you reword a Likert
item in `How you tend to be`, update `TRAIT_ITEMS` in the same commit**, or that
trait silently loses an item.

### 5.2 Likert answer format

`"Agree (4 of 5)"` — the trailing `(n of 5)` is parsed by `persona.likert_value`.
Keep it.

### 5.3 `phase2_answers.json`

```json
{
  "version": 1,
  "created": "2026-10-08T...Z",
  "consent": { "name": "Full Name", "agreed": ["...", "...", "..."], "timestamp": "..." },
  "answers": [
    { "id": "q001", "section": "About you", "type": "text",
      "question": "Which city do you call home?", "answer": "..." },
    { "id": "r01", "section": "Quick re-check", "type": "choice",
      "question": "...", "answer": "...", "repeat_of": "q014" }
  ]
}
```

`consent.name` is required — `load_phase2.py` refuses to build a twin without it.
Ids are `q001…q097` and `r01…r12`.

### 5.4 Built dataset

`data/benchmark/phase2/<pid>/` with `train.jsonl`, `test.jsonl` (both
`{question, answer, section, type, id}`), `baseline.jsonl`
(`{question, answer, reworded_question, reworded_answer, section}`),
`profile.json`, `consent.json`. Same shape as a Twin-2K-500 participant
directory, which is why one UI serves both.

---

## 6. Pitfalls already hit — do not rediscover these

**Avatar**
- SadTalker with `--still --preprocess full` produces **almost no lip movement**.
  Raising `--expression_scale` makes the eyes wander instead. It was abandoned.
- **Wav2Lip is what works** for lip-sync, then GFPGAN over every frame for
  clarity — Wav2Lip alone is sharp enough in motion but visibly soft. This
  combination is implemented in `multimodal.py` and verified.
- A photo cropped above the mouth produces no lip-sync at all, silently. Validate
  the uploaded photo: whole face, mouth visible, mouth closed.
- Phone photos arrive rotated. `ImageOps.exif_transpose` is already applied in
  `build_twin.prepare_photo`; without it face detection fails.

**Environments**
- SadTalker, Wav2Lip and the main runtime need **three separate Python
  environments** — they pin conflicting torch and numpy versions. The setup
  scripts handle this. Do not try to merge them.
- XTTS needs `transformers>=4.57,<5`; v5 removed `isin_mps_friendly` and XTTS
  crashes on import. The pip resolver warns about gradio/diffusers — harmless.
- `COQUI_TOS_AGREED=1` must be set before XTTS loads.

**macOS / Apple Silicon**
- `bitsandbytes` is CUDA-only. The notebook's 4-bit loading **will not run on a
  Mac**. Use MLX instead — see §8.1.
- `faiss-cpu` installs cleanly on arm64; `faiss-gpu` does not exist for Mac.
- PyTorch MPS does not implement every op XTTS and Wav2Lip use, and silently
  falls back or errors. Run those two on CPU on Mac unless a benchmark shows MPS
  is both faster and correct.
- `numba 0.56.4`, which Wav2Lip's `librosa` pin needs, supports Python ≤ 3.10.
  Create that environment with Python 3.10 (`uv venv --python 3.10`), as the
  Colab setup script already does.
- Always `ffmpeg -nostdin` in any shell invocation — without it ffmpeg consumes
  the parent's stdin and silently corrupts whatever runs next.
- Reuse one sentence-transformer instance. Creating one per participant leaked
  memory badly enough to OOM on the tenth participant on a 16 GB GPU.

**Retrieval**
- `RagStore.build()` writes the index to disk but does **not** set `self.index`.
  Call `load()` after `build()`, or the first `retrieve()` lazily reloads.
- Corrections are retrieved, not trained. A contradicting new fact does not
  delete the old one; it wins by the ranking boost. If you implement proper
  contradiction handling, make it explicit and testable.

---

## 7. Definition of done

1. A new person completes consent → interview → uploads → one button, and is
   talking to their twin, with no notebook cells and no file paths.
2. All three tiers are visibly distinct, and a question outside the interview
   produces a refusal rather than an invention.
3. A correction and a volunteered fact both change the next answer, and both
   survive a restart.
4. Voice and avatar work for the consenting subject only.
5. `eval_phase2.py` runs and reports twin score, human ceiling, and percentage of
   ceiling.
6. One command starts the whole thing on a 16 GB Apple Silicon Mac, with no
   Colab, no notebook and no manual file paths.
7. Restarting the app loses nothing — answers, corrections, notes and assets are
   all on disk.
8. `src/eval_benchmark.py` and the Phase 1 data are untouched.
9. `DeferralTwin.app`, copied to a clean Mac with nothing installed, completes
   §9.3 setup unattended and reaches the consent screen. No terminal, no Ollama,
   no Homebrew, no pre-installed Python.
10. After setup, the app works with networking switched off.
11. "Delete my twin" and "Remove everything" leave nothing behind (§9.7).

---

## 8. The macOS stack

Target machine: Apple Silicon, 16 GB unified memory. Everything below runs on
it. The figures are **estimates — benchmark them first (§8.4) and design around
what you measure**, not around these numbers.

### 8.1 Language model — replace bitsandbytes

`bitsandbytes` is CUDA-only. On Mac use one of:

- **MLX (`mlx-lm`) — decided.** Apple's own framework, fastest on this hardware,
  and it lives inside the app's own environment, so first-run setup covers it.
  Use a 4-bit Phi-3-mini MLX build. The app owns load/unload and memory.
- **Ollama — not used.** It is a separate application the person would have to
  install themselves, which breaks "the app sets itself up". Do not add it back
  as a requirement.

Either way, keep the `llm_fn(system_prompt, question, context) -> str` interface
that `deferral.py` already expects, so nothing downstream changes. Put the
implementation behind `src/llm_backend.py` with a backend chosen by config, and
keep a Colab/CUDA path for Phase 1 re-runs.

Expect roughly 2–5 seconds per answer. That is the interactive path; keep it fast
and never block it on voice or video.

### 8.2 Memory budget

Approximate resident sizes: Phi-3-mini 4-bit ≈ 2.5 GB, XTTS-v2 ≈ 2 GB,
Wav2Lip + S3FD ≈ 1 GB, GFPGAN ≈ 0.5 GB, embedder ≈ 0.5 GB. All at once is
roughly 6.5 GB, which fits in 16 GB but leaves little room beside a browser and
an IDE.

So: **load lazily and release.** The embedder and the LLM stay resident. XTTS
loads on the first spoken answer. The avatar models load only inside the
background job and are released when it finishes. Never load all three on
startup.

### 8.3 Avatar: the slow part

Wav2Lip on CPU is on the order of minutes for a ten-second clip, and GFPGAN over
every frame is heavier still — potentially several minutes more. This is the one
feature that does not run at interactive speed on this hardware.

Design for that rather than fighting it:

1. **Text and voice never wait for video.** Answer text streams immediately,
   audio follows, video arrives later. A blocking request is a bug.
2. **Avatar off by default**, with an honest estimate on the toggle
   ("about N minutes") taken from the measured benchmark, not a guess.
3. **Background job queue.** Submitting returns a job id; the UI polls and shows
   progress; the user keeps asking questions meanwhile.
4. **GFPGAN off by default on Mac.** Raw Wav2Lip is soft but acceptable in
   motion; make sharpening an explicit opt-in.
5. **Pre-render for the demo.** A "prepare demo clips" screen that renders avatar
   answers for a chosen list of questions ahead of time and caches them by
   question. During the live demo those play instantly. This is the safety net —
   build it, do not rely on live generation in front of an audience.
6. Cache every rendered clip keyed by the answer text, so re-asking is free.

### 8.4 Benchmark before designing around any of this

First task, before the UI work. Measure on the actual Mac and write the results
into the repo as `BENCHMARK.md`:

- one LLM answer, cold and warm
- one XTTS utterance of about ten seconds
- Wav2Lip on a ten-second clip, with and without GFPGAN
- peak memory for each

Those numbers decide the defaults in §8.3. If the avatar turns out faster than
feared, raise the defaults. If it is worse, lean harder on pre-rendering.

### 8.5 Setup

**Superseded in detail by §9.3 (the first-run setup screen).** The principle
stands: the app installs what it needs, never hands the user a list of commands.
What setup must provide: the MLX model, the XTTS package and weights, the
Wav2Lip environment and its two weight files, ffmpeg (bundle a static build —
do not assume Homebrew). `src/setup_wav2lip.sh` already does
the Wav2Lip part and works on macOS with `uv`; adapt paths away from `/content`.

Keep the three isolated environments. They pin conflicting torch and numpy
versions and merging them will break.

### 8.6 Optional: offload heavy work to a Colab GPU

The app runs locally and must not depend on a network GPU — that is settled by
§4.2 and is the right call for a live demo. But some work is batch work, and
Google's **Colab CLI** lets a terminal agent drive a Colab runtime directly, so
you can use a T4 for the slow jobs without changing the architecture:

- **Phase 1 re-runs** (`eval_benchmark.py` over 100 participants) — hours on CPU.
- **Pre-rendering the demo avatar clips** (§8.3 item 5) — about a minute each on
  a T4 versus several on this Mac.
- **Verifying the CUDA path** still works after refactoring, so the Colab
  notebook does not rot.

```bash
uv tool install google-colab-cli     # macOS/Linux
colab whoami                         # verify auth BEFORE provisioning
colab new -s twin --gpu T4           # ONE persistent session
colab upload -s twin <files>         # explicit; nothing auto-syncs
colab exec -s twin -f script.py
colab download -s twin <artifacts>
colab stop -s twin                   # in a finally block, always
```

Use a persistent session: the avatar environments take about 15 minutes to
install on a fresh VM, and `colab run` tears the machine down after each command.
Free-tier T4 access is constrained, so treat GPU minutes as scarce. A leaked
session is the expensive mistake. `colab repl`/`console` are interactive and a
poor fit for an agent — prefer `exec`.

Rendered clips come back as files and are cached by answer text like any other,
so nothing in the app needs to know where they were made.

---

## 9. The desktop app — decided spec

### 9.1 Decisions (settled — ask before changing)

| Question | Decision | Why |
|---|---|---|
| Delivery | `DeferralTwin.app` in a `.dmg` | a double-click, not a terminal |
| Language model | MLX, inside the app (§8.1) | no separately installed software |
| People per install | **one twin** | consent and deletion stay simple |
| Platforms | macOS on Apple Silicon only | Windows is a separate project |
| Signing | ad-hoc only, no Apple Developer account | not needed for a term project |
| Network | needed for first-run setup only | fully offline afterwards |

### 9.2 Architecture

Two layers, because the heavy libraries cannot be bundled into one app (three
conflicting environments, roughly 10 GB of models — §6, §8):

- **The bootstrapper — the `.app` itself, kept small.** Contains a launcher, a
  native window (`pywebview`, which uses the system WebKit), and a bundled `uv`
  binary. It can run the setup screen **before any environment exists**, so the
  setup UI must depend on nothing but what is inside the `.app`.
- **The runtime — created on first launch** under
  `~/Library/Application Support/DeferralTwin/`:

```
DeferralTwin/
  envs/main/       FastAPI server, MLX, embedder, XTTS, the existing src/
  envs/wav2lip/    isolated Python env (setup_wav2lip.sh, paths moved off /content)
  models/          every weight file, downloaded once
  twin/            the person's data: the §5.4 layout + voice, photo, notes, corrections
  cache/clips/     rendered avatar videos, keyed by answer text
  logs/
  state.json       setup progress, so an interrupted setup resumes
```

After setup the bootstrapper starts the main server from `envs/main` and points
its window at it. Nothing writes inside the `.app` bundle; it must keep working
after the user moves or replaces it.

**Permissions must be declared or macOS kills the app.** The bundle's
`Info.plist` needs `NSMicrophoneUsageDescription` and
`NSCameraUsageDescription` with plain-language reasons. Verify early that the
`pywebview` window can actually capture camera and microphone on the target
macOS; if it cannot, capture in Python instead. A file chooser is always offered
as well, so a refused permission never blocks onboarding.

### 9.3 Part 1–2: install and first-run setup

1. User opens the `.dmg`, drags the app to Applications, opens it.
2. **Gatekeeper.** A copy built on the same Mac opens normally. A downloaded
   copy is blocked once: the user clicks **Done**, opens **System Settings →
   Privacy & Security**, and clicks **Open Anyway**. Control-click → Open no
   longer works on current macOS — do not tell users it does. Put these exact
   steps in a `README` inside the `.dmg`, with a screenshot.
3. **Welcome screen.** What is about to happen, how much disk it needs (measure
   the real total, do not guess), that it needs internet once and never again.
4. **Machine check** before downloading anything: Apple Silicon, memory, free
   disk with margin, macOS version. A failure says what is wrong and stops
   cleanly — never fail half-way through a 10 GB download.
5. **Setup screen.** One row per component with its own progress bar and size:
   language model, voice model, lip-sync model, face detector, face sharpener,
   search model, Hindi translator, then "preparing environments". Overall bar
   and a time estimate computed from measured speed.
6. **Resumable.** Every completed step is recorded in `state.json`; downloads
   resume partial files. Quitting, sleep or a dropped connection costs only the
   unfinished part. Show what is resuming.
7. **Self-test** at the end: one answer, one spoken sentence, a short lip-sync
   render. Green ticks, or a specific failure with a **Retry** button.

### 9.4 Part 3: creating the twin

8. **Consent** — the three agreements, the person's name, written exactly as
   §5.3. Nothing proceeds without it.
9. **Interview** — the §5.1 questions, section by section, progress bar. Every
   answer is saved to `twin/` as it is typed; reopening the app returns to the
   exact question. Write the same JSON as §5.3 so the existing pipeline is
   unchanged.
10. **Voice** — **Record** with a short paragraph to read aloud (about 45 s), or
    choose a file. Play it back. Reject too short, too quiet or clipped, saying
    which.
11. **Photo** — take one with the camera, or choose a file. Check immediately:
    exactly one face, whole face in frame, mouth visible. A failure names the
    problem ("your chin is cut off"). This check exists because a photo cropped
    above the mouth silently produces no lip movement (§6).
12. **Build** — one button calling `build_twin.build()`. Real per-stage progress.
    About two minutes. Then the main window.

### 9.5 Part 4: the main window

13. **Ask.** Three tiers, visually distinct — not just a coloured label:
    grounded ("you said this"), inferred ("you never answered this — reasoning
    from your profile"), refused ("I don't know"). **Why?** expands the evidence.
    **That's not me** → corrected answer → `give_feedback()`.
14. **Speak** — cloned voice, English or Hindi. Text never waits for audio.
15. **Video** — off by default. Submits a background job and returns at once;
    a small indicator shows progress and a time estimate taken from the measured
    benchmark (§8.4). The user keeps asking. Cached clips play instantly.
16. **Tell it about you** — free text, one fact per line, `add_note()`, with the
    list of everything added.
17. **Profile** — `Profile.as_markdown()`, rendered properly.
18. **Results** — Phase 1 and Phase 2 numbers and figures.

### 9.6 Part 5: every later launch

19. Splash while MLX loads (target: under 20 seconds), then straight to the
    main window. No setup, no interview, no network. Corrections and notes are
    replayed as today (`TwinSession`).

### 9.7 Part 6: settings and data

20. **Prepare demo** — choose questions, render their avatar clips in advance
    into `cache/clips/`. This is the safety net for the live demo.
21. Defaults: speak on/off, video on/off, language, sharpening (off by default).
22. **Export my data** — one archive of answers, voice, photo, corrections,
    notes.
23. **Redo interview** — choose whether to keep corrections and notes.
24. **Delete my twin** — type the person's name to confirm; erase everything in
    `twin/` and `cache/`. Models stay. This is what gives the consent step
    meaning; it must actually delete, and the app must return to consent.
25. **Remove everything** — deletes the whole Application Support folder, then
    tells the user to drag the app to the Bin. Trashing the app alone leaves
    roughly 10 GB behind; say so in the README.

### 9.8 Build order

1. Benchmark (§8.4) — the numbers decide §9.5 defaults.
2. Backend API over the existing `src/` — no UI yet. Test with a stub `llm_fn`.
3. Bootstrapper + first-run setup, tested on a **clean macOS user account** —
   the only honest test of "sets itself up".
4. Onboarding screens.
5. Main window.
6. Packaging (`.app`, `.dmg`, README), last.

---

## 10. Where to start

For the desktop app, follow the build order in §9.8.

1. Read `QUICKSTART.md`, then `build_twin.py`, `deferral.py`, `persona.py`.
2. Run the existing pipeline once with a stand-in LLM to see the data flow.
3. **Benchmark the Mac (§8.4) and commit `BENCHMARK.md`.** Everything about the
   avatar design depends on those numbers.
4. Swap the model backend to MLX behind `llm_fn` (§8.1), and confirm the text
   twin answers end to end with no CUDA anywhere.
5. Build the consent and interview screens, writing exactly the JSON in §5.3.
6. Wire that output straight into `build_twin.build()` — it already does the rest.
7. Add voice, then the avatar as a background job with pre-rendering.
8. Replace the UI last, once the flow works end to end.

Ask before changing anything in §2.
