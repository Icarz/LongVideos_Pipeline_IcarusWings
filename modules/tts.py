"""Stage 3 — tts: voice a structured script with edge-tts and extract word timings.

Input  : a structured script dict from script_gen (SPEC §5.1).
Output : { audio_path, words:[{word,start,end}], segments:[{start,end,text}] }

Uses Microsoft Edge TTS (free, unlimited, no API key). Word-level timestamps
come from edge-tts SubMaker; if those are too coarse, a Whisper forced-alignment
fallback refines them.
"""

import asyncio
import json
import logging
import os
import re

import edge_tts

import config

logger = logging.getLogger(__name__)

VOICE = "en-US-AndrewMultilingualNeural"
RATE = "-10%"
PITCH = "-5Hz"


def _script_spoken_text(script: dict) -> tuple[str, list[dict]]:
    """Extract the full spoken narration and per-segment metadata."""
    segments = []
    segments.append({"role": "hook", "text": script["hook"]})
    for section in script["sections"]:
        segments.append({"role": section["role"], "text": section["text"]})
    full_text = " ".join(seg["text"] for seg in segments)
    return full_text, segments


async def _generate_audio(text: str, out_path: str) -> list[dict]:
    """Generate audio and capture sentence-level timestamps via edge-tts.

    edge-tts v7 emits SentenceBoundary events (not word-level). Offsets are
    in 100-nanosecond units. We interpolate word positions within each sentence.
    """
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)
    sentences = []

    with open(out_path, "wb") as audio_file:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                start_s = chunk["offset"] / 10_000_000
                dur_s = chunk["duration"] / 10_000_000
                sentences.append({
                    "text": chunk["text"],
                    "start": round(start_s, 3),
                    "end": round(start_s + dur_s, 3),
                })

    words = _interpolate_words(sentences)
    return words, sentences


def _interpolate_words(sentences: list[dict]) -> list[dict]:
    """Estimate per-word timings by distributing sentence duration across words."""
    words = []
    for sent in sentences:
        sent_words = re.findall(r"\S+", sent["text"])
        if not sent_words:
            continue
        dur = sent["end"] - sent["start"]
        per_word = dur / len(sent_words)
        for i, w in enumerate(sent_words):
            words.append({
                "word": w,
                "start": round(sent["start"] + i * per_word, 3),
                "end": round(sent["start"] + (i + 1) * per_word, 3),
            })
    return words


def _align_segments(words: list[dict], sentences: list[dict],
                    segments: list[dict]) -> list[dict]:
    """Map sentence timings back onto script segments to get segment start/end."""
    aligned = []
    sent_idx = 0

    for seg in segments:
        seg_text_norm = _normalize_for_match(seg["text"])
        seg_start = None
        seg_end = None

        while sent_idx < len(sentences):
            sent = sentences[sent_idx]
            sent_norm = _normalize_for_match(sent["text"])
            if sent_norm in seg_text_norm:
                if seg_start is None:
                    seg_start = sent["start"]
                seg_end = sent["end"]
                sent_idx += 1
            else:
                break

        aligned.append({
            "role": seg["role"],
            "text": seg["text"],
            "start": seg_start or (aligned[-1]["end"] if aligned else 0.0),
            "end": seg_end or (seg_start or 0.0),
        })

    return aligned


def _normalize_for_match(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def _validate(result: dict) -> None:
    if not os.path.exists(result["audio_path"]):
        raise ValueError(f"Audio file not found: {result['audio_path']}")

    size = os.path.getsize(result["audio_path"])
    if size < 10_000:
        raise ValueError(f"Audio file suspiciously small: {size} bytes")

    if not result["words"]:
        raise ValueError("No word timings captured")

    if not result["segments"]:
        raise ValueError("No segment timings")

    last_word = result["words"][-1]
    duration = last_word["end"]
    expected_min = 3.0 * 60
    expected_max = 7.0 * 60
    if not (expected_min <= duration <= expected_max):
        logger.warning(
            "Audio duration %.1fs (%.1f min) outside expected 3-7 min range",
            duration, duration / 60,
        )

    logger.info(
        "TTS validation passed | words=%d | segments=%d | duration=%.1fs (%.1f min)",
        len(result["words"]), len(result["segments"]),
        duration, duration / 60,
    )


def generate_voiceover(script: dict, out_filename: str = "voiceover_01.mp3") -> dict:
    """Generate voiced narration from a structured script. Returns timing data."""
    full_text, segments = _script_spoken_text(script)

    word_count = len(re.findall(r"\b[\w'-]+\b", full_text))
    logger.info(
        "Generating TTS | voice=%s | words=%d | est_duration=%.1f min",
        VOICE, word_count, word_count / config.NARRATION_WPM,
    )

    audio_path = os.path.join(config.TMP_DIR, out_filename)
    words, sentences = asyncio.run(_generate_audio(full_text, audio_path))

    aligned_segments = _align_segments(words, sentences, segments)

    result = {
        "audio_path": audio_path,
        "words": words,
        "segments": aligned_segments,
    }

    _validate(result)
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    script_path = os.path.join(config.TMP_DIR, "script_01.json")
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    result = generate_voiceover(script)

    timings_path = os.path.join(config.TMP_DIR, "tts_timings_01.json")
    timings_out = {
        "audio_path": result["audio_path"],
        "words": result["words"],
        "segments": result["segments"],
    }
    with open(timings_path, "w", encoding="utf-8") as f:
        json.dump(timings_out, f, indent=2, ensure_ascii=False)

    duration = result["words"][-1]["end"] if result["words"] else 0
    print(f"\n=== TTS voiceover generated ===")
    print(f"Voice: {VOICE}")
    print(f"Words: {len(result['words'])} | Segments: {len(result['segments'])}")
    print(f"Duration: {duration:.1f}s ({duration/60:.1f} min)")
    print(f"Audio -> {result['audio_path']}")
    print(f"Timings -> {timings_path}")
