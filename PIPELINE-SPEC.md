# Long-Form Narrated-Video Pipeline — Technical Specification

**Status:** GREENFIELD / design spec. Stages 1–3 are built. Everything downstream
is design. Sections tagged `[DESIGN]` are intended behavior to implement;
`[REUSE]` marks logic that can be lifted from the existing Icarus Wings Shorts
pipeline; `[VALIDATE]` marks assumptions to confirm against a real run.
**Platform:** Windows-first, Python 3.12 (match existing Shorts stack).
**Brand:** Icarus Wings.
**Goal:** Turn a chosen *topic/angle* into a finished ~5-minute, 16:9, narrated
self-improvement video — stock-footage visuals carrying bold animated text, over
a single AI voiceover and an ambient bed.

> NOTE TO CLAUDE CODE: Do NOT scaffold all modules at once. Build and verify ONE
> stage at a time in the order in §4.

---

## 0. Editorial style — the semantic framework (standing instruction)

The channel's visual language is governed by a **semantic framework** that
applies to every script, now and in the future. It is not overridden per-video.

### The core rule

Never read a script line literally. Always ask: **what is the human scene
underneath this sentence?** A person, a place, a physical moment, a feeling
made visible. That scene is what you fetch. The words are just the path to it.

### Per-beat three-step process (mandatory, in order)

1. **Situation** — translate the line into a physical human moment. Who, where,
   doing what, feeling what. No abstractions. Write it as if describing a film
   shot to a cinematographer.
2. **Mood** — apply the channel's visual identity to every beat without
   exception: **cool, dark, cinematic, shadow-heavy, low-key lighting.** Add
   1–2 specific atmosphere words relevant to the beat (tense, still, exhausted,
   isolated, quietly determined). These travel with every query, always.
3. **Query** — combine situation + mood into a concrete **4–7 word** fetch
   string. Must name a literal, photographable object or scene.

### Hard-banned query words

> focus, willpower, discipline, motivation, biology, rhythm, alertness, energy,
> productivity, mindset, habit, growth, success, failure, struggle

If you write one, go one layer deeper into the physical scene.

### Tiers

| Tier | Description |
|------|-------------|
| 1 | Literal photographable scene |
| 2 | Concrete but needs specific framing |
| 3 | Abstract — must resolve to cinematic metaphor in the situation layer |

### Tier 3 metaphor targets

| Concept | Metaphor |
|---|---|
| Energy draining | candle burning low in dark room |
| Mental fog | dim window, rain-blurred glass |
| Clock / time | analogue clock face, long shadows on floor |
| Internal conflict | two hands gripping a desk edge |
| Biological force | tide moving in dark water, slow exhale of breath |

### Calibration gate (mandatory)

Before processing any new script: produce beats for the first 3 hook sentences.
Output them and stop. Wait for human approval before continuing. This catches a
misread interpretation on 3 beats instead of 60.

### Look

Dark, low-key, cool/desaturated, slow, calm-but-tense. Shadow-heavy. When two
clips match, pick the darker/cooler/slower.

### Legal

CC0 only (Pexels/Pixabay/Coverr). No anime/movie/meme assets.

---

## 1. Purpose & the one thing that's different from Shorts

The existing Shorts pipeline is **transcript → extract → decorate**: it pulls a
25–58s window out of someone else's podcast. The source of truth is audio that
already exists.

This pipeline is **topic → script → voice → style-edit**: there is no source
audio. The script is *authored*, voiced with TTS, then **edited in the §0
style** — mood footage + kinetic text + grade.

What CAN be borrowed is back-half plumbing: stock fetching, footage dedup, the
MoviePy render core, music bed, watermark, caching, publish.

## 2. Output contract

A single run produces:
- One **16:9 1920×1080 MP4**, ~4–6 min, single narrated voiceover over a mood
  stock-footage montage, **a kinetic-text layer**, a unifying dark/cool grade, a
  music bed, and a brand watermark.

## 3. Pipeline stages (high level)

```
topic/angle
  → [1] script_gen         authored ~800-word script, structured beats     [BUILT]
  → [2] beat_plan          situation/mood/query per beat (semantic framework) [BUILT]
  → [3] tts                edge-tts voiceover + word/segment timings       [BUILT]
  → [4] background         fetch cinematic footage per beat                [BUILT]
  → [5] video_gen          footage + GRADE + kinetic-text + music + wm
  → [6] publish            R2 upload + YouTube (reuse Shorts logic)
```

## 4. Build order (do these one at a time)

1. `script_gen` — **DONE**; `tmp/script_01.json` validated.
2. `beat_plan` — **DONE**; semantic framework (situation/mood/query/tier).
   Calibration gate built in. Target 40–65 beats per script.
3. `tts` — **DONE**; `tmp/voiceover_01.mp3` + timings (edge-tts).
4. `background` — **DONE** (code); fetches per beat using query field.
   Query-level cache reuses clips when adjacent beats share a query.
5. `video_gen` — assemble the first end-to-end video **with the grade + text
   engine**.
6. `publish` — last; reuse Shorts modules nearly as-is.

Do not proceed until the current stage produces a real artifact you've inspected.

## 5. Module data contracts

### 5.1 `script_gen` [BUILT — keep]
- **Input:** `{title, false_cause, true_cause, lever}` (angle skeleton) +
  optional research notes.
- **Output:** structured script object:
```
{ title, hook, sections:[{role, text}], keep_line, mechanism_confidence, est_words }
```
- **Hard rules:** structure false_cause → turn → true_cause → re_hook → lever →
  close; each payoff opens the next loop. **Mechanism integrity is
  non-negotiable.**

