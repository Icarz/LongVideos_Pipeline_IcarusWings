# Image Generation Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Pexels/Pixabay stock video fetching with OpenAI `gpt-image-1` still-image generation, animated via Ken Burns in the compositor.

**Architecture:** `visual_direction.py` is repurposed to write per-beat image prompts (Claude Sonnet). New `image_gen.py` calls OpenAI Image API and saves PNGs. `compositor.py` loads PNGs as still-image sources for Ken Burns. `background.py` is deleted.

**Tech Stack:** Python 3.12, `openai` SDK, `Pillow` (PIL), FFmpeg, Anthropic SDK (existing)

## Global Constraints

- Image model: `gpt-image-1`, size `1024x1024`, quality `low`
- Every image prompt must end with: `cinematic grain, desaturated, dark palette, 16mm film look`
- Male subjects only, shot anonymously (back, silhouette, hands, partial face)
- Caching: skip generation if PNG exists and `> 5 KB`
- Pipeline never halts — solid-color fallback PNG on any API failure
- Working directory: `C:\Users\PC\Desktop\long videos_icarus_wings`
- Run commands from project root with venv active

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `config.py` | Remove Pexels/Pixabay/mood constants; add image gen constants |
| Modify | `modules/visual_direction.py` | Rewrite system prompt + output format for per-beat image prompts |
| Create | `modules/image_gen.py` | Call OpenAI API, cache PNGs, write manifest |
| Modify | `modules/compositor.py` | Read `beat_images` manifest; load PNG as still-image Ken Burns source |
| Delete | `modules/background.py` | Replaced by `image_gen.py` |
| Create | `tests/test_visual_direction.py` | Validate schema + validation logic |
| Create | `tests/test_image_gen.py` | Caching + manifest structure |

---

## Task 1: Config — remove old constants, add image gen constants

**Files:**
- Modify: `config.py`

**Interfaces:**
- Produces: `config.IMAGE_GEN_MODEL`, `config.IMAGE_GEN_SIZE`, `config.IMAGE_GEN_QUALITY`, `config.IMAGE_GEN_DIR`, `config.IMAGE_GEN_DELAY`, `config.IMAGE_GEN_BACKOFFS` — used by Tasks 3 and 4

- [ ] **Step 1: Remove the background fetch block from `config.py`**

Delete these lines entirely (lines ~72–93 in config.py):
```python
# --- Background fetch (stage 4) -------------------------------------------
BACKGROUND_CHAIN = ["pexels", "pixabay", "coverr", "gradient"]
PEXELS_ENABLED = True
PIXABAY_ENABLED = True
COVERR_ENABLED = True
PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_ORIENTATION = "landscape"
PEXELS_VIDEO_PER_PAGE = 15
PEXELS_SIZE = "medium"
PEXELS_TIMEOUT = 60
PEXELS_BACKOFFS = [2, 4, 8, 16]
PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"
COVERR_SEARCH_URL = "https://api.coverr.co/videos"
FOOTAGE_HISTORY_PATH = os.path.join(TMP_DIR, "footage_history.json")
FOOTAGE_HISTORY_MAX = 600
```

Also delete the mood filter block (~lines 88–93):
```python
# --- Mood footage filter (stage 3) ----------------------------------------
MOOD_LUMA_TARGET = 70
MOOD_LUMA_LEGIBILITY_FLOOR = 18
MOOD_COOL_BIAS_WEIGHT = 1.0
MOOD_DARK_WEIGHT = 1.0
```

- [ ] **Step 2: Add image generation block to `config.py`**

Add this block after the `# --- TTS` section (after `ALIGN_MODEL` line):
```python
# --- Image generation (stage 4) -------------------------------------------
IMAGE_GEN_MODEL    = "gpt-image-1"
IMAGE_GEN_SIZE     = "1024x1024"       # cheapest tier: $0.011/image
IMAGE_GEN_QUALITY  = "low"
IMAGE_GEN_DIR      = os.path.join(TMP_DIR, "images")
IMAGE_GEN_DELAY    = 1.0               # seconds between API calls
IMAGE_GEN_BACKOFFS = [5, 10, 20]       # retry delays on 429
```

- [ ] **Step 3: Verify config loads cleanly**

