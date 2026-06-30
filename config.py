"""Single source of truth for the long-form narrated-video pipeline.

Every module imports tuning constants from here rather than hardcoding. Change
behavior here, not in module bodies. Windows-first, Python 3.12.

Style: Hugh Knows — minimalist stick-figure educational tips format.
"""

import os

# --- Paths -----------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
LOG_FILE = os.path.join(LOGS_DIR, "pipeline.log")

for _d in (TMP_DIR, LOGS_DIR, OUTPUT_DIR):
    os.makedirs(_d, exist_ok=True)

# Hand-authored gold-standard exemplar — update when style changes.
EXEMPLAR_SCRIPT_PATH = os.path.join(SCRIPTS_DIR, "script-01-2pm-focus.txt")

# --- Video frame -----------------------------------------------------------
VIDEO_WIDTH  = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS    = 30
VIDEO_CODEC  = "libx264"
AUDIO_CODEC  = "aac"
VIDEO_BG_COLOR = (250, 246, 239)   # warm cream #FAF6EF

# --- Color theme -----------------------------------------------------------
TEXT_COLOR_DARK  = (26, 26, 26)      # near-black — all primary text
TEXT_COLOR_LIGHT = (240, 242, 245)   # near-white — watermark only

# --- Script (stage 1) ------------------------------------------------------
SCRIPT_MODEL           = "claude-opus-4-8"
SCRIPT_MAX_TOKENS      = 8000
SCRIPT_RETRY_ATTEMPTS  = 3
TARGET_WORDS_MIN       = 400
TARGET_WORDS_MAX       = 550
NARRATION_WPM          = 140   # punchy tips delivery; validate on first TTS run

SCRIPT_SECTION_ROLES = [
    "intro",
    "tip_1", "tip_2", "tip_3",
    "tip_4", "tip_5", "tip_6",
    "outro",
]

# --- Beats (stage 2) -------------------------------------------------------
BEAT_MODEL           = "claude-opus-4-8"
BEAT_MAX_TOKENS      = 12000
BEAT_RETRY_ATTEMPTS  = 3
BEAT_MIN_SECONDS     = 2
BEAT_MAX_SECONDS     = 4
TARGET_BEATS_MIN     = 40
TARGET_BEATS_MAX     = 65

BEAT_TYPES = ["illustration", "text_card"]

# --- TTS (stage 3) ---------------------------------------------------------
TTS_ENGINE   = "edge-tts"
TTS_VOICE    = "en-US-AndrewMultilingualNeural"
TTS_RATE     = "-10%"
TTS_PITCH    = "-5Hz"
ALIGN_MODEL  = "whisper-large-v3"

# --- Image generation (stage 4) --------------------------------------------
IMAGE_GEN_MODEL     = "gpt-image-1"
IMAGE_GEN_SIZE      = "1536x1024"   # landscape — valid gpt-image-1 size
IMAGE_GEN_QUALITY   = "standard"
IMAGE_GEN_STYLE     = (
    "minimalist black line-art stick figure illustration, "
    "warm cream background, simple educational explainer style, "
    "no color, no shading, clean and flat, single centered scene"
)
IMAGE_GEN_CACHE_DIR = os.path.join(TMP_DIR, "image_cache")

# --- Playback tempo (stage 5) ---------------------------------------------
AUDIO_TEMPO = 1.20   # speed up video + audio by 20%

# --- Music & motion (stage 5) ---------------------------------------------
MUSIC_PATH          = os.path.join(ASSETS_DIR, "music", "ambient_bed.mp3")
MUSIC_GAIN_DB       = -18
MUSIC_SWELL_GAIN_DB = -14
MUSIC_FADE_IN       = 1.5
MUSIC_FADE_OUT      = 2.0
BG_CROSSFADE        = 0.8
BG_HARD_CUT_ROLE    = "tip_1"   # first tip opens with a hard cut

# --- Kinetic text: keyword punches (stage 5) ------------------------------
PUNCH_FONT_SIZE   = 64
PUNCH_FONT_FAMILY = "Arial Bold"
PUNCH_COLOR       = TEXT_COLOR_DARK
PUNCH_FADE_IN     = 0.0
PUNCH_FADE_OUT    = 0.35
PUNCH_HOLD_MIN    = 1.5
PUNCH_HOLD_MAX    = 2.0
PUNCH_POSITION    = "lower_third"

# --- Kinetic text: section cards (stage 5) --------------------------------
CARD_FONT_SIZE     = 88
CARD_FONT_FAMILY   = "Georgia"        # serif — matches tip title style
CARD_COLOR         = TEXT_COLOR_DARK  # dark text on light background
CARD_FADE_IN       = 0.8
CARD_FADE_OUT      = 0.8
CARD_BG_COLOR      = VIDEO_BG_COLOR   # card bg matches overall bg (cream)
CARD_BG_OPACITY    = 0.95
CARD_MAX_PER_VIDEO = 8
CARD_TRIGGER_ROLES = [
    "tip_1", "tip_2", "tip_3",
    "tip_4", "tip_5", "tip_6",
]
CARD_KEEP_LINE = False   # no proof-sentence card in tips format

# --- Brand watermark -------------------------------------------------------
BRAND_NAME          = "Icarus Wings"
WATERMARK_FONT_SIZE = 28
WATERMARK_OPACITY   = 0.25   # subtle on light background

# --- Edge-brightness verification (stage 5) -------------------------------
EDGE_SCAN_STRIP_PX  = 15
EDGE_SCAN_INTERVAL_S = 2.0
EDGE_DARK_THRESHOLD  = 8
EDGE_BRIGHT_THRESHOLD = 15

# --- Publish (stage 6) ----------------------------------------------------
YOUTUBE_CATEGORY_ID     = "22"   # People & Blogs
YOUTUBE_DEFAULT_PRIVACY = "private"
