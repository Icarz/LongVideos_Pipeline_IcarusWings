# Stage 5 - compositor: assemble final MP4 from beats, images, TTS, and text overlays.
#
# Input  : tmp/script_01.json, tmp/beats_01.json, tmp/tts_timings_01.json,
#          tmp/clips_manifest_01.json  (beat_images: [path_or_null, ...])
# Output : output/video_01.mp4
#
# Beat types:
#   illustration — PNG centered on cream canvas, static hold, fade in/out
#   text_card    — Pillow renders card_text on cream canvas, encoded to clip
#
# Steps:
#   1. Map beat timings via TTS segment boundaries
#   2. For each beat: encode illustration or text_card clip
#   3. Concat all clips
#   4. Burn in beat captions (illustration beats only, dark text)
#   5. Add brand watermark
#   6. Mix music bed at -18 dB under voiceover
#   7. Write final MP4 via FFmpeg

import json
import logging
import os
import re
import subprocess
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont

import config

logger = logging.getLogger(__name__)

COMP_DIR = os.path.join(config.TMP_DIR, "comp")

_WINDOWS_FONTS = {
    "Arial Bold": "C:/Windows/Fonts/arialbd.ttf",
    "Arial":      "C:/Windows/Fonts/arial.ttf",
    "Georgia":    "C:/Windows/Fonts/georgia.ttf",
    "Times":      "C:/Windows/Fonts/times.ttf",
}