```bash
python -c "import config; print('IMAGE_GEN_MODEL:', config.IMAGE_GEN_MODEL)"
```
Expected output: `IMAGE_GEN_MODEL: gpt-image-1`

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "config: replace Pexels/mood constants with image gen constants"
```

---

## Task 2: Repurpose `visual_direction.py` for per-beat image prompts

**Files:**
- Modify: `modules/visual_direction.py`
- Create: `tests/test_visual_direction.py`

**Interfaces:**
- Consumes: `script_01.json`, `beats_01.json`
- Produces: `visual_direction_01.json` with shape:
  ```json
  {
    "beat_prompts": [
      {
        "beat_idx": 0,
        "image_prompt": "string (40-60 words ending with style anchor)",
        "subject_type": "male_silhouette",
        "light_profile": "string"
      }
    ],
    "bookends": [
      {"open_beat": 0, "close_beat": 62, "note": "string"}
    ]
  }
  ```
- `generate_visual_direction_with_retry(script, beats)` → dict with keys `beat_prompts`, `bookends`

- [ ] **Step 1: Write failing tests**

Create `tests/test_visual_direction.py`:
```python
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.visual_direction import _validate

def _make_prompt(beat_idx, subject_type="male_silhouette"):
    return {
        "beat_idx": beat_idx,
        "image_prompt": (
            "male silhouette standing at window from behind, cold blue-grey backlight, "
            "late afternoon, cinematic grain, desaturated, dark palette, 16mm film look"
        ),
        "subject_type": subject_type,
        "light_profile": "cold, dim",
    }

def _valid_data(n=3):
    return {
        "beat_prompts": [_make_prompt(i) for i in range(n)],
        "bookends": [{"open_beat": 0, "close_beat": n - 1, "note": "test bookend"}],
    }

def test_validate_passes_valid_data():
    _validate(_valid_data(3), total_beats=3)  # no exception

def test_validate_catches_missing_beat():
    data = _valid_data(3)
    data["beat_prompts"].pop(1)  # remove beat 1
    with pytest.raises(ValueError, match="Beats not covered"):
        _validate(data, total_beats=3)

def test_validate_catches_missing_style_anchor():
    data = _valid_data(3)
    data["beat_prompts"][0]["image_prompt"] = "man at window, dark mood"
    with pytest.raises(ValueError, match="style anchor"):
        _validate(data, total_beats=3)

def test_validate_catches_invalid_subject_type():
    data = _valid_data(3)
    data["beat_prompts"][0]["subject_type"] = "robot"
    with pytest.raises(ValueError, match="invalid subject_type"):
        _validate(data, total_beats=3)

def test_validate_catches_missing_bookends():
    data = _valid_data(3)
    data["bookends"] = []
    with pytest.raises(ValueError, match="bookend"):
        _validate(data, total_beats=3)
```

- [ ] **Step 2: Run tests — expect FAIL (function signature mismatch)**

```bash
python -m pytest tests/test_visual_direction.py -v
```
Expected: errors because `_validate` currently has different signature/logic.

- [ ] **Step 3: Rewrite `SYSTEM_PROMPT` in `visual_direction.py`**

Replace the entire `SYSTEM_PROMPT = (...)` block (lines 38–142) with:

```python
STYLE_ANCHOR = "cinematic grain, desaturated, dark palette, 16mm film look"

