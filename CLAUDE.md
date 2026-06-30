# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python pipeline that turns a topic/angle into a finished long-form narrated YouTube video for the "Icarus Wings" channel, in the **"Hugh Knows"** style: punchy, direct, numbered-tips format, minimalist hand-drawn stick-figure illustrations, warm cream background. Windows-first, Python 3.12, no web framework — five sequential CLI stages connected by JSON files on disk.

`config.py` at the project root is the single source of truth for every tunable constant (models, word/beat targets, image style, fonts, audio levels, etc.). Change behavior there, not by hardcoding in module bodies.

## Commands

There is no orchestrator script and no build/lint/test tooling configured (`tests/` is empty, no `pyproject.toml`/`pytest.ini`/linter config exists). Each stage is run manually, in order, from the project root using the venv interpreter:

```
venv\Scripts\python.exe -m modules.script_gen
venv\Scripts\python.exe -m modules.beat_plan
venv\Scripts\python.exe -m modules.tts
venv\Scripts\python.exe -m modules.image_gen
venv\Scripts\python.exe -m modules.compositor
```

**Must use `-m modules.<name>`, not `python modules/<name>.py`.** Every module does a bare `import config`, which only resolves if the project root is on `sys.path` — `-m` invocation from the root adds it automatically; running the file path directly does not, and raises `ModuleNotFoundError: No module named 'config'`.

Each module also doubles as its own smoke test: the `if __name__ == "__main__":` block at the bottom of every file reads the previous stage's JSON output from `tmp/`, runs the stage, and prints a summary. `script_gen.py`'s smoke test uses a hardcoded sample angle (`ANGLE_01`) instead of reading a file, since it's the first stage.

Install dependencies: `venv\Scripts\pip install -r requirements.txt`.

## Pipeline architecture

Five stages, each independently runnable, passing data forward through JSON files in `tmp/`:

```
topic/angle
  → [1] script_gen.py   (Claude Opus 4.8)   → tmp/script_01.json
  → [2] beat_plan.py    (Claude Opus 4.8)   → tmp/beats_01.json
  → [3] tts.py           (edge-tts)         → tmp/tts_timings_01.json + tmp/voiceover_01.mp3
  → [4] image_gen.py    (OpenAI gpt-image-1)→ tmp/clips_manifest_01.json (+ PNGs in tmp/image_cache/)
  → [5] compositor.py   (ffmpeg + Pillow)   → output/video_01.mp4
```

**Stage 1 — `script_gen.py`**: generates the narration script (intro, 6 tips, outro) from an angle dict (`{title, hook_premise, topic, research_notes?}`). Prompts include a hand-authored gold-standard exemplar (`scripts/script-01-2pm-focus.txt`, path in `config.EXEMPLAR_SCRIPT_PATH`) for tone/rhythm calibration. Target word count: `TARGET_WORDS_MIN`–`TARGET_WORDS_MAX` (400–550).

**Stage 2 — `beat_plan.py`**: splits the script into 40–65 timed beats. This is also where per-beat **image prompts are authored** — there is no separate visual-direction stage. Each beat is classified `beat_type: "illustration"` (gets an `image_prompt`, one-sentence scene description) or `"text_card"` (gets `card_text`, the literal text to render on screen); the two fields are mutually exclusive and validated as such. A calibration gate (`generate_calibration_beats`) produces only the first few beats for a quick sanity check before the full run.

**Stage 3 — `tts.py`**: synthesizes voiceover with `edge-tts` (`TTS_VOICE`, `TTS_RATE = "-10%"`), aligns word/sentence timings from the edge-tts SubMaker output (no Whisper forced-alignment is actually wired up despite `config.ALIGN_MODEL` existing — that's a documented-but-unused fallback path).

**Stage 4 — `image_gen.py`**: generates one PNG per `illustration` beat via `gpt-image-1` (`text_card` beats are skipped — `None` in the manifest, since the compositor renders those directly with Pillow). Caches by `sha256(style_prefix + scene_prompt)` in `tmp/image_cache/`, so identical scenes across runs/videos reuse the same file. Fixed 12s sleep between live calls (rate limiting); any API failure falls back to a solid cream PNG so the pipeline never halts. **`config.IMAGE_GEN_STYLE` is the single highest-leverage constant** — it's the full art-direction prompt prefix applied to every illustration beat; changing it changes the look of every future video without touching beat_plan.

**Stage 5 — `compositor.py`**: assembles the final MP4. Each beat (illustration PNG or rendered text-card PNG) becomes a static clip — scaled/padded to 1920×1080 with a quick fade in/out — then concatenated, captioned (full beat text burned in, not karaoke), watermarked, and mixed with an ambient music bed. **There is no Ken Burns pan/zoom** despite that being in early design notes; images are static within their beat duration. Final video/audio is sped up by `config.AUDIO_TEMPO` (1.20×) — this is deliberately offset against `TTS_RATE = "-10%"` (an inverse-compensation pair: slow the TTS engine down for clearer diction, then speed the final cut back up for pacing).

## Non-obvious things worth knowing

- **`PIPELINE-SPEC.md` is substantially outdated** — it documents an earlier design (Pexels/Pixabay stock-footage fetching, a cinematic dark/cool "situation/mood/query" semantic framework, false_cause/true_cause/lever script structure, Ken Burns motion) that was superseded by the current AI-image-generation / tips-format pipeline described above. Treat the actual code and `config.py` as ground truth, not that file. `docs/superpowers/specs/2026-06-30-image-gen-pipeline-design.md` is the up-to-date design reference for the image-generation stage specifically.
- `modules/background.py` (old stock-video fetcher) and `modules/visual_direction.py` (an earlier, never-wired-up cinematic-noir prompt-authoring design) were deleted as dead code — if either name shows up in a stale doc or old commit, it no longer exists.
- The image-gen manifest file is named `tmp/clips_manifest_01.json` (not `images_manifest_01.json`) — a holdover from when that stage produced video clips, kept for compositor compatibility.
- `.env` still carries unused legacy keys (`PEXELS_API_KEY`, `PIXABAY_API_KEY`, `COVERR_API_KEY`, `ELEVENLABS_API_KEY`) from the pre-migration pipeline; `OPENAI_API_KEY` (image_gen) and `ANTHROPIC_API_KEY` (script_gen, beat_plan) are the ones actually read.
- `GROQ_API_KEY` / `config.ALIGN_MODEL` are wired into `.env`/`config.py` but not actually called anywhere — a planned Whisper forced-alignment fallback for word timings that was never implemented.
