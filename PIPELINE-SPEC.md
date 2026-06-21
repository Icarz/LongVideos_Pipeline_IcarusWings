# Long-Form Narrated-Video Pipeline — Technical Specification

**Status:** GREENFIELD / design spec. Stage 1 (`script_gen`) is built and has produced
one validated artifact (`tmp/script_01.json`). Everything downstream is design.
Sections tagged `[DESIGN]` are intended behavior to implement; `[REUSE]` marks logic
that can be lifted from the existing Icarus Wings Shorts pipeline; `[VALIDATE]` marks
assumptions to confirm against a real run and correct.
**Platform:** Windows-first, Python 3.12 (match existing Shorts stack).
**Brand:** Icarus Wings.
**Goal:** Turn a chosen *topic/angle* into a finished ~5-minute, 16:9, narrated
self-improvement video in a **specific editorial style** (see §0) — stock-footage
visuals carrying bold animated text, over a single AI voiceover and an ambient bed.

> NOTE TO CLAUDE CODE: Do NOT scaffold all modules at once. Build and verify ONE
> stage at a time in the order in §4. The creative core is now **two coupled things**:
> the mood-based footage match AND the kinetic-text layer. Get a real artifact from
> each hard stage before wiring downstream. The schemas in §5 are starting points
> expected to change after the first real beat-map.

---

## 0. Editorial style — the thing that defines this channel

The channel's look is a deliberate **blend of two reference edits** the user supplied
(a loud "discipline" edit and a calm "cinematic" edit). The synthesis, locked with the
user:

**V2's eyes, V1's voice** — cinematic/cool footage, carrying bold kinetic text, cut at
a medium-fast rhythm.

| Dimension | Source | Result |
|---|---|---|
| Mood / footage world | calm-cinematic ref | dark, cool, low-key, slow, atmospheric |
| Text / punch | discipline ref | kinetic keyword typography is a **lead layer**, not sparse |
| Pacing | between both | **~2–4s per visual** (≈ 75–130 beats for 5 min) |
| Structure | both (identical) | one branded segment-title card per section + full-frame text cards on key lines |
| Accent | new (cold) | dark/cool base + **one cold icy white-blue accent** (optional single warm-amber pop). NOT the refs' red. |

### The five rules that govern editing (these replace literal-noun matching)

1. **Footage is chosen by MOOD, not by noun.** A beat's job is to set the emotional
   backdrop for the spoken line and its on-screen text — NOT to literally depict the
   subject. **Explicitly dropped:** desk / clock / coffee / rubbing-eyes / head-in-hands
   / ticking-clock / battery-icon literalism. Forbidden.
2. **Text-as-A-roll.** Each sentence donates 1–3 keywords, animated large (bold-sans +
   italic-serif mix). The footage is the backdrop behind the moving word.
3. **Cohesion comes from grade + type, not from the clips.** Disparate CC0 stock is
   unified into one video by a **dark/cool color-grade (LUT) + grain + one type system +
   the single accent color**. This is how the references make mismatched footage feel
   like one piece. This is the core trick — without it the video looks like random stock.
4. **Branded spine.** One reusable segment-title-card template marks each section;
   full-frame text cards land the hook / turn / lever / close.
5. **Aesthetic queries, not documentary.** `beat_plan` emits cinematic-look queries
   (e.g. "silhouette walking fog backlit", "slow aerial ocean dusk moody"), scored on
   *look*, not literal accuracy.

### Visual worlds (the controlled vocabulary `beat_plan` picks from)

Every beat maps to ONE visual world. The fetch query must return cinematic stock in it:
- `lone_silhouette` — a single backlit/shadowed figure.
- `figure_in_landscape` — small human in a large natural scene.
- `nature_atmosphere` — fog, ocean, rain, light-rays, city-at-dusk, sky.
- `texture_abstract` — slow abstract motion, light, ink, particles, grain.
- `intimate_closeup` — hands, eyes, breath, small human detail.
- `slow_human_moment` — an unhurried, anonymous person-doing-something cinematic shot.

(If a beat genuinely needs a concrete object, it still goes through a visual world and
is shot cinematically, low-key — never bright catalogue stock.)

---

## 1. Purpose & the one thing that's different from Shorts

The existing Shorts pipeline is **transcript → extract → decorate**: it pulls a 25–58s
window out of someone else's podcast. The source of truth is audio that already exists.

This pipeline is **topic → script → voice → style-edit**: there is no source audio. The
script is *authored*, voiced with TTS, then **edited in the §0 style** — mood footage +
kinetic text + grade. The creative core (script, mood-matching, and the text layer)
does not exist in Shorts and cannot be borrowed.

What CAN be borrowed is back-half plumbing: stock fetching, footage dedup, the MoviePy
render core, music bed, watermark, caching, publish.

## 2. Output contract

A single run produces:
- One **16:9 1920×1080 MP4**, ~4–6 min, single narrated voiceover over a mood
  stock-footage montage, **a kinetic-text layer**, a unifying dark/cool grade, a music
  bed, and a brand watermark.
