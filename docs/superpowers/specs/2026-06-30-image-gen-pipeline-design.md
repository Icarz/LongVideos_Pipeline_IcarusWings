# Image Generation Pipeline — Design Spec
**Date:** 2026-06-30 (design) — **updated 2026-07-01 to match as-built code + finalized art style**
**Status:** Implemented
**Replaces:** Pexels/Pixabay stock footage fetching (`background.py`, deleted)

---

## 1. Summary

Stock video fetching (Pexels → Pixabay → gradient fallback) is replaced with AI-generated still images via OpenAI's `gpt-image-1` model. Each `illustration` beat gets one generated PNG; the compositor encodes it as a static fade-in/fade-out clip (no Ken Burns pan/zoom — see §5). `text_card` beats render their own PNG via Pillow and never call the image API.

Per-beat image prompts are **not** a separate stage — `beat_plan.py` writes `image_prompt` (or `card_text`) directly as part of its beat-classification output. The originally-planned standalone `visual_direction.py` stage was never built this way; the file that existed under that name (a leftover cinematic-noir "male silhouette" design) was deleted as dead code.

---

## 2. Pipeline After Change

```
script_gen.py   →  tmp/script_01.json
beat_plan.py    →  tmp/beats_01.json            (beat_type + image_prompt/card_text per beat)
tts.py          →  tmp/tts_timings_01.json + tmp/voiceover_01.mp3
image_gen.py    →  tmp/clips_manifest_01.json   (beat_images: [path|null, ...]; PNGs in tmp/image_cache/)
compositor.py   →  output/video_01.mp4
```

Stages untouched: `script_gen.py`, `tts.py`. `beat_plan.py` absorbed the prompt-authorship job originally scoped for `visual_direction.py`.

---

## 3. `beat_plan.py` — image prompts live here, not in a separate stage

### Job
Same call that splits the script into beats also classifies each beat as `illustration` or `text_card` and writes the matching field in the same pass — one Claude Opus 4.8 call, one JSON object, no second model in the loop.

### Beat shape (`tmp/beats_01.json`)
```json
{
  "beats": [
    {
      "line": "exact words from the script narration",
      "beat_type": "illustration",
      "image_prompt": "stick figure sitting at desk staring at clock looking bored",
      "card_text": null,
      "section_role": "tip_1"
    },
    {
      "line": "Tip 1: Trick Your Brain",
      "beat_type": "text_card",
      "image_prompt": null,
      "card_text": "Tip 1:\nTrick Your Brain",
      "section_role": "tip_1"
    }
  ]
}
```

- `image_prompt` is a **single short scene sentence** ("who is doing what, what prop is visible") — the art-style prefix is applied later in `image_gen.py`, not written per-beat. This keeps style changes a one-line config edit instead of a beat_plan re-run.
- `text_card` beats never get an `image_prompt` (validated: must be `null`); `illustration` beats never get `card_text` (also validated `null`).
- Validation: every beat must have a non-empty `image_prompt` xor `card_text` matching its `beat_type`.

---

## 4. `image_gen.py` — generates PNGs for `illustration` beats only

### Job
Read `tmp/beats_01.json`, skip `text_card` beats (`None` in the output array), call OpenAI's Images API for every `illustration` beat, write `tmp/clips_manifest_01.json`.

### API call (actual, `modules/image_gen.py:_generate_image`)
```python
client.images.generate(
    model=config.IMAGE_GEN_MODEL,     # "gpt-image-1"
    prompt=full_prompt,               # IMAGE_GEN_STYLE + ", " + beat["image_prompt"]
    size=config.IMAGE_GEN_SIZE,       # "1536x1024" (landscape)
    quality=config.IMAGE_GEN_QUALITY, # "low"
    n=1,
)
```
No `response_format` param is passed — `gpt-image-1` returns `b64_json` by default; decoded and written straight to disk.