# Section card labels for tip roles (used if overlay cards are needed)
_CARD_LABELS = {
    "intro":  "INTRO",
    "tip_1":  "TIP 1",
    "tip_2":  "TIP 2",
    "tip_3":  "TIP 3",
    "tip_4":  "TIP 4",
    "tip_5":  "TIP 5",
    "tip_6":  "TIP 6",
    "outro":  "YOUR CHEAT SHEET",
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _ffmpeg(*args, timeout=600):
    cmd = ["ffmpeg", "-y"] + [str(a) for a in args]
    logger.debug("ffmpeg %s", " ".join(cmd[2:]))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg exit %d:\n%s" % (proc.returncode, proc.stderr[-3000:]))


def _probe_duration(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        for s in json.loads(proc.stdout).get("streams", []):
            d = float(s.get("duration", 0) or 0)
            if d > 0:
                return d
    except (json.JSONDecodeError, ValueError):
        pass
    return 10.0


def _find_font(family):
    if family.endswith(".ttf") and os.path.exists(family):
        return family.replace("\\", "/")
    path = _WINDOWS_FONTS.get(family)
    if path and os.path.exists(path):
        return path
    # Fallback to Arial if requested font missing
    fallback = _WINDOWS_FONTS.get("Arial")
    if fallback and os.path.exists(fallback):
        logger.warning("Font %r not found — falling back to Arial", family)
        return fallback
    raise RuntimeError("Font not found: %r. Set CARD_FONT_FAMILY to a .ttf path." % family)


def _dt_text(s):
    s = re.sub(r"[''‚‛']", "", s)
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace(",", "\\,")
    return s


def _dt_path(p):
    return p.replace("\\", "/").replace("'", "\\'").replace(":", "\\:")


def _dt(font, size, color, text, x, y, enable=None, border=0, box=False,
        boxcolor=None, boxborderw=0):
    parts = [
        "drawtext=fontfile='%s'" % _dt_path(font),
        "fontsize=%d" % size,
        "fontcolor=%s" % color,
        "text='%s'" % _dt_text(text),
        "x=%s" % x,
        "y=%s" % y,
    ]
    if border > 0:
        parts += ["bordercolor=white", "borderw=%d" % border]
    if box:
        parts.append("box=1")
        parts.append("boxcolor=%s" % (boxcolor or "white@0.6"))
        if boxborderw > 0:
            parts.append("boxborderw=%d" % boxborderw)
    if enable:
        parts.append("enable='between(t,%s)'" % enable)
    return ":".join(parts)


def _wrap_lines(text, max_chars=44):
    words = text.split()
    lines, current, length = [], [], 0
    for word in words:
        wl = len(word)
        if current and length + 1 + wl > max_chars:
            lines.append(" ".join(current))
            current, length = [word], wl
        else:
            length += (1 if current else 0) + wl
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _make_cream_clip(dur, out_path):
    """Solid cream background clip — fallback when image encoding fails."""
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    r, g, b = config.VIDEO_BG_COLOR
    color_hex = "#%02x%02x%02x" % (r, g, b)
    _ffmpeg(
        "-f", "lavfi",
        "-i", "color=c=%s:s=%dx%d:r=%d" % (color_hex, W, H, config.VIDEO_FPS),
        "-t", str(dur),
        "-c:v", config.VIDEO_CODEC,
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        out_path,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Step 1: Map beat timings via TTS segment boundaries
# ---------------------------------------------------------------------------

def _map_beat_timings(beats, tts_segments, tts_words):
    seg_by_role = {}
    for seg in tts_segments:
        role = seg.get("role")
        if role and seg.get("start") is not None and seg.get("end") is not None:
            seg_by_role[role] = seg

    audio_end = tts_words[-1]["end"] if tts_words else 300.0

    beats_by_role = defaultdict(list)
    for i, beat in enumerate(beats):
        beats_by_role[beat["section_role"]].append((i, beat))

    timed = [None] * len(beats)

    for role, role_beats in beats_by_role.items():
        seg = seg_by_role.get(role)
        if seg:
            seg_start = float(seg["start"])
            seg_end   = float(seg["end"])
        else:
            logger.warning("No TTS segment for role=%r -- using fallback", role)
            seg_start, seg_end = 0.0, audio_end

        word_counts = [max(len(beat["line"].split()), 1) for _, beat in role_beats]
        total_words = sum(word_counts)
        seg_dur     = max(seg_end - seg_start, 0.1)

        raw_durs = [seg_dur * (wc / total_words) for wc in word_counts]

        min_s   = float(config.BEAT_MIN_SECONDS)
        max_s   = float(config.BEAT_MAX_SECONDS)
        clamped = [max(min_s, min(max_s, d)) for d in raw_durs]

        total_clamped = sum(clamped)
        if total_clamped > 0:
            scale   = seg_dur / total_clamped
            clamped = [d * scale for d in clamped]

        t = seg_start
        for (orig_idx, beat), dur in zip(role_beats, clamped):
            timed[orig_idx] = dict(beat, start=round(t, 3), end=round(t + dur, 3))
            t += dur

    last_end = 0.0
    for i in range(len(timed)):
        if timed[i] is None:
            wc  = max(len(beats[i]["line"].split()), 1)
            dur = max(config.BEAT_MIN_SECONDS, min(config.BEAT_MAX_SECONDS, wc / 2.5))
            timed[i] = dict(beats[i], start=last_end, end=round(last_end + dur, 3))
        last_end = timed[i]["end"]

    timed[-1]["end"] = audio_end
    return timed


# ---------------------------------------------------------------------------
# Step 2a: Render text_card PNG via Pillow
# ---------------------------------------------------------------------------

def _render_text_card_png(card_text: str, beat_idx: int) -> str:
    """Render card_text centered on cream canvas. Returns PNG path."""
    os.makedirs(COMP_DIR, exist_ok=True)
    out_png = os.path.join(COMP_DIR, "card_%03d.png" % beat_idx)
    if os.path.exists(out_png):
        return out_png

    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    r, g, b = config.VIDEO_BG_COLOR
    img  = Image.new("RGB", (W, H), (r, g, b))
    draw = ImageDraw.Draw(img)

    tr, tg, tb = config.TEXT_COLOR_DARK

    # Try serif font for title cards, fall back gracefully
    font_path = _WINDOWS_FONTS.get(config.CARD_FONT_FAMILY)
    font_size = config.CARD_FONT_SIZE

    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        try:
            font = ImageFont.truetype(_WINDOWS_FONTS.get("Arial Bold", ""), font_size)
        except Exception:
            font = ImageFont.load_default()

    lines = card_text.split("\n")
    line_spacing = int(font_size * 1.35)
    total_h = len(lines) * line_spacing - (line_spacing - font_size)

    y = (H - total_h) // 2
    for line in lines:
        bbox     = draw.textbbox((0, 0), line, font=font)
        text_w   = bbox[2] - bbox[0]
        x        = (W - text_w) // 2
        draw.text((x, y), line, font=font, fill=(tr, tg, tb))
        y += line_spacing

    img.save(out_png)
    return out_png


# ---------------------------------------------------------------------------
# Step 2b: Encode illustration PNG → video clip (static, fade in/out)
# ---------------------------------------------------------------------------

def _encode_illustration_clip(png_path: str, dur: float, beat_idx: int,
                               role: str, out: str) -> None:
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    r, g, b = config.VIDEO_BG_COLOR
    bg_hex = "#%02x%02x%02x" % (r, g, b)

    fade_dur = min(0.35, dur * 0.12)
    fade_out = "fade=t=out:st=%.3f:d=%.3f:color=%s" % (
        max(0.0, dur - fade_dur), fade_dur, bg_hex[1:])

    if role == config.BG_HARD_CUT_ROLE:
        fades = fade_out
    else:
        fade_in = "fade=t=in:st=0:d=%.3f:color=%s" % (fade_dur, bg_hex[1:])
        fades   = "%s,%s" % (fade_in, fade_out)

    # Scale to fit 1920x1080 preserving aspect ratio; pad remainder with cream
    vf = (
        "scale=%d:%d:force_original_aspect_ratio=decrease," % (W, H) +
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s," % (W, H, bg_hex[1:]) +
        "fps=%d," % config.VIDEO_FPS +
        fades
    )

    _ffmpeg(
        "-loop", "1",
        "-i", png_path,
        "-t", str(dur),
        "-vf", vf,
        "-an",
        "-c:v", config.VIDEO_CODEC,
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        out,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Step 2c: Dispatch per beat
# ---------------------------------------------------------------------------

def _prepare_beat_clip(beat: dict, image_path: "str | None",
                       beat_duration: float, beat_idx: int) -> str:
    os.makedirs(COMP_DIR, exist_ok=True)
    out = os.path.join(COMP_DIR, "beat_%03d.mp4" % beat_idx)
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        return out  # resume-safe

    dur  = max(beat_duration, 0.5)
    role = beat.get("section_role", "")

    try:
        if beat.get("beat_type") == "text_card":
            card_text = beat.get("card_text", "")
            png = _render_text_card_png(card_text, beat_idx)
            _encode_illustration_clip(png, dur, beat_idx, role, out)
        else:
            # illustration
            if image_path and os.path.exists(image_path):
                _encode_illustration_clip(image_path, dur, beat_idx, role, out)
            else:
                logger.warning("Beat %d: no image path — cream fallback", beat_idx)
                _make_cream_clip(dur, out)
    except Exception as e:
        logger.error("Beat %d failed: %s — cream fallback", beat_idx, e)
        if os.path.exists(out):
            os.remove(out)
        _make_cream_clip(dur, out)

    return out


# ---------------------------------------------------------------------------
# Step 3: Concatenate beat clips
# ---------------------------------------------------------------------------

def _concat_clips(comp_clips):
    out       = os.path.join(COMP_DIR, "concat.mp4")
    list_path = os.path.join(COMP_DIR, "concat.txt")

    with open(list_path, "w", encoding="utf-8") as f:
        for p in comp_clips:
            f.write("file '%s'\n" % p.replace("\\", "/"))

    _ffmpeg(
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        out,
        timeout=300,
    )
    return out


# ---------------------------------------------------------------------------
# Steps 4 & 5: Build FFmpeg text-overlay filter chains
# ---------------------------------------------------------------------------

def _build_watermark(font):
    """Brand watermark — dark text, low opacity, bottom corner."""
    tr, tg, tb = config.TEXT_COLOR_DARK
    color = "0x%02x%02x%02x@%.2f" % (tr, tg, tb, config.WATERMARK_OPACITY)
    return _dt(
        font, config.WATERMARK_FONT_SIZE, color,
        config.BRAND_NAME, "w-tw-20", "h-th-20",
    )


def _build_beat_captions(beats_timed, font, tempo):
    """Captions for illustration beats only — dark text at bottom."""
    filters  = []
    cap_size = 48
    line_h   = cap_size + 12
    ts       = 1.0 / tempo
    tr, tg, tb = config.TEXT_COLOR_DARK
    text_color = "0x%02x%02x%02x" % (tr, tg, tb)

    for beat in beats_timed:
        # text_card beats are full-screen text — no caption overlay needed
        if beat.get("beat_type") == "text_card":
            continue

        t0 = beat["start"] * ts
        t1 = beat["end"]   * ts
        caption_t1 = t1 - 0.08 * ts

        if caption_t1 - t0 < 0.15:
            continue

        lines   = _wrap_lines(beat["line"], max_chars=44)
        total_h = len(lines) * line_h
        y_anchor = "h*0.82-%d" % (total_h // 2)

        for li, line_text in enumerate(lines):
            y = "%s+%d" % (y_anchor, li * line_h)
            filters.append(_dt(
                font, cap_size, text_color, line_text,
                "(w-tw)/2", y,
                enable="%.3f,%.3f" % (t0, caption_t1),
                border=0,
                box=True,
                boxcolor="0xfaf6ef@0.80",
                boxborderw=14,
            ))

    return filters


# ---------------------------------------------------------------------------
# Steps 6 & 7: Mix audio and write final MP4
# ---------------------------------------------------------------------------

def _add_text_and_audio(concat_path, tts_result, beats_timed, out_path):
    font  = _find_font(config.PUNCH_FONT_FAMILY)
    tempo = getattr(config, "AUDIO_TEMPO", 1.0)

    caption_filters = _build_beat_captions(beats_timed, font, tempo)
    watermark       = _build_watermark(font)
    vf = ",".join(["setpts=PTS/%.4f" % tempo] + caption_filters + [watermark])

    audio_path = tts_result["audio_path"]
    music_path = config.MUSIC_PATH
    has_music  = os.path.exists(music_path)

    base_args = ["-i", concat_path, "-i", audio_path]
    if has_music:
        base_args += ["-i", music_path]

    if has_music:
        af = (
            "[1:a]atempo=%.4f,aformat=fltp:44100:stereo[vo];"
            "[2:a]volume=%.1fdB,afade=t=in:d=%.1f,afade=t=out:d=%.1f[music];"
            "[vo][music]amix=inputs=2:duration=first[audio]"
            % (tempo, config.MUSIC_GAIN_DB, config.MUSIC_FADE_IN, config.MUSIC_FADE_OUT)
        )
        audio_map = ["-filter_complex", af, "-map", "0:v", "-map", "[audio]"]
    else:
        logger.warning("Music file not found (%s) -- skipping", music_path)
        af = "[1:a]atempo=%.4f[audio]" % tempo
        audio_map = ["-filter_complex", af, "-map", "0:v", "-map", "[audio]"]

    _ffmpeg(
        *base_args,
        *audio_map,
        "-vf", vf,
        "-c:v", config.VIDEO_CODEC,
        "-preset", "medium",
        "-c:a", config.AUDIO_CODEC,
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        out_path,
        timeout=1800,
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def compose_video(
    script_path=None,
    beats_path=None,
    tts_path=None,
    manifest_path=None,
    out_path=None,
):
    script_path   = script_path   or os.path.join(config.TMP_DIR,    "script_01.json")
    beats_path    = beats_path    or os.path.join(config.TMP_DIR,    "beats_01.json")
    tts_path      = tts_path      or os.path.join(config.TMP_DIR,    "tts_timings_01.json")
    manifest_path = manifest_path or os.path.join(config.TMP_DIR,    "clips_manifest_01.json")
    out_path      = out_path      or os.path.join(config.OUTPUT_DIR, "video_01.mp4")

    with open(script_path,   encoding="utf-8") as f: script   = json.load(f)
    with open(beats_path,    encoding="utf-8") as f: beats    = json.load(f)["beats"]
    with open(tts_path,      encoding="utf-8") as f: tts      = json.load(f)
    with open(manifest_path, encoding="utf-8") as f: manifest = json.load(f)

    beat_images = manifest["beat_images"]  # list of path | null, one per beat

    if len(beat_images) != len(beats):
        raise ValueError(
            "Image/beat count mismatch: %d images vs %d beats"
            % (len(beat_images), len(beats))
        )

    logger.info("Compositor start | beats=%d", len(beats))

    beats_timed = _map_beat_timings(beats, tts["segments"], tts["words"])
    logger.info("Beat timing | first=%.2fs | last_end=%.2fs",
                beats_timed[0]["start"], beats_timed[-1]["end"])

    os.makedirs(COMP_DIR, exist_ok=True)
    comp_clips = []
    for i, (beat, image_path) in enumerate(zip(beats_timed, beat_images)):
        dur = max(beat["end"] - beat["start"], 0.5)
        comp_clips.append(_prepare_beat_clip(beat, image_path, dur, i))
        if (i + 1) % 10 == 0 or i + 1 == len(beats):
            logger.info("Clips prepared: %d/%d", i + 1, len(beats))

    concat_path = _concat_clips(comp_clips)
    logger.info("Concat -> %s", concat_path)

    _add_text_and_audio(concat_path, tts, beats_timed, out_path)

    size_mb = os.path.getsize(out_path) / 1_048_576
    logger.info("Done -> %s (%.1f MB)", out_path, size_mb)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = compose_video()
    print("\nVideo rendered -> %s" % result)
