# Benchmark — this Mac

Measured 2026-10-08T23:26:34 on Apple M3, 16.00 GB memory, macOS 26.6.2, by `python -m app.benchmark --full`.
Each item ran in its own process; peak memory is the whole process tree, sampled every 0.25 s.

| Item | Time | Peak memory |
|---|---|---|
| LLM (Phi-3-mini 4-bit · MLX) — load | 3.38 s | 1.32 GB resident, 2.23 GB Metal |
| LLM — first answer (cold, after load) | 1.08 s | |
| LLM — cold total (load + answer) | 4.46 s | |
| LLM — warm answer | 0.99 s | |
| XTTS-v2 — load (CPU) | 21.69 s | 3.20 GB |
| XTTS-v2 — 12.26 s utterance | 11.09 s (real-time factor 0.905) | |
| Wav2Lip — 10 s clip, no sharpening | 8.6 s (0.86 s per second of speech) | 4.04 GB |
| Wav2Lip + GFPGAN — 10 s clip | 654.4 s (65.44 s per second of speech) | 4.21 GB |

## What this decides (HANDOFF.md section 8.3)

- A typical 15-second spoken answer takes about **0.2 minutes** to animate without sharpening and **16.4 minutes** with it.
- Video stays **off by default**; the toggle shows these numbers. At this speed live video is usable for short answers, but pre-render the demo anyway.
- Sharpening (GFPGAN) stays **off by default**; it is an explicit opt-in in Settings.
- Voice: about 0.9 s of compute per second of speech on the CPU; text never waits for it.
- Text: a warm answer takes 0.99 s; the splash on later launches waits about 3.38 s for the model to load.

Raw numbers: `benchmark.json` in `~/Library/Application Support/DeferralTwin/`.