SYSTEM_PROMPT = (
    "You are the VISUAL DIRECTOR for Icarus Wings, a self-improvement channel. "
    "You take a script and its beat map and produce IMAGE GENERATION PROMPTS — "
    "one per beat — for OpenAI's gpt-image-1 model.\n\n"
    "You MUST respond with ONLY a single valid JSON object — no markdown, no code "
    "fences, no commentary.\n\n"

    "## YOUR JOB\n\n"
    "You are WRITING PAINTING INSTRUCTIONS for an AI image model. Each prompt tells "
    "the model exactly what scene to paint. Think cinematically: lighting direction, "
    "framing, subject, atmosphere.\n\n"

    "## UNIVERSAL RULES\n\n"

    "### 1. CHARACTER\n"
    "- Male subjects only, shot anonymously (back, silhouette, hands, partial face, distance)\n"
    "- FORBIDDEN: women, children, identifiable faces, crowds\n"
    "- Alternate between human-figure and abstract/nature beats\n\n"

    "### 2. LIGHT ARC\n"
    "Every video follows this arc — prompts must match the section's light profile:\n"
    "- hook: cold, dim, dying afternoon — oppressive stillness\n"
    "- false_cause: flat grey fluorescent — analytical, sterile\n"
    "- turn: high-contrast single key light — dramatic revelation\n"
    "- true_cause: warmer but still dim, golden edge light — discovery\n"
    "- re_hook: slight brightness lift, still moody — stakes rising\n"
    "- lever: warm directional light — purposeful, active\n"
    "- close: resolved, gentle warm light — earned calm\n\n"

    "### 3. STYLE ANCHOR\n"
    "Every single prompt MUST end with exactly:\n"
    f"  {STYLE_ANCHOR}\n"
    "This is non-negotiable — it keeps all beats visually coherent.\n\n"

    "### 4. PROMPT STRUCTURE (40-60 words per prompt)\n"
    "Include: subject | framing | light direction + color temp | atmosphere | style anchor\n"
    "GOOD: 'male silhouette standing at window from behind, cold blue-grey backlight, "
    "late afternoon dim interior, shallow depth of field, "
    "cinematic grain, desaturated, dark palette, 16mm film look'\n"
    "BAD: 'moody dark man atmospheric cinematic'\n\n"

    "### 5. SUBJECT TYPES\n"
    "- 'male_silhouette' — backlit/shadowed male figure\n"
    "- 'male_closeup' — hands, partial face, breath, intimate male detail\n"
    "- 'male_landscape' — small male figure in vast environment\n"
    "- 'male_action' — man doing something (walking, sitting, standing)\n"
    "- 'nature' — landscape, sky, water, fog, no human\n"
    "- 'abstract' — texture, ink, smoke, particles, light, no human\n"
    "- 'timelapse' — time passing, light moving, sky cycling\n\n"

    "### 6. BOOKENDS\n"
    "Hook and close beats share a visual motif (same framing, different emotional weight). "
    "Identify the exact beat indices.\n\n"

    "## OUTPUT FORMAT\n\n"
    "{\n"
    '  "beat_prompts": [\n'
    "    {\n"
    '      "beat_idx": 0,\n'
    '      "image_prompt": "male silhouette standing at window from behind, cold blue-grey '
    'backlight, late afternoon dim interior, shallow depth of field, cinematic grain, '
    'desaturated, dark palette, 16mm film look",\n'
    '      "subject_type": "male_silhouette",\n'
    '      "light_profile": "cold blue-grey, backlit, dying afternoon"\n'
    "    }\n"
    "  ],\n"
    '  "bookends": [\n'
    '    {"open_beat": 0, "close_beat": 62, "note": "same window — defeated vs resolved"}\n'
    "  ]\n"
    "}\n\n"
    "REQUIREMENTS:\n"
    "- Every beat index 0 to N-1 must appear exactly once in beat_prompts\n"
    "- beat_prompts sorted by beat_idx ascending\n"
    f"- Every image_prompt ends with '{STYLE_ANCHOR}'\n"
    "- subject_type must be one of the 7 valid types listed above\n"
    "- At least one bookend pair\n"
)
```

- [ ] **Step 4: Rewrite `_format_input()` in `visual_direction.py`**

Replace the function body (lines 169–196):
```python
def _format_input(script: dict, beats: list[dict]) -> str:
    parts = ["## FULL SCRIPT\n"]
    parts.append(f"Title: {script['title']}\n")
    parts.append(f"[HOOK]\n{script['hook']}\n")
    for section in script["sections"]:
        parts.append(f"[{section['role'].upper()}]\n{section['text']}\n")

    parts.append(f"\n## BEAT MAP ({len(beats)} beats)\n")
    for i, beat in enumerate(beats):
        parts.append(
            f"  [{i:3d}] {beat['section_role']:12s} | {beat.get('mood', ''):30s} | \"{beat['line']}\""
        )

    parts.append(f"\n## INSTRUCTIONS")
    parts.append(f"- Total beats: {len(beats)} — write one image_prompt for EVERY beat (0 to {len(beats) - 1})")
    parts.append(f"- Apply the LIGHT ARC per section_role")
    parts.append(f"- All human subjects must be MALE, shot anonymously")
    parts.append(f"- Every prompt MUST end with: {STYLE_ANCHOR}")
    parts.append(f"- Identify at least one hook ↔ close bookend pair")

    return "\n".join(parts)