- On-screen text is a **core, frequent layer** (kinetic keyword animation + branded
  segment cards + full-frame text cards) — NOT word-level karaoke captions, and NOT the
  old "sparse, three-cases-only" rule. Text is stylized and rhythm-driven, landing on a
  large share of beats, never word-by-word.

## 3. Pipeline stages (high level)

```
topic/angle
  → [1] script_gen      authored ~800-word script, structured beats        [BUILT]
  → [2] beat_plan       split into mood beats; visual-world + query + TEXT per beat
  → [3] background      fetch cinematic footage per beat (Pexels→Pixabay→Coverr)
  → [4] tts             ElevenLabs voiceover + word/segment timings
  → [5] video_gen       footage + GRADE + kinetic-text engine + music + watermark
  → [6] publish         R2 upload + YouTube (reuse Shorts logic)
```

## 4. Build order (do these one at a time)

1. `script_gen` — **DONE**; `tmp/script_01.json` validated.
2. `beat_plan` — beat-map that real script by hand-in-code; let it define the real
   schema (mood beats + per-beat text). Measure real beat count and visual-world mix.
3. `background` — fetch cinematic footage for those real beats; measure the true
   look-match rate under the §0 mood filter.
4. `tts` — voice the real script; capture timings (these backfill beat start/end).
5. `video_gen` — assemble the first end-to-end video **with the grade + text engine**.
6. `publish` — last; reuse Shorts modules nearly as-is.

Do not proceed until the current stage produces a real artifact you've inspected.

## 5. Module data contracts

### 5.1 `script_gen` [BUILT — keep]
- **Input:** `{title, false_cause, true_cause, lever}` (angle skeleton) + optional
  research notes.
- **Output:** structured script object (unchanged shape):
```
{ title, hook, sections:[{role, text}], keep_line, mechanism_confidence, est_words }
```
- **Hard rules (unchanged):** structure false_cause → turn → true_cause → re_hook →
  lever → close; each payoff opens the next loop. **Mechanism integrity is
  non-negotiable** — `check` confidence must present uncertainty honestly. Does NOT
  invent statistics; factual claims come from supplied research notes only.
- Topics/angles are **unchanged** by the style pivot. Only editing changes downstream.

### 5.2 `beat_plan` [DESIGN — hardest stage; defines the style in data]
- **Input:** the structured script (+ later, tts word timings for exact start/end).
- **Output:** an ordered list of mood beats:
```
beats: [
  { start, end,            # timestamps (backfilled from tts)
    line,                  # the sentence(s) this beat covers
    tone,                  # emotional read of the line (e.g. tense, calm, resolve)
    visual_world,          # one of §0's six worlds
    query,                 # cinematic aesthetic fetch query (look, not literal)
    text:                  # the kinetic-text plan for this beat, or null
      { keywords,          #   1–3 words pulled from the line
        style },           #   card | keyword-overlay | segment-title
    section_role }         # carries the script section role for spine logic
]
```
- **Cadence:** target a visual change every **~2–4s** (NOT 3–8s). A 5-min video ≈
  **75–130 beats**.
- **Text density:** a large share of beats carry text (keywords or a card); hooks,
  turns, levers, and the close get the strongest treatment. Not every beat — footage-only
  breathing beats are allowed and desirable.
- **Segment spine:** the first beat of each script section emits a `segment-title` card
  (one reusable template).
- **Hard rule:** every beat emits a **cinematic-mood query** within its `visual_world`.
  "focus" is not a query; "lone silhouette at a window, cold light, shallow depth" is.
- **[VALIDATE]** real beat count, visual-world distribution, and text-density ratio on
  the first real script — fold the numbers back here.

### 5.3 `background` [REUSE plumbing — new selection criteria]
- **Input:** the beat list.
- **Behavior:** per beat, fetch a clip for `query` via the degrade-never chain:
  **Pexels video → Pixabay video (incl. AI category for `texture_abstract`) → Coverr →
  gradient/solid fallback.** All CC0-style / no-attribution / API. Do NOT add
  attribution-required sources (Videezy, Vidsplay, Dareful, Freepik). Anime/movie/meme
  assets seen in the references are **off-limits (copyright)** — we reproduce the
  *rhythm*, not those assets.
- **Selection filter [§0]:** when multiple clips match, pick the **darker / cooler /
  slower** one. Reject bright, busy, corporate-catalogue stock. The grade in §5.5 then
  unifies what's kept.
- **Orientation:** landscape / 16:9.
- **Dedup [REUSE]:** two-tier id ledger (`used_ids` within-run, `history_ids`
  cross-video via `footage_history.json`). At 75–130 beats/video this matters a lot.
- **Output:** ordered list of clip paths aligned 1:1 to beats.
- **[VALIDATE]** Pexels free-tier rate limit (200 req/hr) vs. 75–130 fetches + retries.
  Reuse `PEXELS_BACKOFFS`; expect throttling.

### 5.4 `tts` [DESIGN — REUSE pattern from Shorts transcribe contract]
- **Input:** full spoken text (concatenated sections).
- **Output:** `{audio_path, words:[{word,start,end}], segments:[{start,end,text}]}` —
  same shape as Shorts `transcribe`, so beat timings backfill cleanly.