### 5.2 `beat_plan` [BUILT — semantic framework]
- **Input:** the structured script.
- **Output:** an ordered list of beats:
```
beats: [
  { line,                  # exact sentence(s) from the script
    situation,             # plain-language film shot description
    mood,                  # "cool, dark, cinematic — [atmosphere words]"
    query,                 # concrete 4–7 word photographable fetch string
    tier,                  # 1 (literal) | 2 (needs framing) | 3 (metaphor)
    section_role }         # hook|false_cause|turn|true_cause|re_hook|lever|close
]
```
- **Beat count:** target **40–65** for a ~800-word script. Warning logged below
  45; hard error below 28 or above 85.
- **Banned words in queries:** focus, willpower, discipline, motivation,
  biology, rhythm, alertness, energy, productivity, mindset, habit, growth,
  success, failure, struggle. Validated.
- **Mood prefix:** every beat's mood field must start with "cool, dark,
  cinematic". Validated.
- **Calibration gate:** `generate_calibration_beats(script)` produces first 3
  hook beats for human approval before the full run.

### 5.3 `background` [BUILT — beat-level fetch]
- **Input:** the beat list (each beat has a `query` field).
- **Behavior:** per beat, search for `query` via the degrade-never chain:
  **Pexels video → Pixabay video → simplified query fallback → gradient
  fallback.** All CC0-style / no-attribution / API.
- **Query cache:** beats with identical queries (normalized) reuse the same clip
  without a second API call.
- **Selection filter:** when multiple clips match, pick the longest-duration
  fresh clip (not previously used in this run or history).
- **Orientation:** landscape / 16:9.
- **Dedup:** two-tier id ledger (`run_ids` within-run, `history_ids` cross-video
  via `footage_history.json`).
- **Output:** `{ "beat_clips": [path_per_beat], "stats": {...} }`
- **[VALIDATE]** Pexels free-tier rate limit (200 req/hr) vs. 40–65 fetches +
  retries. Reuse `PEXELS_BACKOFFS`; expect throttling.

### 5.4 `tts` [BUILT]
- **Input:** full spoken text (concatenated sections).
- **Output:** `{audio_path, segments:[{start,end,text}]}` — edge-tts with
  sentence-boundary timings.
- **Engine:** edge-tts, `en-US-AndrewMultilingualNeural` voice.
- **[VALIDATE]** whether sentence timings are precise enough for per-beat sync,
  else forced-alignment pass (Groq Whisper on the generated audio).

### 5.5 `video_gen` [DESIGN — major additions: grade + text engine]
- **Reuse directly:** MoviePy v2 API only; bar-proof Ken Burns; crossfades;
  cover-crop; per-clip duration cap; music bed (−18 dB under VO, fade in/out);
  brand watermark; edge-brightness verification scan.
- **Add for this style:**
  - Frame = **1920×1080** (16:9), 30 fps.
  - Clip timing driven by **beat start/end** (from TTS timings).
  - **Unifying color-grade pass** — dark/cool LUT + grain on all footage.
  - **Kinetic-text engine** — renders keyword overlays and text cards.
  - Background darkening for text legibility.
- **Motion/music feel:** slow Ken Burns drift + gentle crossfades; hard cuts
  reserved for the "turn" reveal; ambient bed swells at turn and close.
- **Output:** final MP4 path.

### 5.6 `publish` [REUSE — Shorts `storage` + `youtube_publish`]
- R2 upload + YouTube upload, best-effort, never fatal to a completed render.
- Default privacy `private`; flag to override.

## 6. Architecture principles (carry over from Shorts)
- Linear, config-driven pipeline; `config.py` is the single source of truth.
- Publish is best-effort, never fatal once a render exists.
- Cache to protect API credit: cache script, TTS audio, and footage per video.
- Non-determinism (script + beat extraction) handled in-code: validate, retry,
  recover — never silently ship a malformed plan.

## 7. Config constants to define (`config.py`)
- `VIDEO_WIDTH=1920`, `VIDEO_HEIGHT=1080`, `VIDEO_FPS=30`.
- `TARGET_WORDS_MIN=700`, `TARGET_WORDS_MAX=900`, `NARRATION_WPM≈150`.
- `BEAT_MIN_SECONDS≈2`, `BEAT_MAX_SECONDS≈4`, `TARGET_BEATS≈40..65`.
- `MOOD_LUMA_*` selection filter (prefer dark/cool/slow); `GRADE_LUT`,
  `GRADE_GRAIN`.
- `TEXT_*` — fonts (bold-sans + italic-serif), `ACCENT_COLOR` (cold icy
  white-blue), card/overlay styles.
- Background chain order + per-source enable flags; reuse `PEXELS_*`,
  `FOOTAGE_HISTORY_MAX`.
- Music/watermark constants reused from Shorts.

## 8. External services & secrets (`.env`)
- **Script:** `ANTHROPIC_API_KEY`.
- **Voice:** edge-tts (no key required).
- **Footage:** `PEXELS_API_KEY`, `PIXABAY_API_KEY`.
- **(Optional) forced alignment:** `GROQ_API_KEY`.
- **Storage/publish [REUSE]:** Cloudflare R2 keys, YouTube OAuth.

## 9. Out of scope (v1)
- Sourcing copyrighted anime / movie / meme assets.
- Word-level karaoke captions.
- Instagram/TikTok publishing.
- Thumbnail generation (handle manually for now).
- Automated test suite beyond per-module smoke harnesses.
- AI-generated footage.

## 10. Definition of done (v1)
One command takes an angle skeleton and produces one watchable ~5-min 16:9 MP4,
end to end, in the §0 style: structurally-correct script, honest mechanism,
single consistent calm voiceover, **mood footage unified by a dark/cool grade**,
a **kinetic-text layer**, a music bed, and a watermark — uploaded private to
YouTube.