```

- [ ] **Step 5: Rewrite `_validate()` in `visual_direction.py`**

Replace the entire `_validate` function (lines 199–287):
```python
def _validate(data: dict, total_beats: int) -> None:
    if "beat_prompts" not in data or not isinstance(data["beat_prompts"], list):
        raise ValueError("Missing 'beat_prompts' array")

    valid_subjects = {
        "male_silhouette", "male_closeup", "male_landscape",
        "male_action", "nature", "abstract", "timelapse",
    }

    seen = set()
    for i, bp in enumerate(data["beat_prompts"]):
        for field in ("beat_idx", "image_prompt", "subject_type", "light_profile"):
            if field not in bp:
                raise ValueError(f"beat_prompts[{i}]: missing '{field}'")

        if bp["subject_type"] not in valid_subjects:
            raise ValueError(
                f"beat_prompts[{i}]: invalid subject_type '{bp['subject_type']}'. "
                f"Use: {sorted(valid_subjects)}"
            )

        if len(bp["image_prompt"]) < 30:
            raise ValueError(f"beat_prompts[{i}]: image_prompt too short")

        if STYLE_ANCHOR not in bp["image_prompt"].lower():
            raise ValueError(
                f"beat_prompts[{i}]: missing style anchor. "
                f"Prompt must contain: '{STYLE_ANCHOR}'"
            )

        idx = bp["beat_idx"]
        if idx in seen:
            raise ValueError(f"beat_prompts[{i}]: duplicate beat_idx {idx}")
        seen.add(idx)

    missing = set(range(total_beats)) - seen
    if missing:
        raise ValueError(f"Beats not covered by any prompt: {sorted(missing)}")

    if "bookends" not in data or not isinstance(data["bookends"], list) or len(data["bookends"]) < 1:
        raise ValueError("Need at least one bookend pair in 'bookends'")

    subject_dist = {}
    for bp in data["beat_prompts"]:
        st = bp["subject_type"]
        subject_dist[st] = subject_dist.get(st, 0) + 1

    logger.info(
        "Validation passed | beats=%d | bookends=%d | subjects=%s",
        len(data["beat_prompts"]), len(data["bookends"]),
        " ".join(f"{k}:{v}" for k, v in sorted(subject_dist.items())),
    )
