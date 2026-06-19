# Long-Form Narrated-Video Pipeline — Technical Specification

**Status:** GREENFIELD / design spec. Nothing here is built yet. Sections tagged
`[DESIGN]` are intended behavior to implement; `[REUSE]` marks logic that can be
lifted from the existing Icarus Wings Shorts pipeline; `[VALIDATE]` marks
assumptions that must be confirmed against a real run and corrected.
**Platform:** Windows-first, Python 3.12 (match existing Shorts stack).
**Brand:** Icarus Wings.
**Goal:** Turn a chosen *topic/angle* into a finished ~5-minute, 16:9, narrated
self-improvement video with stock-footage visuals and a single AI voiceover.

> NOTE TO CLAUDE CODE: Do NOT scaffold all modules at once. Build and verify ONE
> stage at a time in the order in section 4. The two hardest stages
> (`script_gen`, `beat_plan`) have no proven output yet — get a single real
> artifact from each before wiring downstream. Treat the schema in 5.x as a
> starting point expected to change after the first real script.

---

## 1. Purpose & the one thing that's different from Shorts

The existing Shorts pipeline is **transcript -> extract -> decorate**: it pulls a
25–58s window out of someone else's podcast and decorates it. The source of truth
is audio that already exists.

This pipeline is **topic -> script -> voice -> decorate**: there is no source
audio. The script is *authored*, then voiced with TTS, then matched to footage
beat by beat. The creative core is the script and the script-to-footage matching —
neither exists in the Shorts pipeline and neither can be borrowed.

What CAN be borrowed is the back half: stock fetching, footage dedup, the MoviePy
render core, music bed, watermark, caching, and publish.

## 2. Output contract

A single run produces:
- One **16:9 1920x1080 MP4**, ~4–6 min, single narrated voiceover over a
  stock-footage montage with a music bed and brand watermark.
- Sparse on-screen text for emphasis only (NOT word-level karaoke captions — that
  is a Shorts convention and is explicitly out of scope here).

## 3. Pipeline stages (high level)

```
topic/angle
  -> [1] script_gen      authored ~800-word script, structured beats
  -> [2] tts             ElevenLabs voiceover + word/segment timings
  -> [3] beat_plan       split script into visual beats; query + tier per beat
  -> [4] background      fetch footage per beat (Pexels -> Pixabay -> Coverr)
  -> [5] video_gen       assemble VO + footage + music + watermark + text
  -> [6] publish         R2 upload + YouTube (reuse Shorts logic)
```

## 4. Build order (do these one at a time)

1. `script_gen` — get ONE good script out first. Everything depends on its shape.
2. `beat_plan` — beat-map that one real script by hand-in-code; let it define the
   real schema.
3. `background` — fetch footage for those real beats; measure the true match rate.
4. `tts` — voice the real script; capture timings.
5. `video_gen` — assemble the first ugly end-to-end video.
6. `publish` — last; reuse Shorts modules nearly as-is.

Do not proceed to the next stage until the current one produces a real artifact
you've inspected.

## 5. Module data contracts

### 5.1 `script_gen` [DESIGN — hardest stage, no proven output]
- **Input:** `{title, false_cause, true_cause, lever}` (the angle skeleton) +
  optional research notes.
- **Output:** a structured script object:
```
{
  title,
  hook,                      # cold-open, 0-20s
  sections: [                # ordered narrative sections
    { role,                  # one of: false_cause, turn, true_cause,
                             #         re_hook, lever, close
      text }                 # the spoken prose for this section
  ],
  keep_line,                 # the one proof sentence that must never be cut
  mechanism_confidence,      # solid | partial | check
  est_words                  # ~700-900
}
```
- **Hard rules:**
  - Structure must follow false_cause -> turn -> true_cause -> re_hook -> lever
    -> close. Each payoff opens the next loop (no clean exits mid-script).
  - **Mechanism integrity is non-negotiable.** If `mechanism_confidence` is
    `check`, the script must present the uncertainty honestly, never assert a
    contested claim as fact. A confidently-wrong mechanism is the worst failure
    mode for this channel.
  - `script_gen` does NOT invent statistics. Factual claims come from supplied
    research notes only; unverifiable claims are cut or hedged.
- **[VALIDATE]** word count -> runtime ratio (assume ~150 wpm narration; confirm
  against first real TTS render).

### 5.2 `tts` [DESIGN — REUSE pattern from Shorts transcribe contract]
- **Input:** the script's full spoken text (concatenated sections).
- **Output:** `{audio_path, words:[{word,start,end}], segments:[{start,end,text}]}`
  — same shape as the Shorts `transcribe` contract, so downstream timing logic is
  familiar.
- **Engine:** ElevenLabs API, one fixed voice ID (consistency across all videos).
  Store voice ID in `config`.
- **[VALIDATE]** whether ElevenLabs returns usable word timings directly, or
  whether a forced-alignment pass (e.g. re-running Groq Whisper on the generated
  audio, reusing the Shorts transcribe module) is needed to get per-word timing.
  Assume the latter until proven otherwise.

### 5.3 `beat_plan` [DESIGN — the new creative-matching stage]
- **Input:** the structured script + word timings from `tts`.
- **Output:** an ordered list of visual beats:
```
beats: [
  { start, end,              # timestamps from tts word timings
    line,                    # the sentence(s) this beat covers
    visual,                  # plain-language description of what's on screen
    query,                   # the literal fetch query (concrete noun phrase)
    tier }                   # 1 | 2 | 3 (see below)
]
```
- **Beat cadence:** target a visual change every ~3–8s, NOT one image per section.
  A 5-min video = roughly 40–70 beats. (Contrast Shorts: 4 slots total.)