- **Engine:** ElevenLabs, one fixed voice ID (consistency). Calm, low, measured
  narrator (~140–150 wpm). Store voice ID in `config`.
- **[VALIDATE]** whether ElevenLabs word timings are usable, else forced-alignment pass
  (Groq Whisper on the generated audio — `align.py`). Assume the latter until proven.

### 5.5 `video_gen` [REUSE MoviePy core — major additions: grade + text engine]
- **Reuse directly:** MoviePy v2 API only; bar-proof Ken Burns (`_ken_burns_motion`,
  single resize + clamped pan); crossfades; cover-crop to exact frame; per-clip duration
  cap; music bed (`_music_track`, −18 dB under VO, fade in/out); brand watermark;
  edge-brightness verification scan.
- **Add for this style:**
  - Frame = **1920×1080** (16:9), 30 fps.
  - Clip timing driven by **beat start/end**.
  - **Unifying color-grade pass** — a dark/cool LUT + grain applied to all footage so
    disparate stock reads as one video (§0 rule 3). This is mandatory, not optional.
  - **Kinetic-text engine** — renders the `text` plan per beat: (a) `keyword-overlay`
    (1–3 words animated large over footage, bold-sans + italic-serif mix, cold accent);
    (b) `card` (full-frame text on graded/solid bg for hook/turn/lever/close); (c)
    `segment-title` (one reusable branded template per section). Near-white + the single
    cold accent; tasteful motion-in/out. **No word-by-word karaoke.**
  - Background darkening kept under text for legibility.
- **Motion/music feel [§0]:** slow Ken Burns drift + gentle crossfades by default; hard
  cuts reserved for the "turn" reveal; ambient bed swells slightly at the turn and the
  close.
- **Output:** final MP4 path.

### 5.6 `publish` [REUSE — Shorts `storage` + `youtube_publish`]
- R2 upload + YouTube upload, best-effort, never fatal to a completed render.
- Default privacy `private`; flag to override.
- Long-form is NOT a Short — set category/metadata accordingly; do not tag `#Shorts`,
  do not constrain to <60s.

## 6. Architecture principles (carry over from Shorts)
- Linear, config-driven pipeline; `config.py` is the single source of truth.
- Publish is best-effort, never fatal once a render exists.
- Cache to protect API credit: cache script, TTS audio, and footage per video.
- Non-determinism (script + beat extraction) handled in-code: validate, retry, recover
  — never silently ship a malformed plan.

## 7. Config constants to define (`config.py`)
- `VIDEO_WIDTH=1920`, `VIDEO_HEIGHT=1080`, `VIDEO_FPS=30`.
- `TARGET_WORDS_MIN=700`, `TARGET_WORDS_MAX=900`, `NARRATION_WPM≈150`.
- `BEAT_MIN_SECONDS≈2`, `BEAT_MAX_SECONDS≈4`, `TARGET_BEATS≈75..130`.
- `VISUAL_WORLDS` (the §0 list) + per-world query hints.
- `MOOD_*` selection filter (prefer dark/cool/slow); `GRADE_LUT`, `GRADE_GRAIN`.
- `TEXT_*` — fonts (bold-sans + italic-serif), `ACCENT_COLOR` (cold icy white-blue),
  card/overlay/segment-title styles, text-density target.
- `ELEVENLABS_VOICE_ID` (fixed, placeholder until user picks), `TTS_MODEL`.
- Background chain order + per-source enable flags; reuse `PEXELS_*`,
  `FOOTAGE_HISTORY_MAX`.
- Music/watermark constants reused from Shorts.

## 8. External services & secrets (`.env`)
- **Script:** `ANTHROPIC_API_KEY`.
- **Voice:** `ELEVENLABS_API_KEY`.
- **Footage:** `PEXELS_API_KEY`, `PIXABAY_API_KEY`, Coverr access.
- **(Optional) forced alignment:** `GROQ_API_KEY`.
- **Storage/publish [REUSE]:** Cloudflare R2 keys, YouTube OAuth
  (`YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN`).

## 9. Out of scope (v1)
- Sourcing copyrighted anime / movie / meme assets (the references use them; we do not).
- Word-level karaoke captions.
- Instagram/TikTok publishing.
- Thumbnail generation (handle manually for now).
- Automated test suite beyond per-module smoke harnesses.
- AI-generated footage — flagged open; revisit at the `background` stage if the CC0 chain
  can't fill `texture_abstract` / metaphor beats.

## 10. Definition of done (v1)
One command takes an angle skeleton and produces one watchable ~5-min 16:9 MP4, end to
end, in the §0 style: structurally-correct script, honest mechanism, single consistent
calm voiceover, **mood footage unified by a dark/cool grade**, a **kinetic-text layer**
at ~2–4s cadence with branded segment cards, a music bed, and a watermark — uploaded
private to YouTube. Ugly-but-complete beats polished-but-partial. Measure real numbers
(beat count, visual-world mix, text density, look-match rate, runtime) on the first run
and fold them back into this spec.