```

- [ ] **Step 6: Update `__main__` block output path and print (lines 337–369)**

Replace the `__main__` block with:
```python
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    script_path = os.path.join(config.TMP_DIR, "script_01.json")
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    beats_path = os.path.join(config.TMP_DIR, "beats_01.json")
    with open(beats_path, "r", encoding="utf-8") as f:
        beats_data = json.load(f)

    result = generate_visual_direction_with_retry(script, beats_data["beats"])

    out_path = os.path.join(config.TMP_DIR, "visual_direction_01.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    prompts = result["beat_prompts"]
    subjects = {}
    for bp in prompts:
        subjects[bp["subject_type"]] = subjects.get(bp["subject_type"], 0) + 1

    print(f"\n=== Visual Direction: {len(prompts)} beat prompts ===")
    print(f"Bookends: {len(result['bookends'])}")
    print(f"Subject types: {json.dumps(subjects, indent=2)}")
    print(f"\nFirst 3 prompts:")
    for bp in prompts[:3]:
        print(f"  [{bp['beat_idx']:3d}] {bp['subject_type']:18s} | {bp['image_prompt'][:70]}...")
    print(f"\nSaved -> {out_path}")
```

- [ ] **Step 7: Also remove unused imports in `visual_direction.py`**

Remove `import re` from the imports (only used if the old regex stripping is there; `_strip_to_json` still uses `re` so keep it if present). Verify no import errors:
```bash
python -c "from modules.visual_direction import generate_visual_direction_with_retry; print('OK')"
```
Expected: `OK`

- [ ] **Step 8: Run tests — expect PASS**

```bash
python -m pytest tests/test_visual_direction.py -v
```
Expected:
```
PASSED tests/test_visual_direction.py::test_validate_passes_valid_data
PASSED tests/test_visual_direction.py::test_validate_catches_missing_beat
PASSED tests/test_visual_direction.py::test_validate_catches_missing_style_anchor
PASSED tests/test_visual_direction.py::test_validate_catches_invalid_subject_type
PASSED tests/test_visual_direction.py::test_validate_catches_missing_bookends
```

- [ ] **Step 9: Commit**

```bash
git add modules/visual_direction.py tests/test_visual_direction.py
git commit -m "visual_direction: repurpose for per-beat image prompts (gpt-image-1)"
```

---

## Task 3: Create `image_gen.py`

**Files:**
- Create: `modules/image_gen.py`
- Create: `tests/test_image_gen.py`

**Interfaces:**
- Consumes: `visual_direction_01.json["beat_prompts"]` (list of dicts with `beat_idx`, `image_prompt`, `subject_type`)
- Produces: `images_manifest_01.json` with shape:
  ```json
  {
    "beat_images": ["tmp/images/beat_000.png", ...],
    "stats": {"total_beats": 65, "generated": 60, "cached": 5}
  }
  ```
- `generate_images(beat_prompts: list[dict]) -> dict` — called by pipeline runner

**Dependencies:** `pip install openai pillow` (add to requirements if present)

- [ ] **Step 1: Install dependencies**

```bash
pip install openai pillow
```
Expected: installs without error (may already be present).

- [ ] **Step 2: Write failing tests**

Create `tests/test_image_gen.py`:
```python
import os, sys, json, base64
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import tempfile

import config

# Patch IMAGE_GEN_DIR to a temp dir for all tests
@pytest.fixture(autouse=True)
def tmp_image_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMAGE_GEN_DIR", str(tmp_path))
    return tmp_path


def _fake_png_b64():
    """1x1 black PNG as base64."""
    import io
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 0)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_generate_one_uses_cache(tmp_image_dir):
    from modules.image_gen import _generate_one
    # Pre-write a fake PNG > 5 KB
    path = os.path.join(str(tmp_image_dir), "beat_000.png")
    Image.new("RGB", (100, 100), (10, 10, 10)).save(path)
    # Pad to > 5 KB
    with open(path, "ab") as f:
        f.write(b"\x00" * 6000)

    client = MagicMock()
    result = _generate_one("any prompt", 0, client)

    assert result == path
    client.images.generate.assert_not_called()  # cache hit — no API call


def test_generate_one_calls_api_when_no_cache(tmp_image_dir):
    from modules.image_gen import _generate_one

    fake_b64 = _fake_png_b64()
    mock_resp = MagicMock()
    mock_resp.data[0].b64_json = fake_b64
    client = MagicMock()
    client.images.generate.return_value = mock_resp

    result = _generate_one("a cinematic prompt", 1, client)

    assert result.endswith("beat_001.png")
    assert os.path.exists(result)
    client.images.generate.assert_called_once()


def test_generate_images_returns_manifest_shape(tmp_image_dir):
    from modules.image_gen import generate_images

    beat_prompts = [
        {"beat_idx": 0, "image_prompt": "prompt 0", "subject_type": "nature"},
        {"beat_idx": 1, "image_prompt": "prompt 1", "subject_type": "abstract"},
    ]
    fake_b64 = _fake_png_b64()
    mock_resp = MagicMock()
    mock_resp.data[0].b64_json = fake_b64

    with patch("modules.image_gen.OpenAI") as MockClient:
        instance = MockClient.return_value
        instance.images.generate.return_value = mock_resp
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            result = generate_images(beat_prompts)

    assert "beat_images" in result
    assert len(result["beat_images"]) == 2
    assert "stats" in result
    assert result["stats"]["total_beats"] == 2
```

- [ ] **Step 3: Run tests — expect FAIL (module not found)**

```bash
python -m pytest tests/test_image_gen.py -v
```
Expected: `ModuleNotFoundError: No module named 'modules.image_gen'`

- [ ] **Step 4: Create `modules/image_gen.py`**

```python
"""Stage 4 — image_gen: generate one PNG per beat via OpenAI gpt-image-1.

Input  : visual_direction_01.json (beat_prompts list)
Output : images_manifest_01.json + tmp/images/beat_NNN.png per beat

Caching: skip if beat_NNN.png exists and > 5 KB.
Fallback: solid-color PNG on API failure — pipeline never halts.
"""

