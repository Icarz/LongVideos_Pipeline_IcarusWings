# Stage 5 - compositor: assemble final MP4 from beats, clips, TTS, and text overlays.
#
# Input  : tmp/script_01.json, tmp/beats_01.json, tmp/tts_timings_01.json,
#          tmp/clips_manifest_01.json
# Output : output/video_01.mp4
#
# Steps:
#   1. Cut and sequence background clips to beat timing from TTS
#   2. Apply Ken Burns (eased pan, section-aware) per clip
#   3. Darken footage with cinematic curves filter
#   4. Burn in keyword punches (bold white + dark box + stroke, lower-third)
#   5. Burn in section cards (full-screen, structural beats)
#   6. Mix music bed at -18 dB under voiceover
#   7. Add brand watermark (bottom corner, low opacity)
#   8. Write final MP4 via FFmpeg

import json
import logging
import math
import os
import random
import re
import subprocess
from collections import defaultdict

import config

logger = logging.getLogger(__name__)

COMP_DIR = os.path.join(config.TMP_DIR, "comp")

_WINDOWS_FONTS = {
    "Arial Bold": "C:/Windows/Fonts/arialbd.ttf",
    "Arial":      "C:/Windows/Fonts/arial.ttf",
}

# Curated card labels — shown full-screen at the first beat of each trigger role
_CARD_LABELS = {
    "turn":       "THE TURN",
    "re_hook":    "BUT WAIT",
    "lever":      "THE FIX",
    "close":      "THE TRUTH",
    "true_cause": "THE REAL CAUSE",
    "false_cause": "THE MYTH",
}

