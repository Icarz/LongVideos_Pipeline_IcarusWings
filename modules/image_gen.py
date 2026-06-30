"""Stage 4 — image_gen: generate one PNG per illustration beat via OpenAI gpt-image-1.

text_card beats are skipped (None in manifest) — the compositor renders them
directly from card_text using Pillow.

Input  : beat list from beat_plan (each beat has beat_type + image_prompt/card_text)
Output : { "beat_images": [path_or_null, ...], "stats": {...} }
         Saved to tmp/clips_manifest_01.json for compositor.
"""

import base64
import hashlib
import json
import logging
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

import config

load_dotenv()

logger = logging.getLogger(__name__)

os.makedirs(config.IMAGE_GEN_CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _cached_path(prompt: str) -> str:
    return os.path.join(config.IMAGE_GEN_CACHE_DIR, f"{_prompt_hash(prompt)}.png")


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def _build_prompt(image_prompt: str) -> str:
    return f"{config.IMAGE_GEN_STYLE}, {image_prompt}"


def _generate_image(prompt: str, client: OpenAI) -> str:
    """Call gpt-image-1, save PNG, return local path. Raises on API error."""
    cache_path = _cached_path(prompt)
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
        logger.info("  cache hit: %s", os.path.basename(cache_path))
        return cache_path

    response = client.images.generate(
        model=config.IMAGE_GEN_MODEL,
        prompt=prompt,
        size=config.IMAGE_GEN_SIZE,
        quality=config.IMAGE_GEN_QUALITY,
        n=1,
    )

    b64 = response.data[0].b64_json
    if not b64:
        raise RuntimeError("API returned empty image data")

    with open(cache_path, "wb") as f:
        f.write(base64.b64decode(b64))

    logger.info("  generated: %s", os.path.basename(cache_path))
    return cache_path


# ---------------------------------------------------------------------------
# Fallback: blank cream PNG
# ---------------------------------------------------------------------------

def _cream_fallback(beat_idx: int) -> str:
    """Generate a plain cream PNG when the API fails."""
    path = os.path.join(config.IMAGE_GEN_CACHE_DIR, f"fallback_{beat_idx:03d}.png")
    if os.path.exists(path):
        return path
    try:
        from PIL import Image
        r, g, b = config.VIDEO_BG_COLOR
        img = Image.new("RGB", (1536, 1024), (r, g, b))
        img.save(path)
        logger.info("  cream fallback PNG: beat %d", beat_idx)
    except Exception as exc:
        logger.error("  fallback PNG failed: %s", exc)
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_images(beats: list[dict]) -> dict:
    """Generate images for all illustration beats; None for text_card beats.

    Returns:
        {
            "beat_images": [path_or_null, ...],   # one entry per beat
            "stats": { "generated": N, "cached": N, "skipped": N, "failed": N }
        }
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (check your .env file)")

    client = OpenAI(api_key=api_key)

    beat_images: list[str | None] = []
    stats = {"generated": 0, "cached": 0, "skipped": 0, "failed": 0}

    illustration_beats = sum(
        1 for b in beats if b.get("beat_type") == "illustration"
    )
    logger.info(
        "Image gen start | beats=%d | illustrations=%d | text_cards=%d",
        len(beats), illustration_beats, len(beats) - illustration_beats,
    )

    for i, beat in enumerate(beats):
        beat_type = beat.get("beat_type")

        if beat_type == "text_card":
            beat_images.append(None)
            stats["skipped"] += 1
            logger.debug("[%d/%d] text_card — skipped", i + 1, len(beats))
            continue

        # illustration beat
        image_prompt = beat.get("image_prompt", "").strip()
        if not image_prompt:
            logger.warning("[%d/%d] illustration beat missing image_prompt — fallback", i + 1, len(beats))
            beat_images.append(_cream_fallback(i))
            stats["failed"] += 1
            continue

        full_prompt = _build_prompt(image_prompt)
        was_cached = os.path.exists(_cached_path(full_prompt))

        logger.info(
            "[%d/%d] illustration | %s",
            i + 1, len(beats), image_prompt[:60],
        )

        try:
            path = _generate_image(full_prompt, client)
            beat_images.append(path)
            if was_cached:
                stats["cached"] += 1
            else:
                stats["generated"] += 1
                # Rate limit: ~5 req/min on standard tier
                time.sleep(12)
        except Exception as exc:
            logger.error("[%d/%d] image gen failed: %s — cream fallback", i + 1, len(beats), exc)
            beat_images.append(_cream_fallback(i))
            stats["failed"] += 1

    logger.info(
        "Image gen complete | generated=%d | cached=%d | skipped=%d | failed=%d",
        stats["generated"], stats["cached"], stats["skipped"], stats["failed"],
    )

    return {"beat_images": beat_images, "stats": stats}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    beats_path = os.path.join(config.TMP_DIR, "beats_01.json")
    with open(beats_path, "r", encoding="utf-8") as f:
        beats_data = json.load(f)

    result = generate_images(beats_data["beats"])

    manifest_path = os.path.join(config.TMP_DIR, "clips_manifest_01.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n=== Image gen: {len(result['beat_images'])} beats ===")
    print(f"Stats: {json.dumps(result['stats'], indent=2)}")
    print(f"Manifest -> {manifest_path}")