import base64
import json
import logging
import os
import time

from openai import OpenAI
from dotenv import load_dotenv
from PIL import Image

import config

load_dotenv()

logger = logging.getLogger(__name__)
os.makedirs(config.IMAGE_GEN_DIR, exist_ok=True)


def _fallback_png(beat_idx: int) -> str:
    path = os.path.join(config.IMAGE_GEN_DIR, f"beat_{beat_idx:03d}.png")
    r, g, b = config.VIDEO_BG_COLOR
    Image.new("RGB", (1024, 1024), (r, g, b)).save(path)
    logger.warning("  fallback PNG written: beat_%03d.png", beat_idx)
    return path


def _generate_one(prompt: str, beat_idx: int, client: OpenAI) -> str:
    path = os.path.join(config.IMAGE_GEN_DIR, f"beat_{beat_idx:03d}.png")

    if os.path.exists(path) and os.path.getsize(path) > 5_000:
        logger.info("  cached: beat_%03d.png", beat_idx)
        return path

    backoffs = config.IMAGE_GEN_BACKOFFS + [None]
    for attempt, backoff in enumerate(backoffs):
        try:
            resp = client.images.generate(
                model=config.IMAGE_GEN_MODEL,
                prompt=prompt,
                size=config.IMAGE_GEN_SIZE,
                quality=config.IMAGE_GEN_QUALITY,
                n=1,
                response_format="b64_json",
            )
            img_bytes = base64.b64decode(resp.data[0].b64_json)
            with open(path, "wb") as f:
                f.write(img_bytes)
            logger.info("  generated: beat_%03d.png (%.1f KB)", beat_idx, len(img_bytes) / 1024)
            return path
        except Exception as exc:
            if backoff is None:
                logger.error("  beat %d failed after %d attempts: %s", beat_idx, attempt, exc)
                return _fallback_png(beat_idx)
            logger.warning("  attempt %d failed (%s) — backoff %ds", attempt + 1, exc, backoff)
            time.sleep(backoff)

    return _fallback_png(beat_idx)