# Camera move per section role — controls pan direction and framing
_ROLE_CAMERA = {
    "hook":        "zoom_in",
    "false_cause": "pan",
    "turn":        "push_in",    # dramatic centered push — pairs with hard cut
    "true_cause":  "zoom_out",
    "re_hook":     "pan",
    "lever":       "zoom_in",
    "close":       "drift",      # slow centered drift for the emotional close
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
    raise RuntimeError("Font not found: %r. Set PUNCH_FONT_FAMILY to a .ttf path." % family)


def _dt_text(s):
    # Strip curly apostrophes; escape filter-separator characters
    s = re.sub(r"[''‚‛']", "", s)
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace(",", "\\,")
    return s


def _dt_path(p):
    return p.replace("\\", "/").replace("'", "\\'").replace(":", "\\:")


def _dt(font, size, color, text, x, y, enable=None, border=0, box=False, boxcolor=None, boxborderw=0):
    parts = [
        "drawtext=fontfile='%s'" % _dt_path(font),
        "fontsize=%d" % size,
        "fontcolor=%s" % color,
        "text='%s'" % _dt_text(text),
        "x=%s" % x,
        "y=%s" % y,
    ]
    if border > 0:
        parts += ["bordercolor=black", "borderw=%d" % border]
    if box:
        parts.append("box=1")
        parts.append("boxcolor=%s" % (boxcolor or "black@0.6"))
        if boxborderw > 0:
            parts.append("boxborderw=%d" % boxborderw)
    if enable:
        parts.append("enable='between(t,%s)'" % enable)
    return ":".join(parts)


def _wrap_lines(text, max_chars=44):
    """Split text into screen-width lines for caption display."""
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


def _make_black_clip(dur, out_path):
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    _ffmpeg(
        "-f", "lavfi",
        "-i", "color=c=black:s=%dx%d:r=%d" % (W, H, config.VIDEO_FPS),
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

        # Proportional distribution by word count
        raw_durs = [seg_dur * (wc / total_words) for wc in word_counts]

        # Clamp each beat to [BEAT_MIN_SECONDS, BEAT_MAX_SECONDS]
        min_s   = float(config.BEAT_MIN_SECONDS)
        max_s   = float(config.BEAT_MAX_SECONDS)
        clamped = [max(min_s, min(max_s, d)) for d in raw_durs]

        # Rescale so the segment total still matches the TTS window exactly
        total_clamped = sum(clamped)
        if total_clamped > 0:
            scale   = seg_dur / total_clamped
            clamped = [d * scale for d in clamped]

        t = seg_start
        for (orig_idx, beat), dur in zip(role_beats, clamped):
            timed[orig_idx] = dict(beat, start=round(t, 3), end=round(t + dur, 3))
            t += dur

    # Fill any beats whose role had no TTS segment
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
# Steps 2 & 3: Prepare individual beat clips (Ken Burns + cinematic dark + fades)
# ---------------------------------------------------------------------------

def _prepare_beat_clip(clip_path, beat_duration, beat_idx, role):
    os.makedirs(COMP_DIR, exist_ok=True)
    out = os.path.join(COMP_DIR, "beat_%03d.mp4" % beat_idx)
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        return out  # resume-safe

    dur = max(beat_duration, 0.5)

    try:
        _encode_beat_clip(clip_path, dur, beat_idx, role, out)
    except Exception as e:
        logger.error("Beat %d failed (%s): %s — black fallback", beat_idx, clip_path, e)
        if os.path.exists(out):
            os.remove(out)
        _make_black_clip(dur, out)

    return out


def _encode_beat_clip(clip_path, dur, beat_idx, role, out):
    W   = config.VIDEO_WIDTH
    H   = config.VIDEO_HEIGHT
    FPS = config.VIDEO_FPS

    src_dur = _probe_duration(clip_path)
    if src_dur < dur:
        loops   = math.ceil(dur / src_dur) + 1
        in_args = ["-stream_loop", str(loops), "-i", clip_path]
    else:
        in_args = ["-i", clip_path]

    # --- Ken Burns: scale oversized, then eased crop pan ---
    ow = int(W * config.BG_KENBURNS_ZOOM)
    oh = int(H * config.BG_KENBURNS_ZOOM)
    px = ow - W   # max horizontal travel
    py = oh - H   # max vertical travel

    cam = _ROLE_CAMERA.get(role, "zoom_in")

    # Seeded random pan direction (reproducible per beat index)
    rng  = random.Random(beat_idx * 1337 + 7)
    dirs = [(1, 1), (-1, 1), (1, -1), (-1, -1),
            (1, 0), (-1, 0), (0, 1), (0, -1)]
    dx, dy = rng.choice(dirs)

    # Ease-in-out using cosine: pos = max * (1 - cos(PI * t/dur)) / 2
    # Forward: 0 → px;  Reverse: px → 0
    pi_d = "%.6f" % (3.14159265 / max(dur, 0.01))

    if cam == "push_in":
        # Centered static crop — hard cut (no fade-in) signals the turn
        x_expr = "'trunc(%d/2)'" % px
        y_expr = "'trunc(%d/2)'" % py

    elif cam == "drift":
        # Slow horizontal drift around center for the emotional close
        amp    = max(px // 4, 1)
        x_expr = "'trunc(%d/2+%d*sin(%.6f*t))'" % (px, amp, 3.14159265 / max(dur, 0.01))
        y_expr = "'trunc(%d/2)'" % py

    else:
        # Eased pan in randomized direction
        if dx >= 0:
            x_expr = "'trunc(%d*(1-cos(%s*t))/2)'" % (px, pi_d)
        else:
            x_expr = "'trunc(%d*(1+cos(%s*t))/2)'" % (px, pi_d)

        if dy >= 0:
            y_expr = "'trunc(%d*(1-cos(%s*t))/2)'" % (py, pi_d)
        else:
            y_expr = "'trunc(%d*(1+cos(%s*t))/2)'" % (py, pi_d)

    # --- Cinematic darkening: curves filter (non-linear, preserves blacks) ---
    dark  = 1.0 - config.BG_OVERLAY_OPACITY
    mid   = dark * 0.60
    curve = "curves=all='0/0 0.5/%.3f 1/%.3f'" % (mid, dark)

    # --- Cool color grade: desaturate + push blue channel ---
    grade = (
        "hue=s=%.2f," % config.GRADE_SATURATION +
        "colorchannelmixer=rr=0.88:rb=0.04:gg=0.92:gb=0.03:br=0.02:bb=1.06"
    )

    # --- Film grain ---
    grain = ""
    if config.GRADE_GRAIN_STRENGTH > 0:
        grain = ",noise=c0s=%d:c0f=t+u" % config.GRADE_GRAIN_STRENGTH

    # Fade in/out baked per clip — turn gets no fade-in (hard cut)
    fade_dur = min(0.35, dur * 0.12)
    fade_out = "fade=t=out:st=%.3f:d=%.3f" % (max(0.0, dur - fade_dur), fade_dur)
    if role == config.BG_HARD_CUT_ROLE:
        fades = fade_out
    else:
        fade_in = "fade=t=in:st=0:d=%.3f" % fade_dur
        fades   = "%s,%s" % (fade_in, fade_out)

    vf = (
        "scale=%d:%d," % (ow, oh) +
        "crop=%d:%d:x=%s:y=%s," % (W, H, x_expr, y_expr) +
        "%s," % curve +
        "%s%s," % (grade, grain) +
        "fps=%d," % FPS +
        fades
    )

    _ffmpeg(
        *in_args,
        "-t", str(dur),
        "-vf", vf,
        "-an",
        "-c:v", config.VIDEO_CODEC,
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        out,
        timeout=180,
    )


# ---------------------------------------------------------------------------
# Concatenate beat clips — fades baked per clip, concat demuxer for speed
# ---------------------------------------------------------------------------

def _concat_clips(comp_clips, beats_timed):
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
# Steps 4, 5, 7: Build FFmpeg text-overlay filter chains
# ---------------------------------------------------------------------------

def _build_card_vf(beats_timed, script, font, tempo):
    """Section transition cards + keep-line proof card + watermark."""
    filters    = []
    cards_shown = set()
    card_bg    = "0x%02x%02x%02x" % tuple(config.CARD_BG_COLOR)
    ts         = 1.0 / tempo  # scale original timestamps into sped-up timeline

    for beat in beats_timed:
        t0   = beat["start"] * ts
        role = beat["section_role"]
        is_card_beat = (role in config.CARD_TRIGGER_ROLES and role not in cards_shown)

        if is_card_beat:
            cards_shown.add(role)
            tc0   = t0
            tc1   = tc0 + 2.5 * ts
            label = _CARD_LABELS.get(role, role.replace("_", " ").upper())
            filters.append(
                "drawbox=x=0:y=0:w=iw:h=ih"
                ":color=%s@%.2f:t=fill"
                ":enable='between(t,%.3f,%.3f)'"
                % (card_bg, config.CARD_BG_OPACITY, tc0, tc1)
            )
            filters.append(_dt(
                font, config.CARD_FONT_SIZE, "white", label,
                "(w-tw)/2", "(h-th)/2",
                enable="%.3f,%.3f" % (tc0, tc1),
                border=4,
            ))

    # Keep-line card: proof sentence in the turn section
    if config.CARD_KEEP_LINE:
        keep = script.get("keep_line", "").strip()
        if keep:
            for beat in beats_timed:
                if beat["section_role"] == "turn":
                    tk0   = (beat["start"] + 2.6) * ts
                    tk1   = tk0 + 4.0 * ts
                    short = re.sub(r"[''‚‛']", "", keep[:60])
                    if len(keep) > 60:
                        short += "..."
                    filters.append(
                        "drawbox=x=0:y=ih/3:w=iw:h=ih/3"
                        ":color=%s@%.2f:t=fill"
                        ":enable='between(t,%.3f,%.3f)'"
                        % (card_bg, config.CARD_BG_OPACITY, tk0, tk1)
                    )
                    filters.append(_dt(
                        font, 38, "white", short,
                        "(w-tw)/2", "(h-th)/2",
                        enable="%.3f,%.3f" % (tk0, tk1),
                        border=2,
                    ))
                    break

    # Watermark always visible
    filters.append(_dt(
        font, config.WATERMARK_FONT_SIZE,
        "white@%.2f" % config.WATERMARK_OPACITY,
        config.BRAND_NAME,
        "w-tw-20", "h-th-20",
    ))

    return filters


def _build_beat_captions(beats_timed, font, tempo):
    """Full beat-line captions — text changes exactly when the video cuts."""
    filters    = []
    cap_size   = 48
    line_h     = cap_size + 12
    ts         = 1.0 / tempo
    cards_seen = set()

    for beat in beats_timed:
        t0   = beat["start"] * ts
        t1   = beat["end"]   * ts
        role = beat["section_role"]

        is_card_beat = (role in config.CARD_TRIGGER_ROLES and role not in cards_seen)
        if is_card_beat:
            cards_seen.add(role)

        # Delay caption start until the card overlay clears
        caption_t0 = (beat["start"] + 2.6) * ts if is_card_beat else t0
        caption_t1 = t1 - 0.08 * ts  # tiny gap before next cut

        if caption_t1 - caption_t0 < 0.15:
            continue

        lines    = _wrap_lines(beat["line"], max_chars=44)
        total_h  = len(lines) * line_h
        y_anchor = "h*0.82-%d" % (total_h // 2)

        for li, line_text in enumerate(lines):
            y = "%s+%d" % (y_anchor, li * line_h)
            filters.append(_dt(
                font, cap_size, "white", line_text,
                "(w-tw)/2", y,
                enable="%.3f,%.3f" % (caption_t0, caption_t1),
                border=2,
                box=True,
                boxcolor="0x080a0e@0.85",
                boxborderw=14,
            ))

    return filters


# ---------------------------------------------------------------------------
# Steps 6 & 8: Mix audio and write final MP4
# ---------------------------------------------------------------------------

def _add_text_and_audio(concat_path, tts_result, script, beats_timed, out_path):
    font  = _find_font(config.PUNCH_FONT_FAMILY)
    tempo = getattr(config, "AUDIO_TEMPO", 1.0)

    card_filters    = _build_card_vf(beats_timed, script, font, tempo)
    caption_filters = _build_beat_captions(beats_timed, font, tempo)
    vf = ",".join(["setpts=PTS/%.4f" % tempo] + card_filters + caption_filters)

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

    beat_clips = manifest["beat_clips"]

    if len(beat_clips) != len(beats):
        raise ValueError(
            "Clip/beat count mismatch: %d clips vs %d beats" % (len(beat_clips), len(beats))
        )

    logger.info("Compositor start | beats=%d", len(beats))

    beats_timed = _map_beat_timings(beats, tts["segments"], tts["words"])
    logger.info("Beat timing | first=%.2fs | last_end=%.2fs",
                beats_timed[0]["start"], beats_timed[-1]["end"])

    os.makedirs(COMP_DIR, exist_ok=True)
    comp_clips = []
    for i, (beat, clip_path) in enumerate(zip(beats_timed, beat_clips)):
        dur  = max(beat["end"] - beat["start"], 0.5)
        role = beat["section_role"]
        comp_clips.append(_prepare_beat_clip(clip_path, dur, i, role))
        if (i + 1) % 10 == 0 or i + 1 == len(beats):
            logger.info("Clips prepared: %d/%d", i + 1, len(beats))

    concat_path = _concat_clips(comp_clips, beats_timed)
    logger.info("Concat -> %s", concat_path)

    _add_text_and_audio(concat_path, tts, script, beats_timed, out_path)

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
