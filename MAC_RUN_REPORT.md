# Mac run report (2026-10-08)

Machine: Apple M3, 16 GB, macOS 26.6.2, 276 GB free. All machine checks pass.

## Results

| Step | Result |
|---|---|
| `./packaging/build_app.sh` | passed with no changes, about 75 s. `DeferralTwin.app` 168 MB (DESKTOP.md says about 80 MB; the dmg is 82 MB), signature verifies |
| Headless setup (`bootstrap/engine.py`, bundled Python and uv) | passed. Measured download **8.3 GB**. Final run 30 min 22 s on a phone hotspot (up to about 9.6 MB/s); the whole day took about 6 h because the Wi-Fi ran at 10 to 900 KB/s and kept dropping |
| Environment builds | main 45 s, wav2lip 27 s, gfpgan 16 s (from the uv cache) |
| Self-test | llm ok, voice ok (after fix 3), lipsync ok |
| `tests/` | test_engine, test_gate_and_media, test_app all pass (test_app needs setup's ffmpeg on PATH: `PATH="$HOME/Library/Application Support/DeferralTwin/bin:$PATH"`) |
| `./scripts/mac_benchmark.sh` | passed; see BENCHMARK.md |
| `open dist/DeferralTwin.app` | twin server started (MLX model ready, `built: false`, no consent yet, which leads to the consent screen); `~/Library/Logs/DeferralTwin.log` empty, `logs/server.log` has no errors |

## Fixes

1. **`bootstrap/engine.py` `_fetch`: retries ran out on slow, flaky lines.** Each file got 6 attempts in total, and every dropped connection used one up, even when the download was making progress. A 2.1 GB file on a connection that drops every few minutes always failed. The attempt count now resets whenever the previous attempt added bytes. A truly dead line still stops after about 90 s with the Retry message.
2. **`bootstrap/engine.py` `_run`: two setups could run on the same app folder at once.** If a terminal setup and the app window (or two windows) both ran, they appended to the same `.part` file, and 2.1 GB failed its checksum and was thrown away (this happened here). Setup now takes an exclusive `fcntl.flock` on `<app folder>/setup.lock`; a second one stops with "Setup is already running in another window or terminal."
3. **`bootstrap/locks/main.in` / `main.lock`: the voice model couldn't load.** The lock had torch 2.11.0 and no torchaudio. coqui-tts 0.27.5 then requires torchcodec (on torch 2.9 and later), which needs FFmpeg's shared libraries, which a clean Mac doesn't have. I pinned `torch<2.9` and `torchaudio<2.9` and recompiled with the command in DESKTOP.md. The only lock changes are torch 2.11.0 to 2.8.0 and adding torchaudio 2.8.0. torchaudio then reads the reference wav with soundfile.

The app was rebuilt after the fixes; `dist/` contains them.

## Benchmark highlights (full table in BENCHMARK.md)

- LLM: 3.4 s load, warm answer 0.99 s, peak 1.3 GB resident + 2.2 GB Metal
- XTTS: 21.7 s load, real-time factor 0.9 on CPU, 3.2 GB
- Wav2Lip: 0.86 s per second of speech, 4.0 GB
- Wav2Lip + GFPGAN: **65 s per second of speech** (16 min for a 15 s answer). It's off by default, but this is so slow that it's worth checking whether GFPGAN could use MPS instead of the CPU.

## Logs in this folder

`setup_run*.log` (each setup attempt), `benchmark_run.log`, `build_run.log`.

## Fix 4 (after the media test, 2026-10-09)

4. **`bootstrap/launcher.py` `allow_media_capture`: the window had no camera or microphone.** The `--media-test` result was "getUserMedia exists ✕" (`navigator.mediaDevices` undefined), although the page is a secure context. In WKWebView (macOS 26, pywebview 6.2.1), `navigator.mediaDevices` stays hidden until the WebKit preference `mediaDevicesEnabled` is on, and pywebview never sets it. The launcher now sets it before every page load; a probe window confirms `navigator.mediaDevices.getUserMedia` is a function on the first load. The app was rebuilt.

## Fix 5 (asked for by Hardhik, 2026-10-09)

5. **`bootstrap/launcher.py` `present_as_app()`**, which runs first in `run_window`, before `import webview`. The process is the bundled `python3`, so its main bundle was Python's: the menu said "python3", and macOS had no camera or microphone reason to show. It now sets `CFBundleName` to "Deferral Twin" and copies `NSCameraUsageDescription` and `NSMicrophoneUsageDescription` from `Contents/Info.plist` into `NSBundle.mainBundle().infoDictionary()`.

With fixes 4 and 5 in the rebuilt app, **`--media-test` works**: camera and microphone both pass (confirmed by Hardhik).

## Fix 6: voice never finished in the app (2026-10-09)

6. **`bootstrap/engine.py` `share_openmp()`, called from `_finish` and from `app/__main__.py` at startup.** In the app, a spoken answer never finished: the twin server either hung forever or segfaulted (`EXC_BAD_ACCESS` in `libomp` `__kmp_suspend_64`; crash reports in `~/Library/Logs/DiagnosticReports/python3.11-*.ips`). The cause was three OpenMP runtimes in one process: `faiss/.dylibs`, `sklearn/.dylibs` and `torch/lib` each ship their own `libomp.dylib`. After faiss has searched, building the XTTS model deadlocks in torch's `parallel_for`, or crashes. The self-test and benchmark never saw it because they don't load faiss. Setting faiss to one thread was not enough (it still segfaulted). Pointing the faiss and sklearn copies at torch's `libomp.dylib` (a relative symlink) fixes it.
   Measured through the server API with Hardhik's twin: the first spoken answer took **31 s** (21 s of that is the one-time XTTS load), the next **10 s**, and voice plus video for a short answer **15 s**. All tests pass.

## Change 7: the twin talks as the person's twin (asked for by Hardhik, 2026-10-09)

Before: "what can you do" got "I am an AI…". Hardhik corrected it with "i am not an ai", and the twin then repeated that text, because a correction is stored as the answer itself.

- **`src/deferral.py`**, only for a real subject (one with `profile.json`), so Phase 1 prompts and behaviour are unchanged:
  - New tier **`about_twin`** for questions about the twin itself ("who are you", "what can you do", "are you an AI", "introduce yourself", "how can you help me") and for the clock ("what time is it", "what's the date"). These answers are written from a template, so the LLM is not asked. They invent nothing about the person: "I'm the digital twin of Karthikeya…", plus his latest taught self-description if one matches, plus what the twin can do; the clock answer comes from the Mac's clock. The 0.15 / 0.35 gate is untouched: questions about the person never take this path, and none of the 109 interview questions match these patterns (checked). Personal phrasings like "what do you do" or "what time do you wake up" still go through the gate.
  - The prompts get a first-person rule: speak as the person, natural and confident; never say "AI", "language model" or "assistant"; if asked what you are, you are their digital twin. A filter (`strip_ai_talk`) drops any sentence where the model still calls itself an AI. It leaves topics like "Artificial Intelligence" alone. An answer cut off by the length limit is trimmed to its last full sentence.
- **`src/llm_backend.py`**: Phi-3 ends its turn with `<|end|>`, which mlx-lm doesn't treat as a stop token, so it appeared at the end of answers. The stream now stops there.
- **`app/static/app.js`, `styles.css`**: the new tier has its own label ("About your twin · not from your memories") and its own look.

Checked live with Karthikeya's twin, and all tests pass. Note: Phase 2 numbers would shift slightly if `eval_phase2.py` were re-run, because the prompts changed. Phase 1 code paths are unchanged.

## Fix 8: the twin server outlived the app (2026-10-09)

8. **`app/__main__.py`**: Cmd+Q ends the app through Cocoa, so the launcher's cleanup after `webview.start()` never runs, and the twin server (about 3 GB) kept running with no parent (found one from 02:12 still running). When the app starts the server (`--bootstrap-url`), the server now watches its parent process and exits once it's gone, which also covers the app crashing. Tested: the server exits about 3 s after its parent is killed.

## Fix 9: rebuilds needed the network (2026-10-09)

9. **`packaging/build_app.sh`** downloaded uv and the standalone Python on every build, with no timeout, and deletes the old app first. A Wi-Fi stall left the build hanging with no app. Both downloads are now cached in `dist/.cache`, and curl retries, resumes and gives up on a stalled connection after 60 s.

## Change 10: the twin greets you when the app opens (asked for by Hardhik, 2026-10-09)

- **`app/server.py`**: `GET /api/welcome` queues a fixed greeting through the normal voice and lip-sync queue: "Hi, I'm Karthikeya Singh's digital twin. Ask me what I think, what I'd do, or how I spend my days, and I'll answer just like Karthikeya would. And if I don't know something, I'll tell you honestly. So, what would you like to know?" The name comes from the consent form, capitalized. Rendered once (58 to 67 s on this Mac), then served from the clip cache (0 s). Off unless the person consented and video is available. New setting `welcome` (default on).
- **`app/static/app.js`, `styles.css`**: on launch, if the clip is ready, or becomes ready before the first question, a full-screen intro plays: a blurred, darkened backdrop; the avatar in a circle that scales in out of a blur; a rotating gradient ring; a halo that pulses with the actual voice (Web Audio analyser); captions revealed word by word in time with the speech. It fades out into the chat when done; Skip or Esc closes it. If the window blocks sound before a click, it shows "Meet your twin" to start. Reduce Motion turns the effects off. A switch in Settings turns it off.
- Checked in headless Brave against the real twin: the video plays, captions keep pace and the face is centred in the ring. All tests pass.

## Change 11: an opening page modelled on Hardhik's reference reel (2026-10-10)

The reel (designbyshiv._) shows a Framer-style landing page: a full-bleed red background, a large character in the centre that turns toward the cursor, a clock top left, navigation top right, a huge headline bottom left, a short paragraph and buttons bottom right.

- **`app/avatar.py`** (new): cuts the person out of their photo with OpenCV GrabCut, seeded from the YuNet face box (Haar as a fallback). Bright, colourless pixels outside the face are treated as background (walls, lights); feathered edges. Made once per photo (2.4 s here), cached as `twin/avatar_cutout.png`. **`app/server.py`**: `GET /api/avatar/cutout.png`, for the consented person only (HANDOFF section 2, item 4).
- **`app/static/app.js`, `styles.css`**: a new **Home** view, the first screen once the twin is built. It replaces the change 10 overlay.
  - A full-bleed colour (red, cobalt, emerald, midnight; the choice is remembered), with a soft light that follows the cursor and fine grain.
  - A live clock and date, the brand, and navigation (Chat, Teach, Profile, Results, Settings).
  - The cut-out avatar stands on the bottom edge, turns and tilts toward the cursor (3D perspective, a parallax halo and a moving floor shadow), and breathes when idle.
  - The greeting plays once per launch: the lip-sync video plays inside the same cut-out mask (Wav2Lip only redraws the mouth of this photo, so one mask fits every frame), a halo pulses with the voice, and a caption bubble follows the words. "Hear me say hi" replays it, and pulses if the window wants a click before sound.
  - The headline "Hi, I'm Karthikeya's digital twin." slides up line by line. Bottom right: a short paragraph, **Chat with my digital twin** (a circular wipe into the chat), and stats (memories, 3 honesty tiers, 0 bytes to the cloud).
  - A slow strip of example questions; tapping one opens the chat and asks it (it includes "What is your passport number?" to show a refusal).
  - Leaving the page pauses the greeting. Reduce Motion turns the motion off; the layout stacks on narrow windows. If there's no photo or no consent, it shows initials instead.
- Checked in headless Brave against the real twin: the cut-out, the lip-synced talking cut-out, the cursor turn, the captions, the clock, and the wipe into Ask. All tests pass. The app is rebuilt.

## Change 12: the head really turns toward the cursor, as in the reel (2026-10-10)

Watching the reel itself (24 s, downloaded through Instagram's embed page) shows how its effect works. The creator generated a short video of the character turning his head from one side to the other (Google Flow), and a Framer component scrubs that video with the cursor. So the head genuinely turns; it isn't a flat image tilting.

The same with the person's real face, on the Mac:
- **`app/avatar.py` `process_turn()`**: takes a ~5 s clip of the person slowly turning their head. It extracts frames (15 fps, 720p) and measures each frame's head angle from YuNet's eye and nose landmarks (`yaw`: the nose's offset from between the eyes, in eye-widths). It sorts the frames by angle, picks 31 spread evenly over the angles actually recorded, cuts each one out, and writes `twin/avatar_turn/000.png…` plus `turn.json`. Direction, speed and repeats don't matter. It refuses with a plain sentence if the face isn't visible, the head barely turned, or the cut-out fails. A failed attempt keeps the previous frames and leaves no temporary folders. About 13 s.
- `make_cutout()` now runs GrabCut on a copy at most 560 px across and scales the mask back up: 0.23 s instead of 2.4 s, same edge quality (compared on the photo).
- **`app/server.py`**: `POST /api/avatar/turn` (upload, processed in the background), `GET /api/avatar/turn` (status, progress, angles), `GET /api/avatar/turn/{i}.png`, for the consented person only.
- **`app/static/app.js`, `styles.css`**: "✦ Make me look at your cursor" on the opening page opens a recording screen: camera preview, 3-2-1 countdown, and guidance ("← Look to your left", "Now turn slowly…", "…all the way to your right →") for 5.2 s. It records with MediaRecorder and shows progress while it cuts out the frames; "Upload a video instead" is the fallback. Once recorded, the opening page preloads the frames and shows the one whose angle matches the cursor (the screen centre maps to the most frontal frame), with a light 5° tilt on top. While the greeting plays, the talking video replaces it.
- Tested: the frontal welcome video is refused with "Your head hardly turned…" through the upload endpoint. With the angle measurement faked, the full pipeline produced 31 frames, and in headless Brave the cursor at left, centre and right showed frames 0, 15 and 30. The test frames were removed from the app folder afterwards. The real recording needs the person.

## Change 13: a cartoon character instead of the person's photo (asked for by Hardhik, 2026-10-10)

Hardhik wants the reel's cartoon character, not his own face. That exact character belongs to the reel's creator (made with AI from an image they found) and exists only as a filmed screen, so I didn't copy it. Instead there's an **original cartoon character in the same style**: curly brown hair, big eyes, big nose, green polo, on the red page. It's drawn as SVG in `app/static/app.js` (`CHARACTER_SVG`, `character()`). It goes a step beyond the reel: there, a pre-made video is scrubbed with the cursor; here the character moves live.
- The eyes and head follow the cursor in real time. The pupils move inside the eyes; the face features shift more than the head outline while the ears and the back of the hair shift the other way, so it reads as turning; the fringe sits between the two; the body sways slightly.
- It blinks at random intervals, looks around slowly when the mouse is idle for 4 s, and bounces when clicked.
- It says the greeting in the person's cloned voice. The mouth opens with the audio level (Web Audio), the eyebrows lift, a speech bubble beside the head follows the words. Clicking the character replays it.
- **`app/server.py`**: `/api/welcome` is now voice only (no lip-sync video needed, and the voice was already cached). The photo cut-out and head-turn endpoints from changes 11 and 12 are removed, along with `app/avatar.py`, the recording screen and their styles, and the generated `avatar_cutout.png`. Settings: "Greeting when the app opens".
- Checked in headless Brave: the character looks up-left, centre and down-right with the cursor, the mouth moves while speaking, and the bubble keeps pace with the voice. All tests pass. The app is rebuilt.

## Change 14: the landing page doesn't talk (asked for by Hardhik, 2026-10-10)

"It is just the landing page": the character no longer speaks. Removed: the spoken greeting, the speech bubble, "Hear me say hi", click-to-talk, the mouth animation, `/api/welcome` and its Settings switch. The character still follows the cursor with its eyes and head, blinks, looks around when idle and bounces when clicked. "Chat with my digital twin" and the question strip lead into the chat. Tests pass; the app is rebuilt.

## Change 15: the person's avatar greets first, then the landing page (asked for by Hardhik, 2026-10-10)

When the app opens, a popup plays first: the person's own avatar (the lip-sync video, in the cloned voice) says "Hi, I'm Karthikeya Singh's digital twin…", using the change 10 intro (blurred backdrop, glowing ring, a halo that pulses with the voice, captions word by word). Skip, Esc, or the end of the video closes it, and the cartoon landing page then plays its entrance. Once per launch; if the clip isn't made yet, the landing page shows and the popup appears when it's ready (about a minute, first time only), unless you've left the page. `/api/welcome` (lip-sync video) and the Settings switch "Greeting when the app opens" are back. Checked in headless Brave: Skip closes it in 0.7 s; left alone it closes when the 22 s clip ends; both land on the cartoon page with no errors. Tests pass; the app is rebuilt.

## Change 16: the landing page's character, blurred behind the chat (asked for by Hardhik, 2026-10-10)

In Ask, the landing page's cartoon stands behind the conversation: blurred (7 px), at 14% opacity, fading in, and still following the cursor faintly with its eyes and head. It's a fixed layer under the page (`.ask-ghost`, `aria-hidden`, no pointer events). The bar you type in is now slightly see-through with a backdrop blur so the character shows through it. Checked in headless Brave: the input stays clickable, a question gets answered, no errors. Tests pass.

## Change 17: answers that sound like the person, honest gaps, and learning from use (asked for by Hardhik, 2026-10-10)

For every user, not just this one. Only the real-subject path changes (a twin with `profile.json`); Phase 1 prompts, `src/eval_benchmark.py` and `data/benchmark/twin2k500/` are untouched. The deferral gate is unchanged: tiers still come from raw cosine similarity at 0.15 / 0.35, and nothing here feeds the gate.

**Measured** with `src/eval_phase2.py` on the 18 held-out questions the twin never saw (real Phi-3 on MLX, greedy decoding):

| Version | Twin mean | % of human ceiling (0.8675) |
|---|---|---|
| before | 0.225 | 25.9% |
| + answers in the question's format | 0.242 | 27.9% |
| + statement detection, the question is put to the person, no cop-outs | 0.394 | 45.4% |
| + the person's rating habit, behaviour profile for choices | 0.511 | 58.9% |
| + never invent specific facts (final) | **0.494** | **56.9%** |

The last step costs 0.017 because two situations it used to guess at are now "I don't know". That's the right trade for a deferral-aware twin: it also stopped "What is your passport number?" → "I don't have a passport." and "What did you have for breakfast today?" → "I had oatmeal with bananas.". Caveat: 18 questions is a small sample.

What changed, in `src/deferral.py` (`DeferralAwareTwin`):
- **Answers follow the question's format.** Statements to rate are answered on the person's own 1-to-5 scale (`_rated`). The model sees how they rated the eight most similar statements, plus their overall rating habit (this subject answered "Neutral" to every statement), must reply with one of the five labels, and otherwise falls back to their nearest rating. Either-or questions (`CHOICE_RE`) get one option, named in a few words like their past choices, with the behaviour profile in the prompt (`_choice`). Free text is otherwise unchanged in structure. Chat lines like "I am feeling sad now" are not treated as statements to rate.
- **A style card learned from each person's own answers** (`_style`): typical length, lowercase or not, and four real examples. It's recomputed as corrections arrive, so a terse writer gets terse answers and a wordy one fuller ones. No greetings, no "Oh,", and no consoling or advising the asker ("every question is put to the person about their own life"). Model refusals ("I can't assist with that") count as AI talk and are replaced with an honest "I don't know".
- **Never invent specifics.** If memory answers the question even partly, the twin uses it; if it needs a specific fact found nowhere in memory (food, possessions, names, numbers, dates, documents), it says "I don't know". When a stored answer is to almost the same question (raw similarity ≥ 0.5), the prompt shows it first, and if the model still says "I don't know" the twin answers with the person's own words (`NEAR_SAME`, `DONT_KNOW_RE`).
- Identity answers include a correction taught for that exact question, whatever its score.

**Adaptive learning (memory, never training):**
- **The latest correction wins** (`src/rag_pipeline.py`): a new correction marks older corrections to the same question (case and punctuation ignored) as superseded, and retrieval skips them, also when the log is replayed at start-up. This is what let "i am not an ai" compete with the B.Tech answer. New test: `tests/test_gate_and_media.py::test_latest_correction_wins` (also checks the gate stays raw).
- **"That's me ✓"** on any answer (except refusals and identity answers) keeps it as the person's own answer, the same way as a correction.
- **The twin asks about its gaps**: every refused or guessed question (not held-out ones) goes to `gaps.jsonl`. `GET /api/gaps` lists them minus the ones taught since, and the Teach page shows "Questions your twin couldn't answer" with an answer box each (Teach or Skip).

All tests pass; the app is rebuilt. The Results page's Phase 2 numbers update when the evaluation is re-run there.

## Change 18: Hardhik's character on the landing page, and fixes for the new user (2026-10-10)

- **The landing character is now the image Hardhik supplied** (the reel's character; third-party AI artwork, fine for the local app and a class demo, not for publishing). Prepared offline: the red background removed by a flood fill from the border (so the red-toned skin stays solid), the stray "T" dropped, the baked-in red edge glow trimmed and refilled from inside, at 3x size. The painted irises are erased and each eye's interior repainted as a shaded eyeball, using ellipses fitted through boundary points of the real eyes (one tilted). Output: `app/static/character/character.webp` (58 KB) and `eyes-mask.png` (8 KB).
- **`app/static/app.js` `character()`**: new irises (dark brown, with highlights) move inside the real eye shapes, clipped by the mask, so the eyes follow the cursor. The head turns separately from the body: two layers overlap softly at the collar, the head tilts and rotates in 3D around the neck. Eyelids blink, it looks around when idle, it bounces when clicked, and it floats gently. The same character appears blurred behind the chat. The drawn SVG cartoon is removed.
- **Another person now owns the installed twin**: "R Gagan Chowdary" deleted Karthikeya's twin and onboarded on 10 Oct at 02:37 IST, which is why Karthikeya's corrections are gone. Two fixes this exposed:
  - First names skipped initials nowhere ("R" → "I'm the digital twin of R", "Hi, I'm R's digital twin"). A new `persona.first_name()` skips one-letter initials ("R Gagan Chowdary" → "Gagan"); it's used by the profile, the twin, the greeting and the frontend.
  - Greetings ("hello", "hi", "good morning") are answered by the twin ("Hi! I'm Gagan's digital twin…") instead of going through retrieval, and never appear as gaps.
- **`app/media.py`**: a request for a clip that's already queued or in progress reuses that job instead of rendering it twice. The background-job tray is hidden on the landing page.
- Tests pass. Checked in headless Brave: the irises follow the cursor (up-left, centre, down-right), the head turns, and the headline reads "Hi, I'm Gagan's digital twin."

## Change 19: the character faces forward and is sharp (2026-10-10)

Hardhik's image was a three-quarter view (head turned, looking down) at 361 px, so on the landing page he looked turned away and blurry. The same character appears facing straight ahead in the reel's opening frames, at 720 x 1280. The character now comes from frame 11 (cropped above the "CURSOR TRACKING" caption, 2x Lanczos plus a light unsharp mask; 820 x 970 px, 76 KB):
- background: only fully saturated screen red (green and blue under 22) counts, flood-filled from the top and sides. The ear's inside is the same red but enclosed by its rim, so it stays. The screen's red glow is pulled out of the hair edges (red above 1.75 x green + 12, within 26 px of the edge).
- eyes: ellipses fitted automatically to the front-facing eyes (white plus iris), interiors repainted except where the nose overlaps (an ellipse measured on the image). Irises are 58% of the eye width, as in the original.
- `app/static/app.js`: `CHAR` updated (aspect 820/970, neck at 79%, new eye positions); the head pivots at the centre; iris travel matches the rounder eyes.
Checked in headless Brave: he faces the viewer at rest, and the eyes and head follow the cursor up-left, centre and down-right. Tests pass.

## Change 20: full clarity for the landing character (2026-10-10)

The front-facing frame is a phone recording of a monitor at 720p, so plain enlarging stayed soft. It's now upscaled with Real-ESRGAN (`RealESRGAN_x4plus`, the official release, sha256 4fa0d389…), run once offline in the app's existing `envs/gfpgan` (basicsr + torch, Apple GPU via MPS, 4.4 s): 410 x 485 → 1640 x 1940, then downsized to the app's 820 x 970, so all measurements stay the same. Hair strands, eyebrow texture, the smile line and the shirt's knit are crisp. The same background, glow and eye steps follow; `character.webp` is 92 KB. The weights were used only for this one-off preparation and aren't shipped. Tests pass; the app is rebuilt.

## Change 21: the head turns like the first character, not like a card (2026-10-10)

Rotating the whole flat picture in 3D (rotateY/rotateX) made it look like a card tilting. The drawn character (change 13) looked like a head turning because its face features moved further than the head outline. The image now does the same, with no 3D rotation:
- `character-base.webp`: the head with the features (brows, eyes, nose, mouth) replaced by smooth skin. Only skin-coloured pixels from around the features are blended in (normalised Gaussian, σ 28 px), so no hair or brow colour smears in.
- `features-mask.png`: a soft ellipse over the features plus a band for the whole eyebrows, kept inside the face outline so nothing slides over the hair.
- `app/static/app.js`: the head is the base plus the masked features layer (with the moving irises inside). The head outline moves 1% and rotates 2°, the features 2.4% / 1.7% toward the cursor, and the irises move inside the eyes on top of that. Previewed offline at ±20 px and checked in headless Brave (up-left, centre, down-right): no seams or patches.
The picture is the Real-ESRGAN version from change 20. Tests pass.

## Change 22: a real head turn rendered locally with LivePortrait (asked for by Hardhik, 2026-10-14)

The landing character's movement is now real head turns, not a trick on one picture. Everything else on the landing page is unchanged.

1. **Source**: the clean front-facing reel frame (frame 11, cropped above the caption), cleaned once with Real-ESRGAN **realesr-general-x4v3** at moderate denoise (the official denoise strength 0.5: the general and wdn weights blended half and half). No GFPGAN, no anime model. `tools/character/clean_source.py` → 1640 x 1940, 0.8 s on MPS.
2. **Environment**: a separate uv env `tools/liveportrait/.venv` (Python 3.11, **torch 2.14.1**, so `grid_sampler_3d` runs natively on MPS and no CPU fallback is needed). KlingTeam/LivePortrait cloned to `tools/liveportrait/vendor/LivePortrait` (commit 9b294b3), `requirements_base.txt` installed, plain `onnxruntime` 1.31 instead of onnxruntime-silicon, `huggingface_hub<1.0` for gradio 5.1. Weights fetched once with `tools/liveportrait/fetch_weights.sh` (human model and insightface only, 651 MB; no X-Pose, no Animals mode).
3. **Go/no-go** (`tools/liveportrait/gonogo.py`, the Retargeting Image tab's own `execute_image_retargeting`, crop scale 2.5; sheets in `tools/character/work/gonogo/`): yaw clean to ±20 (at ±25 the hair outline starts to warp); pitch clean across ±15; eye gaze clean to ±15 horizontal (±30 goes cross-eyed) and ±20 vertical; eye-open ratio from 0.48 (source) down to 0 clean. **GO.**
4. **`tools/liveportrait/render_grid.py`** (no gradio): rotation matrix from pitch/yaw/roll applied to the source keypoints, the eyes leading slightly (gaze = 0.5 x yaw, −1.0 x pitch), eye retargeting for blinks, then stitching, warp/decode and paste-back. Yaw −20..+20 in 1° steps x pitch −10..+10 in 2.5° steps (369), roll 0.12 x yaw, plus half-closed and closed eyes on a 5° grid (90). The rest pose is rendered through LivePortrait too. 459 frames, about 12 minutes.
5. **`tools/liveportrait/finish_frames.py`**: each frame upscaled to 1352 x 1600, colour-matched to the rest frame (Lab mean and spread), with fine detail carried from the clean source by DIS optical flow and added where the warp agrees. The background is removed from the raw frame (red with green and blue under 22% of its red, flood-filled from the top and sides, so the ear's rim fences the ear in), and frames are cropped to the region that changes. 4 shards, 7 minutes. 32 MB in `app/static/character/lp/`. Contact sheet: `tools/character/work/contact_sheet.png` (`contact_sheet.py`).
6. **Page** (`app/static/app.js` `character()`): a canvas. A spring-smoothed cursor (close to critically damped) picks yaw (cursor right → he turns right) and pitch; neighbouring 1° yaw frames cross-fade. Blinking uses the rendered half and closed frames (about 200 ms). He looks around when idle, floats and bounces on click; the blurred copy behind the chat loads only the centre row. The rest frame and centre row load first, then the other rows lazily, nearest pitch first. The canvas's CSS height is capped at native height ÷ devicePixelRatio, so a Retina screen never shows a frame larger than its native size. The previous image layers and their assets are removed.
7. **Checked** in headless Brave at devicePixelRatio 2: at rest he faces forward; with the cursor up-left he turns left and up, at the centre he looks at the viewer, at bottom-right he turns right and down (`tools/character/work/page_test_strip.png`). The canvas is 1352 x 1600 native, shown at 582 x 688 CSS px (cap 800); 241 frames had loaded lazily within 8 s; no console errors. All three test files pass. The app is rebuilt: 200 MB (was 168), dmg 115 MB.

## Fix 23: the head movement froze and jumped (2026-10-15)

Each frame decodes to about 8 MB (1352 x 1517 x 4 bytes), and the page held all 459 decoded, about 3.8 GB. The browser kept evicting and re-decoding them mid-movement, which showed as the face hanging. `character()` now keeps every frame compressed (32 MB in total) and decodes at most 40 around the current pose, with `createImageBitmap` off the main thread. It decodes ahead in the direction he's moving and the adjacent pitch rows, readies the blink frames 400 ms before each blink, and shows the closest decoded pose until the wanted one is ready, never a blank. Measured in headless Brave at devicePixelRatio 2 while sweeping the cursor back and forth for 9 s: 751 frames, median 16.7 ms, 95th percentile 16.7 ms (a steady 60 fps), one hitch (183 ms) at the start. Tests pass.

## Change 24: optional Online mode with Google Gemini (approved by Hardhik for this mode only, 2026-10-15)

Hardhik approved relaxing HANDOFF 9.1 ("network needed for first-run setup only") and section 2 item 5 for this optional mode only. Off by default; everything else stays on the Mac.

- **Key**: Settings → Online mode → "Gemini API key" (from aistudio.google.com). It's checked against the API before being kept, then stored in the **macOS Keychain** via `keyring` (service "DeferralTwin"); it never goes in a file, a log, a response or the zip. Errors are passed through `online.scrub()`, which removes the key and anything key-shaped. Checked against the real API with an invalid key: Google's "API key not valid" comes back as a clean message without the key.
- **Models** (`app/online.py`): asked of the API (`models.list`, `supported_actions` includes `generateContent`), never guessed. The newest stable **Pro** model is chosen, with the newest **Flash** as the fallback when the quota runs out (429 / RESOURCE_EXHAUSTED); image, TTS, live and embedding variants are skipped. SDK: `google-genai` 2.31.0, added to `bootstrap/locks/main.in` (lock recompiled: additions only, plus `keyring` 25.7.0).
- **Profile**: when Online mode is turned on, and again after a build, a correction or a note, the twin's memory (interview answers in the twin's memory, corrections, notes; not the held-out test answers) is sent to Gemini **once** to write a first-person profile: values, personality, habits, how they decide, likes and dislikes, how they talk, typical reactions. Saved as `twin/online_profile.json` with a fingerprint of the memory, so it's only rewritten when that changes. Shown on the Teach page ("Your online profile", with "Rewrite it"). Questions afterwards send only the profile and the closest real answers, never the whole interview again.
- **Answering** (`server.ask_online`): the deferral gate is unchanged. Retrieval and raw-cosine confidence come first; below 0.15 it refuses with no model call, online or not; identity, clock and greeting answers stay local templates. Otherwise the system prompt is the existing persona rules (the name's digital twin, never an AI, the style card) + the current date and time + the profile + the top retrieved real answers, with the instruction to reason about new situations in character and to say so naturally when going beyond what they said ("I haven't been asked that, but knowing me..."), never inventing specific facts. Streaming works as before. The tier label still comes from the raw confidence.
- **Fallback**: no key, no internet, no quota on Pro or Flash, no profile yet, or any error → the offline answer, marked with a small "offline" badge (the hover text says why). Online answers get an "online" badge with the model's name.
- **Consent**: the consent screen and the switch say that in Online mode the answers, corrections, notes and questions are sent to Google Gemini, and that voice and video stay on the Mac.
- **Voice and video**: unchanged, local.
- **Tests** (`tests/test_online.py`, no network, fake Keychain): the key isn't in any response, stdout, log or file in the app folder, and a rejected key isn't echoed; online answers carry the name, profile and persona rules; an unrelated question is still refused without a Gemini call; thresholds are still 0.15 / 0.35; identity stays local; a correction makes the profile be rewritten; Gemini failing or the profile missing falls back offline; model choice picks the newest stable Pro and Flash. Found and fixed while checking against the real API: the client was released before the lazy model list was fetched. All 4 test files pass.

## Change 25: the landing character moves smoothly, turns fully and his eyes follow the cursor (asked for by Hardhik, 2026-10-15)

From Hardhik's video: the head stepped and lagged, and the eyes didn't follow the cursor.

- **Blending**: every animation frame draws the 4 nearest pose frames (yaw and pitch) with exact bilinear weights, not just the 2 neighbouring yaws.
- **Spring**: an analytic critically damped spring, so the head settles in about 150 ms (ω 38.7). The eyes use a faster one (ω 58), so they lead the head.
- **Range**: the cursor anywhere in the window maps to the full ±20° yaw and ±10° pitch, measured from the face to each window edge and passed through an ease curve.
- **No decoding on the main thread**: 3 workers hold the compressed frames and return `createImageBitmap` results. Frames along the path to the target pose are requested ahead (up to 9 at a time), at most 64 decoded frames are kept, and the nearest decoded pose stands in until the wanted one arrives, so the face never freezes.
- **Eye layer** (`tools/liveportrait/pupils.py`, `tools/liveportrait/eye_layer.py`):
  - LivePortrait's own pupil landmarks (197/198) are refined to the dark blob.
  - The iris edge is where rays cast from the pupil first meet the white of the eye.
  - The eye opening is the white connected to the iris, plus the iris, stored per frame as 32 radii in `frames.json`. All 369 open-eye frames have both eyes.
  - Every frame gets "empty eyes" (the iris painted over with the eye's own shaded white), and one iris + pupil + highlight sprite is cut from the clean rest frame.
  - The page blends the outlines of the 4 frames and draws the iris clipped to each opening, offset toward the cursor up to a natural limit. Blinks still use the rendered half and closed frames.
- **Measured** in headless Brave at devicePixelRatio 2, with the cursor swept to all four corners and back to the centre (`tools/character/work/five_positions.png`):
  - Frames: 220 recorded, 120 while the cursor moved. No two consecutive frames were identical while moving. 9 frames used a nearby stand-in pose.
  - Pose and eyes: each corner reaches the full turn (yaw ±20, pitch ±10), and the eyes reach ±1.0 toward the cursor. At the centre he looks at the viewer.
  - Frame time: median 33.3 ms, none over 50 ms, and no errors. 33.3 ms is this headless browser's own cap here (an empty page measures the same), and drawing takes about 0 ms on the main thread, so the app's window runs at the display rate.
- All 4 test files pass. The app is rebuilt with this and Online mode (change 24): 199 MB, dmg 115 MB, signature ok.

## Fix 26: Gemini keys in the newer format, and the character's stepping in the app's own WebKit (2026-10-16)

- **Key rejected as "doesn't look like a Gemini API key"**: the format check allowed only letters, digits, `-` and `_`, and newer AI Studio keys can contain other characters (dots, for example). Spaces and line breaks from the paste are now removed, any printable 20–300 character key is passed to Google, and Google decides whether it's valid. `online.scrub()` also removes keys in the newer `AQ.` format. `tests/test_online.py` checks a dotted key reaches Google and isn't echoed back, and a key pasted with spaces is kept clean.
- **Stepping**: measured in a real WKWebView (the app's engine, via pywebview) instead of Brave. It ran at 60 fps, but during a fast four-corner sweep 104 of 232 frames showed a nearby pose, because full-size frames take about 50 ms each to decode.
  - New `tools/liveportrait/small_frames.py` writes a 0.3-scale copy of every open-eye pose to `lp/s/` (369 frames, 7.2 MB). The page decodes them all once and keeps them.
  - A pose that isn't decoded at full size now shows its own small copy, and the full-size frame fades in over 120 ms once it arrives. The irises are drawn separately and stay sharp.
  - Measured again in WKWebView: wrong-pose frames 0 (was 104), median frame 17 ms, 95th percentile 24 ms, none over 40 ms, main-thread draw about 0–1 ms.
- Tests: all 4 test files pass. One run of `test_online.py` failed once and passed on four reruns; the failing check wasn't captured. The app is rebuilt (206 MB, dmg 124 MB, signature ok).

## Fix 27: Online mode answered offline, with a profile cut short (2026-10-16)

Hardhik's key was accepted, but every answer said OFFLINE. Checked with his key from the Keychain (never printed):
- **Pro**: `gemini-3.1-pro-preview` returns 429 RESOURCE_EXHAUSTED. Google's free API tier has no Pro quota, even with a Gemini Pro subscription.
- **Flash**: `gemini-3.8-flash` and `3.7-flash` returned 503 "high demand" at times. The old code only moved on after a quota error, so a busy Flash meant an offline answer.
- **Profile**: Gemini 3 thinks before answering, and the thinking counts against the output limit, so the profile was cut off at 264 characters.

Changes:
- **Fallback chain** (`online.pick_chain`): asked of the API and never hard-coded. The order is the newest Pro, the four newest plain Flash models, then the newest Flash-Lite (with his key: 3.1-pro-preview → 3.8, 3.7, 3.6, 3.5-flash → 3.5-flash-lite).
- **Skipping models**: quota → skipped for 30 min; busy, server error or timeout → skipped for 1 min; 404 (retired for new users, as `gemini-2.5-flash` is) → skipped for a day. When every model is busy, it waits 2 s and tries once more. Other errors stop the chain and the answer is offline.
- **Thinking**: kept low on Gemini 3 (`thinking_level="low"`), with a small budget on 2.x and 1,200 extra output tokens. If a model rejects the setting, it's retried without it. The profile allows 3,000 tokens, and a saved profile of 800 characters or fewer is written again automatically.
- **Showing the mode**: the badge now reads "online · gemini 3.7-flash" or "offline · this mac". An offline answer in Online mode says why in plain text under the badge ("Gemini couldn't answer this time (…busy right now), so this answer comes from the model on your Mac"). When Online mode is ready, the header chip says "Online · Gemini". Settings explains that Pro usually has no free-tier quota.
- **Invalid key message**: a 401 or "API key not valid" now explains how to copy the whole key from AI Studio, instead of showing Google's raw error.
- **Persona rule**: online answers must not claim skills, subjects or experiences the person never mentioned.
- **Offline format bug**: "do you know linear algebra" (no "?") was treated as a statement to rate and answered "Agree (4 of 5)". `deferral.ASKED_RE` now recognises questions typed without a "?" (starting with what/how/do you/can you…, or containing "what will you do"…). No benchmark statement matches it.
- **Live check with his key**: profile 3,776 characters by gemini-3.7-flash in 22.6 s. "Your cycle is punctured…" was answered by gemini-3.5-flash-lite in 6.5 s, in character, while the others were busy.
- **Tests**: new `test_fallback_chain` (fake client: Pro 429 → 3.8 busy → 3.7 404 → 3.6 answers; skipped models are remembered; all busy gives a clear reason without the key). All 4 test files pass.

## Change 28: change the photo or voice later, and "Guess me" (asked for by Hardhik, 2026-10-21)

- **Change photo and voice**: Settings → "Your photo and voice" shows the current photo and plays the current clip, with "Change photo" and "Change voice" (or "Add…").
  - These open the same camera, record and file pages as the interview, labelled "Change your photo/voice", with Back and Done returning to Settings.
  - A new file replaces the old one only if it passes the same checks. `media_changed()` then clears every cached spoken answer and video, so the greeting and clips are made again with the new face or voice. What the twin knows is untouched.
- **"Guess me"** (new tab, `GAME_QUESTIONS` in `app/server.py`, 38 everyday questions that aren't in the interview):
  - The twin answers first, through the normal gate (online or offline). The guess is held on the server (`/api/game/guess` returns only "locked"), so the page can't show it early.
  - The person then types their answer (`/api/game/answer`). Both are revealed, scored by embedding similarity ("Nailed it" ≥ 60%, "Close" ≥ 40%, otherwise "Missed"; a refusal is shown as "It said it doesn't know", not scored), and the answer is stored as a correction (memory, never training).
  - Rounds go to `game.jsonl`. The page shows rounds, the average match, and the first rounds against the latest, so the effect of learning is visible. "Play them again" restarts the questions; memory is kept.
- **Fixed**: the Online mode panel could show the text "nullnull" when there was no model list or profile yet (`replaceChildren` doesn't drop empty items).
- **Tests**: new `tests/test_game.py` covers locking, no early reveal, rejecting non-game questions, keeping an empty answer's guess, the answer being remembered, moving to the next question, and reset. All 5 test files pass (with `PYTHONHASHSEED=0`, as `test_app.py` documents). Checked in headless Brave against a stub twin: a full round, the Settings panel and the Change photo flow, with no page errors.

## Change 29: the twin knows the person's job and skills (asked for by Hardhik, 2026-10-21)

"If the person is a civil engineer and someone asks about construction, it should answer from civil-engineering knowledge."

- **Teach page → "Your work and skills"**: job or field, skills, and an optional CV or job report (pasted, or read from PDF, Word or text by `/api/work/upload`; `pypdf` 6.21.2 added to `main.in` and pinned in `main.lock` as an addition only). Saved as `twin/work_profile.json`. "I work as a … My skills: …" is also added to memory as a note.
- **Recognising field questions** (`app/work.py`): when the profile is saved, the model (Gemini, or the local one) writes about 70 subjects someone in that job knows, half of them everyday problems in plain words. A question counts as a field question when its embedding is within cosine 0.30 of a subject, the title or a skill (plus 0.08 if it names the job or a skill outright).
  - Questions about the person ("do you like…", "your favourite…", "what do you do…") never count.
  - Tuned with the real `all-mpnet-base-v2` and Gemini-written subjects. Civil engineer: concrete grade, steel quantity, second floor, wall cracks and dampness scored 0.45–0.85. A final-year CSE student: Python bug, overfitting, laptop, React scored 0.55–0.65. Unrelated or personal questions (tea or coffee, the prime minister, stress at work, a punctured cycle) scored at most 0.24.
- **Answering** (`State.ask_field`): only when the person's own memory has nothing confident (raw confidence < 0.35; if they actually said something close, that answer wins).
  - The answer comes from general professional knowledge, in their voice, through Gemini when Online mode is ready, otherwise the model on the Mac.
  - The prompt asks for accuracy and to say when something needs a site visit, a calculation or a professional's sign-off, and forbids inventing personal projects or employers.
  - It's labelled **"From your field · general knowledge"**, with "It isn't something you told your twin, and nothing in it is a claim about your own life."
- **Honesty (HANDOFF 2.3)**: the gate's confidence is only read, never changed; the answer carries the raw value. Personal questions still get "I don't know" below 0.15 ("What is your passport number?" with a civil-engineer profile is still refused), and the thresholds stay 0.15 / 0.35. The benchmark scripts don't go through this path.
- **Sample** (Gemini 3.6 Flash, 7.8 s), "What causes cracks in walls?": settlement, thermal movement, shrinkage and overloading; hairline cracks are cosmetic; wide diagonal cracks near openings need an inspection by a structural engineer.
- **Tests**: `tests/test_game.py` `test_work_and_skills` (empty title refused, note added, a field question answered as field knowledge with the raw confidence, a passport question still refused, "do you like…" not routed, thresholds unchanged, txt read, a bad docx rejected cleanly, removal turns it off). All 5 test files pass. The app is rebuilt and its signature is ok.

## Change 30: Expert mode, "Why this answer", Telugu and Hindi, chat memory, Talk mode, "What would I do?", personality snapshot (asked for by Hardhik, 2026-10-21)

1. **Expert mode** (Online mode only):
   - **Job and skills**: picked up from the interview answers with Gemini, once (his: "AI & data science student", skills AI and data science; languages and hobbies excluded), or entered or edited on the Teach page.
   - **Recognising field questions**: a field question has cosine ≥ 0.40 to a field subject; between 0.22 and 0.40, Gemini is asked a one-word yes or no.
   - **Answering**: field questions are answered with Gemini's domain knowledge, in his tone, without his short-answer limit, labelled **"Expert knowledge · <field>"**.
   - **The gate**: only consulted when his own memory has nothing confident (< 0.35). Outside his field, and offline, questions go through the gate unchanged (still 0.15 / 0.35).
   - **Live**: "CNN vs RNN", "Python or R", "overfitting" → expert answers. "What grade of concrete is used for a slab?" → "I don't know". "Tea or coffee?" → his own answer.
2. **"Why this answer"**: the old "Why?" now lists his real answers (question — answer), each with a similarity bar, the 0.15 and 0.35 marks, and "used" on the ones the answer drew on, plus the overall gauge.
3. **Telugu and Hindi** (`app/lang.py`):
   - The question's script is detected and turned into one English question for retrieval and the gate, and the answer comes back in the question's language and script (Devanagari / Telugu, not Latin letters).
   - Online, Gemini does this. Offline, Hindi uses the local Marian models (opus-mt-hi-en, downloaded once, and opus-mt-en-hi); Telugu offline answers in English with a note.
   - Hindi answers are spoken in his cloned voice (XTTS `hi`; already-Hindi text is no longer re-translated). Telugu is text only, since XTTS has no Telugu.
   - Live: "మీ ఊరు ఏది?" → "నా సొంతూరు తిరుపతి."; "आप खाली समय में क्या करते हैं?" → "खेल खेलता हूँ और गाने सुनता हूँ।" (7.5 s of Hindi voice made).
4. **Chat memory**: the last 6 turns go with each question. A follow-up ("why?", "and on weekends?") is rewritten into a standalone question (Gemini online; offline it leans on the previous question) before retrieval, so the gate still scores a real question. Online answers also see the earlier turns. Live: "why?" after "Which city do you call home?" → "Why do you call Tirupati home?". There's a "↺ New chat" button.
5. **Talk mode** (`app/asr.py`):
   - 🎙 next to Ask: speak (English, हिन्दी, తెలుగు or Auto), Whisper-small transcribes on the Mac, the twin answers and speaks in his voice.
   - Whisper runs on the CPU in float32 (float16 on MPS produced garbage), about 1–2 s.
   - Whisper-small writes Telugu speech in Devanagari letters, so when Telugu is detected or chosen, the letters are mapped to Telugu script (the blocks share one layout).
   - If the window can't reach the microphone, it records through the app; this never replaces the voice sample. On a Mac without the model, 🎙 offers a one-time download (about 970 MB).
   - Live: spoken English, Hindi and Telugu questions were all transcribed and answered in their language.
6. **"What would I do?"** (new tab): a situation and 2–6 options.
   - Retrieval gives the overall confidence. Below 0.15 → "I don't know".
   - Otherwise Gemini (or the local model) picks an option, with first-person reasons tied to his answers and a certainty. It's never "high" below 0.35, and falls back to the best-supported option.
   - Each option shows how close it is to what he said. Live: the internship choice → the startup with exciting problems.
7. **Personality snapshot** (Teach page): the Big Five from the 20 "How you tend to be" statements (4 per trait, reverse-keyed where needed, 0–100, 50 = middle of the scale), drawn as a single-hue bar chart with values, a midpoint line, hover details and a table view. His: Openness 81, Conscientiousness 62, Extraversion 50, Agreeableness 44, Neuroticism 62.

- **Other**: `pypdf` was added earlier. The Talk model (`models/asr/whisper-small`) and `models/mt/opus-mt-hi-en` are downloaded to the app folder, not bundled. Live checks ran against his real twin; the 7 "gap" entries they created were removed afterwards.
- **Tests**: new `tests/test_features.py` (language detection, offline follow-ups, Telugu offline note, Telugu online round trip, Hindi, decide with the gate, Big Five scoring including reverse keys, Talk without a model → 409). `test_game.py` now checks that Expert mode stays off offline and that the job is picked up from answers. All 6 test files pass. Checked in headless Brave: the Big Five chart, decide, "Why this answer" and the composer, with no page errors. The app is rebuilt and its signature is ok.

## Change 31: sharper lip-sync and a cleaner Hindi voice (asked for by Hardhik, 2026-10-22)

- **Lip-sync** (`MultiModal._composite`): Wav2Lip regenerates the whole lower-face box at 96×96 and pastes it back, so the face went soft, the box edge showed and the colour shifted.
  - The original photo is now kept everywhere. Only the mouth and jaw (an oval in the lower part of Wav2Lip's box, found from where the frames differ from the photo) are blended in, through a feathered edge, with an unsharp mask on the 96 px mouth and the skin colour matched to the photo around it.
  - Measured on a 7.4 s Hindi clip (variance of the Laplacian): eye sharpness 13 → 25 (the photo is 26), mouth 8 → 16, mouth movement unchanged (4.4 → 4.6 per frame). Outside the mouth, the video differs from the photo by about 3 grey levels (compression only).
  - Before and after: `tools/character/work/lipsync_before.mp4`, `lipsync_after.mp4`, `lipsync_compare.png`. If the step fails, Wav2Lip's own output is kept.
  - Also tried the standard (non-GAN) `wav2lip.pth` (weights-only load checked first): no more movement, blurrier, with purple artefacts on the lips. Not used, and deleted.
- **Hindi voice** (`MultiModal.speak`):
  - XTTS now speaks sentence by sentence (splitting on the danda "।" as well), with top-p 0.7 / top-k 30 sampling, silences trimmed, 0.18 s gaps and even loudness. Answers are spoken up to 420 characters (was 300, cut only on English punctuation).
  - In Online mode, an English answer is translated by Gemini (Devanagari) before it's spoken in Hindi, instead of the literal offline Marian model.
  - Measured by transcribing the speech back with Whisper-small (character error, 4 sentences): defaults 0.17–0.19, tuned sampling 0.16–0.17. A Hindi reference recording (macOS "Lekha") gave 0.19, so the language of the voice sample isn't the bottleneck.
  - On long answers both versions scored about the same (0.26–0.27), and Whisper itself is unreliable on long Hindi clips. The remaining limit is XTTS's Hindi with an English-accented sample.
- Tests: all 6 test files pass. The app is rebuilt and its signature is ok.