def generate_images(beat_prompts: list[dict]) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set — check your .env file")

    client = OpenAI(api_key=api_key)
    beat_images: list[str] = []
    generated = cached = 0

    logger.info("Generating images for %d beats...", len(beat_prompts))

    for i, bp in enumerate(beat_prompts):
        idx = bp["beat_idx"]
        cached_path = os.path.join(config.IMAGE_GEN_DIR, f"beat_{idx:03d}.png")
        was_cached = os.path.exists(cached_path) and os.path.getsize(cached_path) > 5_000

        logger.info(
            "[%d/%d] beat_%03d | %s", i + 1, len(beat_prompts), idx, bp["subject_type"]
        )

        path = _generate_one(bp["image_prompt"], idx, client)
        beat_images.append(path)

        if was_cached:
            cached += 1
        else:
            generated += 1
            time.sleep(config.IMAGE_GEN_DELAY)

    logger.info("Done | generated=%d | cached=%d", generated, cached)
    return {
        "beat_images": beat_images,
        "stats": {"total_beats": len(beat_prompts), "generated": generated, "cached": cached},
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    vd_path = os.path.join(config.TMP_DIR, "visual_direction_01.json")
    with open(vd_path, encoding="utf-8") as f:
        vd = json.load(f)

    result = generate_images(vd["beat_prompts"])

    manifest_path = os.path.join(config.TMP_DIR, "images_manifest_01.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n=== Image generation: {result['stats']['total_beats']} beats ===")
    print(f"Generated: {result['stats']['generated']}")
    print(f"Cached:    {result['stats']['cached']}")
    print(f"Manifest -> {manifest_path}")
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
python -m pytest tests/test_image_gen.py -v
```
Expected:
```
PASSED tests/test_image_gen.py::test_generate_one_uses_cache
PASSED tests/test_image_gen.py::test_generate_one_calls_api_when_no_cache
PASSED tests/test_image_gen.py::test_generate_images_returns_manifest_shape
```

- [ ] **Step 6: Commit**

```bash
git add modules/image_gen.py tests/test_image_gen.py
git commit -m "feat: add image_gen.py — per-beat OpenAI image generation with caching"
```

---

## Task 4: Update `compositor.py` — read PNG manifest, still-image Ken Burns

**Files:**
- Modify: `modules/compositor.py` (3 targeted changes)

**Interfaces:**
- Consumes: `images_manifest_01.json["beat_images"]` (list of PNG paths)
- All downstream (concat, text, audio) unchanged

- [ ] **Step 1: Update `_encode_beat_clip` — replace video loop logic with still-image input**

In `_encode_beat_clip` (around line 247), replace these lines:
```python
    src_dur = _probe_duration(clip_path)
    if src_dur < dur:
        loops   = math.ceil(dur / src_dur) + 1
        in_args = ["-stream_loop", str(loops), "-i", clip_path]
    else:
        in_args = ["-i", clip_path]
```

With:
```python
    in_args = ["-loop", "1", "-i", clip_path]
```

`-loop 1` tells FFmpeg to loop the still image indefinitely; `-t dur` in the final command caps the output length. No duration probe needed.

- [ ] **Step 2: Remove unused `import math`**

In `compositor.py`, remove the line:
```python
import math
```

Verify no remaining `math.` references:
```bash
python -c "import modules.compositor; print('OK')"
```
Expected: `OK` (no ImportError or NameError)

- [ ] **Step 3: Update `compose_video` — read `beat_images` from new manifest**

In `compose_video` (around line 534), make three changes:

Change the default manifest path (line ~544):
```python
# OLD:
manifest_path = manifest_path or os.path.join(config.TMP_DIR, "clips_manifest_01.json")
# NEW:
manifest_path = manifest_path or os.path.join(config.TMP_DIR, "images_manifest_01.json")
```

Change the manifest key (line ~552):
```python
# OLD:
beat_clips = manifest["beat_clips"]
# NEW:
beat_clips = manifest["beat_images"]
```

Update the error message (line ~556):
```python
# OLD:
raise ValueError("Clip/beat count mismatch: %d clips vs %d beats" % (len(beat_clips), len(beats)))
# NEW:
raise ValueError("Image/beat count mismatch: %d images vs %d beats" % (len(beat_clips), len(beats)))
```

- [ ] **Step 4: Update the file header comment**

At the top of `compositor.py`, update the input comment (line ~4):
```python
# OLD:
#          tmp/clips_manifest_01.json
# NEW:
#          tmp/images_manifest_01.json
```

- [ ] **Step 5: Verify compositor imports cleanly**

```bash
python -c "from modules.compositor import compose_video; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add modules/compositor.py
git commit -m "compositor: read PNG images_manifest; still-image Ken Burns via -loop 1"
```

---

## Task 5: Delete `background.py` and clean up

**Files:**
- Delete: `modules/background.py`
- Delete: `tmp/footage_history.json` (if exists)

- [ ] **Step 1: Confirm nothing imports `background`**

```bash
python -c "
import ast, os
for root, _, files in os.walk('.'):
    if 'venv' in root or '.git' in root:
        continue
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            src = open(path).read()
            if 'background' in src and 'background.py' not in path:
                print(path, '— references background')
"
```
Expected: no output (nothing imports `background`). If any file does, update that import first.

- [ ] **Step 2: Delete `background.py`**

```bash
git rm modules/background.py
```

- [ ] **Step 3: Delete footage history ledger**

```bash
python -c "
import os
p = 'tmp/footage_history.json'
if os.path.exists(p):
    os.remove(p)
    print('deleted', p)
else:
    print('not found — skip')
"
```

- [ ] **Step 4: Run all tests — confirm nothing broke**

```bash
python -m pytest tests/ -v
```
Expected: all 8 tests pass.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "cleanup: delete background.py and footage_history.json"
```

---

## Post-Implementation Smoke Test

After all tasks, run a manual end-to-end test of just the two new stages:

```bash
# 1. Verify visual_direction generates prompts (uses real Claude API — ~$0.14)
python modules/visual_direction.py
# Expected: tmp/visual_direction_01.json written, 40-65 beat_prompts

# 2. Verify image_gen generates images for first 3 beats only (edit __main__ temporarily)
# Expected: tmp/images/beat_000.png ... beat_002.png written, ~$0.03

# 3. Verify compositor accepts the PNG manifest
python modules/compositor.py
# Expected: output/video_01.mp4 written
```