### Caching
Cache key is `sha256(full_prompt)[:16]` (the **style-prefixed** prompt, not just the beat's scene line) → `tmp/image_cache/<hash>.png`. If that file exists and is `> 1000` bytes, generation is skipped — this means two different videos that happen to produce the same scene description hit the same cached PNG. Safe to re-run mid-batch after a failure without burning credits.

### Rate limiting & retry
- Fixed **12-second sleep** after every successful (non-cached) generation — no exponential-backoff table, no separate `IMAGE_GEN_DELAY`/`IMAGE_GEN_BACKOFFS` constants (the original plan's values were never added to `config.py`).
- On any exception (429, other HTTP error, empty response): log the error and write a **solid cream fallback PNG** (`config.VIDEO_BG_COLOR`, 1536×1024) so the pipeline never halts.

### Output manifest (`tmp/clips_manifest_01.json`)
```json
{
  "beat_images": [
    "tmp/image_cache/a5d3218175d2fff2.png",
    null
  ],
  "stats": { "generated": 38, "cached": 0, "skipped": 8, "failed": 0 }
}
```
`beat_images` has exactly one entry per beat (`null` for every `text_card` beat) — `compositor.py` asserts `len(beat_images) == len(beats)`.

### Cost per video (at `IMAGE_GEN_QUALITY = "low"`, 1536×1024, ~46 beats / ~38 illustration beats)
| Beats needing images | Cost (~$0.016/image) |
|---|---|
| 32 (low end of range, more text_cards) | ~$0.51 |
| 38 (typical) | ~$0.61 |
| 57 (high end of range) | ~$0.91 |

(`medium` quality is ~$0.063/image, ~4x; `high` is ~$0.246/image, ~15x — see config comment.)

---

## 5. `compositor.py` — reads the PNG manifest, encodes static (not Ken Burns) clips

### What it actually does (no animated pan/zoom)
- Reads `tmp/clips_manifest_01.json` → `beat_images`, zipped 1:1 against `beats_timed`.
- Each `illustration` beat: `_encode_illustration_clip(image_path, dur, beat_idx, role, out)` — `ffmpeg -loop 1 -i <png>` scaled to fit 1920×1080 (`force_original_aspect_ratio=decrease`), **padded with the cream background color** to fill the frame (no crop, no zoom), then a quick fade-in/fade-out (`fade_dur = min(0.35, dur*0.12)`).
- Each `text_card` beat: `_render_text_card_png()` (Pillow, centered serif text on cream) → encoded the same way as an illustration clip.
- The original design's "Ken Burns: upscale to 1920×1920, crop 1920×1080, animate zoom 1.0x→1.10x + pan drift up to 40px" was **never implemented** — there is no `BG_KENBURNS_ZOOM`/`BG_KENBURNS_PAN` in `config.py`. If motion is wanted later, that's still open work, not something to assume is already happening.
- `role == config.BG_HARD_CUT_ROLE` (the first `tip_1` beat) skips the fade-in for a hard cut; everything else fades both ways.
- Concat, captions, audio mixing, color treatment (no LUT/grain — that was a Shorts-pipeline holdover that was also dropped), watermark: unchanged from the rest of the pipeline.

### What gets deleted
- mp4 clip loading/decoding logic (was already removed along with `background.py`).

---

## 6. `config.py` — actual current state

```python
# --- Image generation (stage 4) --------------------------------------------
IMAGE_GEN_MODEL     = "gpt-image-1"
IMAGE_GEN_SIZE      = "1536x1024"   # landscape — valid gpt-image-1 size
IMAGE_GEN_QUALITY   = "low"         # ~$0.016/image; medium ~$0.063, high ~$0.246
IMAGE_GEN_STYLE     = (
    "A minimalist hand-drawn stick figure illustration in classic cartoon "
    "style, black ink line art on a textured cream/beige paper background. "
    "The figure has a perfectly round head with simple dot eyes, a small "
    "smile, and a tuft of messy hair on top. The body and limbs are drawn "
    "with thin, slightly wobbly, uniform-width black lines — no shading, "
    "no thickness variation, no digital smoothness. Add small motion lines "
    "near moving joints (hands, feet) to suggest action. Imperfect "
    "hand-drawn linework, not vector, not smooth, slightly rough linework, "
    "like it was drawn quickly with a fine-tip pen. Single centered scene, "
    "no color"
)
IMAGE_GEN_CACHE_DIR = os.path.join(TMP_DIR, "image_cache")
```

`IMAGE_GEN_DIR`, `IMAGE_GEN_DELAY`, `IMAGE_GEN_BACKOFFS` from the original plan were never added — the actual constants are `IMAGE_GEN_CACHE_DIR` (not `IMAGE_GEN_DIR`) and the 12s sleep / fallback-on-exception logic is hardcoded in `image_gen.py` rather than config-driven. `IMAGE_GEN_STYLE` was not in the original plan at all — it's the art-direction knob that took three iterations (see below) to land on, and it's the single highest-leverage constant for changing how every video looks.

### Art-style iteration log (why this exact wording)
1. **First pass** — generic "minimalist black line-art... clean and flat": produced bold, thick, marker-style lines with garbled clock numerals. Too far from the target stock-cartoon look.
2. **Second pass** — added "thin uniform-weight... classic stock-cartoon doodle style... small tuft of hair": closer, clock face cleaned up, but still thicker/bolder than the reference and the face read as too cartoon-expressive.
3. **Final (current)** — explicit "thin, slightly wobbly, uniform-width black lines — no shading, no thickness variation, no digital smoothness" + "imperfect hand-drawn linework, not vector, not smooth" + "small motion lines near moving joints": landed closest to the reference. Known remaining gap: gpt-image-1 sometimes renders the "rough linework" instruction as smudgy/charcoal shading on limbs rather than clean wobbly ink lines — if that recurs, the next lever to pull is adding "clean thin ink line, no smudging, no grayscale shading, pure black linework only".

### Removed (already done, prior commit)
- `BACKGROUND_CHAIN`, `PEXELS_*`, `PIXABAY_*`, `COVERR_*` config references (background.py deleted; the API keys remain in `.env`, unused)
- `FOOTAGE_HISTORY_PATH`, `FOOTAGE_HISTORY_MAX`
- `MOOD_LUMA_*`, `MOOD_COOL_BIAS_WEIGHT`, `MOOD_DARK_WEIGHT`
- `EDGE_SCAN_STRIP_PX`, `EDGE_SCAN_INTERVAL_S`, `EDGE_DARK_THRESHOLD`, `EDGE_BRIGHT_THRESHOLD` (edge-brightness verification scan — depended on `numpy`/`opencv`, neither of which is used anywhere in the current codebase)

---

## 7. Deletions (done)

| File / Dir | Action | Status |
|---|---|---|
| `modules/background.py` | Delete | ✅ Done |
| `modules/visual_direction.py` (old cinematic-noir design, never wired up) | Delete | ✅ Done |
| `requirements.txt`: `requests`, `elevenlabs`, `moviepy`, `opencv-python-headless`, `numpy` | Remove (unused) | ✅ Done |
| `requirements.txt`: `openai`, `edge-tts` | Add (were missing) | ✅ Done |
| `tmp/footage_history.json`, `tmp/clips/` | Clear stale pre-migration artifacts | Not yet done |

`.env` requires `OPENAI_API_KEY` — now set. Pexels/Pixabay/Coverr keys remain in `.env`, unused.

---

## 8. Out of Scope

- No video generation (Sora, Runway, etc.) — images only.
- No per-beat style variation — `IMAGE_GEN_STYLE` is a single constant applied to every illustration beat in a video.
- No image editing/inpainting — Generations endpoint only.
- No cross-video image dedup ledger — cache key is prompt-hash only, scoped to `tmp/image_cache/` for the life of that directory (not per-run, not explicitly cross-video, but also not actively managed/pruned).
- **No Ken Burns motion** (pan/zoom on the static image) — beats are encoded as a static frame with fade-in/fade-out only. This was in the original design but never implemented; revisit if the finished videos feel too static.
