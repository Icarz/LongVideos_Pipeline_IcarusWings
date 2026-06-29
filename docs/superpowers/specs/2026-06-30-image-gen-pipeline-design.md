# Image Generation Pipeline — Design Spec
**Date:** 2026-06-30  
**Status:** Approved  
**Replaces:** Pexels/Pixabay stock footage fetching (`background.py`)

---

## 1. Summary

Replace stock video fetching (Pexels → Pixabay → gradient fallback) with AI-generated still images via OpenAI's `gpt-image-1` model. Each beat gets one generated PNG, animated in the compositor with Ken Burns pan+zoom to create motion. Visual direction (Claude Sonnet) is repurposed to write per-beat image prompts instead of stock search queries.

---

## 2. Pipeline After Change

```
script_gen.py        →  script_01.json
beat_plan.py         →  beats_01.json
tts.py               →  audio segments
visual_direction.py  →  visual_direction_01.json   (per-beat image prompts)
image_gen.py         →  images_manifest_01.json    (PNGs in tmp/images/)
compositor.py        →  final video
```

Stages untouched: `script_gen.py`, `beat_plan.py`, `tts.py`.

---

## 3. `visual_direction.py` — Repurposed

### Job
Reads full script + all beats, calls Claude Sonnet, outputs one rich image generation prompt per beat. Holistic view of the whole video is required here — prompts must follow the light arc, plan bookends, and maintain visual coherence across 65 beats.

### What changes
- System prompt rewritten: "write image generation prompts" not "write stock search queries"
- Output format: flat per-beat array instead of scene-grouped structure
- Each `image_prompt` is 40–60 words — painting instructions, not search terms
- Every prompt ends with a **style anchor**: `cinematic grain, desaturated, dark palette, 16mm film look` — enforces visual consistency across all beats

### What stays the same
- Claude Sonnet model (cost-effective, sufficient for prompt authorship)
- Light arc applied per section role: hook → cold/dim, lever/close → warmer/resolved
- Character rules: male subjects only, shot anonymously (back, silhouette, hands, partial face)
- Subject types: `male_silhouette`, `male_closeup`, `male_landscape`, `male_action`, `nature`, `abstract`, `timelapse`
- Bookend planning: hook ↔ close share a visual motif (same framing, different emotional weight)
- Retry logic (3 attempts with validation)

### Output format (`visual_direction_01.json`)
```json
{
  "beat_prompts": [
    {
      "beat_idx": 0,
      "image_prompt": "Male silhouette standing at window, shot from behind, cold blue-grey light, late afternoon, dim interior, cinematic grain, desaturated, dark palette, 16mm film look",
      "subject_type": "male_silhouette",
      "light_profile": "cold, dim, dying afternoon"
    }
  ],
  "bookends": [
    {
      "open_beat": 0,
      "close_beat": 62,
      "note": "same window framing — defeated posture vs resolved"
    }
  ]
}
```

### Validation rules (unchanged logic, updated fields)
- Every beat index must appear exactly once in `beat_prompts`
- `subject_type` must be one of the 7 valid types
- `image_prompt` must be at least 20 characters
- At least one bookend pair required
- Style anchor must be present in every prompt (validator checks that prompt length > 30 chars and subject_type is set — style consistency is enforced by the system prompt, not regex)

---

## 4. `image_gen.py` — New Module

### Job
Read per-beat prompts from `visual_direction_01.json`, call OpenAI Image API, save PNGs to `tmp/images/`, write manifest.

### API call
```python
openai.images.generate(
    model="gpt-image-1",
    prompt=beat["image_prompt"],
    size="1024x1024",
    quality="low",
    n=1,
    response_format="b64_json"
)
```

### Caching
If `tmp/images/beat_NNN.png` exists and is `> 5 KB`, skip generation. Safe to re-run mid-batch after failure without burning credits.

### Rate limiting & retry
- `IMAGE_GEN_DELAY = 1.0` seconds between calls (avoids hammering the API)
- On 429: exponential backoff using `IMAGE_GEN_BACKOFFS = [5, 10, 20]`
- On other HTTP errors: log warning, write a solid-color fallback PNG (matches `VIDEO_BG_COLOR`) so the pipeline never halts

### Output manifest (`images_manifest_01.json`)
```json
{
  "beat_images": [
    "tmp/images/beat_000.png",
    "tmp/images/beat_001.png"
  ],
  "stats": {
    "total_beats": 65,
    "generated": 60,
    "cached": 5,
    "fallback": 0
  }
}
```

### Cost per video
| Beats | Cost at $0.011/image |
|-------|---------------------|
| 40    | ~$0.44              |
| 65    | ~$0.72              |

---

## 5. `compositor.py` — Changes

### What changes
- Reads `images_manifest_01.json` → `beat_images` instead of `clips_manifest_01.json` → `beat_clips`
- Background frame source: `PIL.Image.open(png_path)` instead of mp4 video decode
- Ken Burns applied to static image per beat:
  1. Upscale PNG to 1920×1920 (over-scale for pan room)
  2. Crop 1920×1080 window
  3. Animate zoom `1.0x → 1.10x` + pan drift up to 40px over beat duration
  4. Output N frames at 30fps

### What stays the same
- `BG_KENBURNS_ZOOM = 1.10` and `BG_KENBURNS_PAN = 40` config constants — no change
- Crossfades between beats (0.8s default, hard cut at `turn` section)
- Overlay opacity, captions, audio mixing, color grade, watermark — unchanged

### What gets deleted
- mp4 clip loading/decoding logic

---

## 6. `config.py` — Changes

### Add
```python
IMAGE_GEN_MODEL    = "gpt-image-1"
IMAGE_GEN_SIZE     = "1024x1024"
IMAGE_GEN_QUALITY  = "low"
IMAGE_GEN_DIR      = os.path.join(TMP_DIR, "images")
IMAGE_GEN_DELAY    = 1.0
IMAGE_GEN_BACKOFFS = [5, 10, 20]
```

### Remove
- `BACKGROUND_CHAIN`, `PEXELS_*`, `PIXABAY_*`, `COVERR_*`
- `FOOTAGE_HISTORY_PATH`, `FOOTAGE_HISTORY_MAX`
- `MOOD_LUMA_*`, `MOOD_COOL_BIAS_WEIGHT`, `MOOD_DARK_WEIGHT`

---

## 7. Deletions

| File / Dir | Action |
|------------|--------|
| `modules/background.py` | Delete |
| `tmp/footage_history.json` | Delete |
| `tmp/clips/` | Clear after confirming new pipeline works |

`.env` requires `OPENAI_API_KEY`. Pexels/Pixabay keys can remain (unused).

---

## 8. Out of Scope

- No video generation (Sora, Runway, etc.) — images only
- No per-beat style variation — style anchor is constant across all beats
- No image editing/inpainting — Generations endpoint only
- No cross-video image dedup ledger — each video generates fresh (caching is within-run only)