- **Tier tags:**
  - **Tier 1** — literal stock exists (person working, city street, coffee, walk).
  - **Tier 2** — needs a specific concrete query but stock exists (e.g. "person
    rubbing eyes at desk afternoon").
  - **Tier 3** — abstract beat with NO literal footage (circadian clock, alertness
    dropping). Resolve with a **metaphor** chosen here, in the plan, that emits a
    real query (e.g. clock = literal clock face; energy draining = candle burning
    down). Pixabay's AI-video category is an allowed source for Tier 3.
- **Hard rule:** every beat MUST emit a fetchable concrete `query`. "focus" is not
  a query; "person staring blankly at laptop" is.
- **[VALIDATE]** real Tier 1/2/3 distribution. Design assumption: ~60% Tier 1–2,
  ~40% Tier 3. The true ratio decides how much the abstraction gap hurts. Measure
  it on the first real script.

### 5.4 `background` [REUSE — adapt Shorts `background.select_backgrounds`]
- **Input:** the beat list.
- **Behavior:** per beat, fetch a clip for `query` via the degrade-never chain:
  **Pexels video -> Pixabay video (incl. AI category for Tier 3) -> Coverr ->
  gradient/solid fallback.** All three are CC0-style / no-attribution / API.
  Do NOT add attribution-required sources (Videezy, Vidsplay, Dareful, Freepik).
- **Orientation:** landscape/16:9 (Shorts used portrait — flip the orientation
  param).
- **Dedup [REUSE]:** keep the two-tier id ledger (`used_ids` within-run,
  `history_ids` cross-video via `footage_history.json`). At 40–70 beats/video this
  matters more than in Shorts, not less.
- **Output:** ordered list of clip paths aligned 1:1 to beats.
- **[VALIDATE]** Pexels free-tier rate limit (200 req/hr) vs. 40–70 fetches per
  video + retries. May need throttling/backoff (reuse `PEXELS_BACKOFFS`).

### 5.5 `video_gen` [REUSE — adapt Shorts MoviePy v2 core]
- **Reuse directly:** MoviePy v2 API only; bar-proof Ken Burns
  (`_ken_burns_motion`, single resize + clamped pan); crossfades; cover-crop to
  exact frame; per-clip duration cap; music bed (`_music_track`, -18 dB under VO,
  fade in/out); brand watermark.
- **Change for long-form:**
  - Frame = **1920x1080** (16:9), not 1080x1920.
  - Clip timing is driven by **beat start/end**, not a fixed slot model.
  - **No karaoke captions.** Replace with optional sparse on-screen emphasis text
    on selected beats only (driven by an optional `emphasis_text` field on a beat).
  - Background overlay/darkening kept (legibility for any on-screen text).
- **Output:** final MP4 path.
- **Edge-bar verification [REUSE]:** keep the edge-brightness scan that catches
  Ken Burns miscomposites.

### 5.6 `publish` [REUSE — Shorts `storage` + `youtube_publish`]
- R2 upload + YouTube upload, best-effort, never fatal to a completed render.
- Default privacy `private`; flag to override.
- Long-form is NOT a Short — set YouTube category/metadata accordingly; do not tag
  `#Shorts` and do not constrain to <60s.

## 6. Architecture principles (carry over from Shorts)
- Linear, config-driven pipeline; `config.py` is the single source of truth.
- Publish is best-effort, never fatal once a render exists.
- Cache to protect API credit: cache script, TTS audio, and footage per video.
- Non-determinism (script + beat extraction) handled in-code: validate, retry,
  recover — never silently ship a malformed plan.

## 7. Config constants to define (`config.py`)
- `VIDEO_WIDTH=1920`, `VIDEO_HEIGHT=1080`, `VIDEO_FPS=30`.
- `TARGET_WORDS_MIN=700`, `TARGET_WORDS_MAX=900`, `NARRATION_WPM≈150`.
- `BEAT_MIN_SECONDS≈3`, `BEAT_MAX_SECONDS≈8`.
- `ELEVENLABS_VOICE_ID` (fixed), `TTS_MODEL`.
- Background chain order + per-source enable flags; reuse `PEXELS_*`,
  `FOOTAGE_HISTORY_MAX`.
- Music/watermark constants reused from Shorts.

## 8. External services & secrets (`.env`)
- **Script:** `ANTHROPIC_API_KEY`.
- **Voice:** `ELEVENLABS_API_KEY`.
- **Footage:** `PEXELS_API_KEY`, `PIXABAY_API_KEY`, Coverr access.
- **(Optional) forced alignment:** `GROQ_API_KEY` (reuse Whisper for word timings
  if ElevenLabs timings are insufficient).
- **Storage/publish [REUSE]:** Cloudflare R2 keys, YouTube OAuth
  (`YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN`).

## 9. Out of scope (v1)
- Animation / motion graphics / GSAP (explicitly dropped).
- Word-level karaoke captions.
- Instagram/TikTok publishing.
- Thumbnail generation (separate concern; handle manually for now).
- Automated test suite beyond per-module smoke harnesses.

## 10. Definition of done (v1)
One command takes an angle skeleton and produces one watchable ~5-min 16:9 MP4,
end to end, with: a structurally-correct script, an honest mechanism, a single
consistent voiceover, footage that matches each beat at ~3–8s cadence, a music
bed, and a watermark — uploaded private to YouTube. Ugly-but-complete beats
polished-but-partial. Measure real numbers (beat count, tier ratio, match rate,
runtime) during this first run and fold them back into this spec.
